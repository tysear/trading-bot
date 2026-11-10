import asyncio
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests
from flask import Flask, Response, jsonify, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from smc_indicators import get_smc_analysis
except ImportError:
    get_smc_analysis = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("quantbot")

# ==========================================
# الإعدادات
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_IDS = {x.strip() for x in os.environ.get("CHAT_ID", "").split(",") if x.strip()}
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

BINANCE_SPOT_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
BINANCE_FUTURES = "https://fapi.binance.com"

BARS_PER_DAY = {"15m": 96, "1h": 24, "4h": 6, "1d": 1}
CACHE_TTL = {"15m": 120, "1h": 300, "4h": 600, "1d": 1800}

CRYPTO_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "XRP": "XRP Ripple", "BNB": "BNB Binance",
    "SOL": "Solana", "ADA": "Cardano", "DOGE": "Dogecoin",
}
NEWS_QUERIES = {
    "GC=F": "gold price", "SI=F": "silver price", "CL=F": "crude oil price",
    "EURUSD=X": "EUR USD euro dollar", "GBPUSD=X": "GBP USD pound dollar",
    "USDJPY=X": "USD JPY yen dollar", "^GSPC": "S&P 500", "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones", "AAPL": "Apple stock", "TSLA": "Tesla stock",
    "MSFT": "Microsoft stock", "NVDA": "Nvidia stock", "GOOGL": "Google Alphabet stock",
}

ACTIONS = ["شراء قوي", "شراء حذر", "انتظار", "بيع حذر", "بيع قوي"]
FNG_AR = {"extreme fear": "خوف شديد", "fear": "خوف", "neutral": "محايد", "greed": "طمع", "extreme greed": "طمع شديد"}

NOISE_RE = re.compile(
    r"prediction market|price (range|prediction)|to buy for 20|worth more|millionaire|get rich|"
    r"predicts|unbelievable|\$\d+(\.\d+)? million|\d+ times|investing move|wash-sale|"
    r"should (americans|you|investors)|i.m making|i.d buy|"
    r"crypto(currency)? (atms?|machines|kiosks?)|bitcoin atms?|"
    r"best (crypto|altcoin)|top \d+ |\b\d+x\b|could (soar|explode|skyrocket)|sponsored|"
    r"\bby 20(2[7-9]|[3-9]\d)\b|\bfor 20(2[7-9]|[3-9]\d)\b",
    re.IGNORECASE,
)
HIGH_IMPACT_RE = re.compile(
    r"\betf\b|\bsec\b|\bfed\b|federal reserve|rate (cut|hike)|interest rate|inflation|\bcpi\b|fomc|"
    r"tariff|hack|exploit|lawsuit|\bban\b|outflow|inflow|liquidat|approv|delist|bankrupt|"
    r"exchange|whale|treasury|stablecoin|discount",
    re.IGNORECASE,
)

# ==========================================
# أدوات عامة والتخزين المؤقت
# ==========================================
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key, ttl):
    with _cache_lock:
        item = _cache.get(key)
    if item and time.time() - item[1] < ttl:
        return item[0]
    return None

def cache_set(key, value):
    with _cache_lock:
        _cache[key] = (value, time.time())
    return value

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantBot/2.4)"}

def http_get(url, params=None, timeout=12):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
        logger.warning("HTTP %s from %s", r.status_code, url)
    except requests.RequestException as e:
        logger.warning("Request failed %s: %s", url, e)
    return None

def is_crypto(symbol):
    return symbol.endswith("-USD")

def normalize_symbol(raw):
    s = raw.strip().upper()
    if s in CRYPTO_NAMES:
        s += "-USD"
    return s

def fmt_price(p):
    if p is None:
        return "-"
    p = float(p)
    decimals = 2 if abs(p) >= 100 else 4 if abs(p) >= 1 else 6
    return f"{p:,.{decimals}f}"

# ==========================================
# جلب بيانات الأسعار والمؤشرات (مع التفاضل والتكامل)
# ==========================================
def fetch_binance(symbol, interval):
    pair = symbol[:-4] + "USDT"
    for host in BINANCE_SPOT_HOSTS:
        r = http_get(f"{host}/api/v3/klines", {"symbol": pair, "interval": interval, "limit": 300})
        if r is None:
            continue
        try:
            rows = r.json()
        except ValueError:
            continue
        if not isinstance(rows, list) or len(rows) < 60:
            continue
        df = pd.DataFrame(rows).iloc[:, :6]
        df.columns = ["time", "open", "high", "low", "close", "volume"]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df.index = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df[["open", "high", "low", "close", "volume"]]
    return None

def fetch_yfinance(symbol, interval):
    if yf is None:
        return None
    try:
        if interval == "4h":
            raw = yf.Ticker(symbol).history(period="3mo", interval="1h")
        else:
            period = {"15m": "1mo", "1h": "3mo", "1d": "1y"}[interval]
            raw = yf.Ticker(symbol).history(period=period, interval=interval)
        if raw is None or raw.empty:
            return None
        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        if interval == "4h":
            df = df.resample("4h").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            )
        df = df.dropna(subset=["close"])
        return df if len(df) >= 60 else None
    except Exception as e:
        logger.warning("yfinance error for %s: %s", symbol, e)
        return None

def fetch_ohlcv(symbol, interval):
    key = f"ohlcv_{symbol}_{interval}"
    cached = cache_get(key, CACHE_TTL[interval])
    if cached is not None:
        return cached
    df = fetch_binance(symbol, interval) if is_crypto(symbol) else None
    if df is None:
        df = fetch_yfinance(symbol, interval)
    return cache_set(key, df) if df is not None else None

def rsi_wilder(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain / loss
    return (100 - 100 / (1 + rs)).fillna(50)

def atr_wilder(high, low, close, period=14):
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

def calculate_cvar_risk(prices, confidence_level=0.95):
    """
    التكامل الاحتمالي (CVaR / Expected Shortfall) لحساب المخاطر في أسوأ حالات السوق.
    """
    if len(prices) < 20:
        return 0.0
    arr = np.array(prices, dtype=float)
    returns = np.diff(np.log(arr))
    var = np.percentile(returns, (1 - confidence_level) * 100)
    cvar = returns[returns <= var].mean()
    return float(cvar) if not np.isnan(cvar) else 0.0

def compute_indicators(df, interval):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    bpd = BARS_PER_DAY[interval]

    price = float(c.iloc[-1])
    atr = float(atr_wilder(h, l, c).iloc[-1])
    support, resistance = float(df["low"].iloc[-51:-1].min()), float(df["high"].iloc[-51:-1].max())
    
    # التفاضل الأول والثاني (السرعة والتسارع اللحظي)
    velocity = float(np.diff(c.iloc[-5:].values)[-1]) if len(c) >= 5 else 0.0
    acceleration = float(np.diff(np.diff(c.iloc[-5:].values))[-1]) if len(c) >= 6 else 0.0
    
    # التكامل الاحتمالي للمخاطر (CVaR)
    cvar_val = calculate_cvar_risk(c.tolist())

    return {
        "price": price,
        "rsi": float(rsi_wilder(c).iloc[-1]),
        "atr": atr,
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "macd_hist": float(hist.iloc[-1]),
        "velocity": velocity,
        "acceleration": acceleration,
        "cvar_risk": cvar_val,
        "support_50": support,
        "resistance_50": resistance,
        "dist_to_support_atr": (price - support) / atr if atr > 0 else None,
        "dist_to_resistance_atr": (resistance - price) / atr if atr > 0 else None,
    }

def quant_score(ind):
    score, reasons = 0, []
    p, e20, e50 = ind["price"], ind["ema20"], ind["ema50"]
    if p > e50 and e20 > e50:
        score += 2; reasons.append("اتجاه صاعد")
    elif p < e50 and e20 < e50:
        score -= 2; reasons.append("اتجاه هابط")
    else:
        score += 1 if p > e50 else -1

    if ind["macd_hist"] > 0: score += 1
    elif ind["macd_hist"] < 0: score -= 1

    # تعزيز القرار بناءً على التفاضل (السرعة والتسارع)
    if ind["velocity"] > 0 and ind["acceleration"] > 0:
        score += 1; reasons.append("زخم تفاضلي صاعد متسارع")
    elif ind["velocity"] < 0 and ind["acceleration"] < 0:
        score -= 1; reasons.append("زخم تفاضلي هابط متسارع")

    return score, reasons

def score_to_action(score):
    if score >= 3: return "شراء قوي"
    if score == 2: return "شراء حذر"
    if score <= -3: return "بيع قوي"
    if score == -2: return "بيع حذر"
    return "انتظار"

def direction(action):
    return 1 if "شراء" in action else -1 if "بيع" in action else 0

def level_guard(action, ind):
    d, atr = direction(action), ind.get("atr") or 0
    if d == 0 or atr <= 0: return action, None
    if d < 0 and 0 <= (ind["price"] - ind["support_50"]) / atr < 1.0:
        return "انتظار", "السعر قريب من الدعم؛ يفضل الانتظار"
    elif d > 0 and 0 <= (ind["resistance_50"] - ind["price"]) / atr < 1.0:
        return "انتظار", "السعر قريب من المقاومة؛ يفضل الانتظار"
    return action, None

def trade_levels(action, price, atr):
    d = direction(action)
    if d == 0: return None
    return {
        "sl": price - d * 1.5 * atr,
        "tp1": price + d * 1.5 * atr,
        "tp2": price + d * 3.0 * atr,
        "tp3": price + d * 4.5 * atr,
    }

# ==========================================
# الأخبار والمشاعر والذكاء الاصطناعي
# ==========================================
def get_fear_greed():
    cached = cache_get("fng", 3600)
    if cached: return cached
    r = http_get("https://api.alternative.me/fng/")
    try:
        item = r.json()["data"][0]
        label = item["value_classification"]
        return cache_set("fng", {"value": int(item["value"]), "label": label, "label_ar": FNG_AR.get(label.lower(), label)})
    except Exception:
        return None

def get_derivatives(symbol):
    pair = symbol[:-4] + "USDT"
    key = f"deriv_{pair}"
    cached = cache_get(key, 300)
    if cached: return cached
    out = {}
    r = http_get(f"{BINANCE_FUTURES}/fapi/v1/premiumIndex", {"symbol": pair})
    try: out["funding_rate_pct"] = float(r.json()["lastFundingRate"]) * 100
    except Exception: pass
    return cache_set(key, out) if out else None

def get_smc(df):
    if get_smc_analysis is None: return None
    try:
        res = get_smc_analysis(df["close"].tolist(), df["high"].tolist(), df["low"].tolist(), df["volume"].tolist())
        return {"score": res.get("score", 0), "zone": res.get("premium_discount", {}).get("zone")}
    except Exception:
        return None

def news_google(query, limit):
    r = http_get("https://news.google.com/rss/search", {"q": f"{query} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if r is None: return []
    try: root = ET.fromstring(r.content)
    except ET.ParseError: return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if title: items.append({"title": title, "source": (it.findtext("source") or "Google News").strip()})
        if len(items) >= limit: break
    return items

def get_news(symbol):
    key = f"news_{symbol}"
    cached = cache_get(key, 600)
    if cached is not None: return cached
    asset = news_google(f"{symbol} crypto" if is_crypto(symbol) else f"{symbol} stock", 6)
    macro = news_google("Federal Reserve OR inflation", 3)
    result = {"asset": asset, "macro": macro}
    return cache_set(key, result)

AI_STATUS = {"error": ""}
SYSTEM_PROMPT = "You are a senior markets analyst. Reply with ONLY one JSON object (no markdown) with keys: action, confidence, news_sentiment, news_summary, reasoning, risks, invalidation."

def ask_ai(payload):
    if not OPENROUTER_API_KEY:
        AI_STATUS["error"] = "مفتاح OpenRouter غير مضبوط"
        return None
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Snapshot:\n" + json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    try:
        res = requests.post(OPENROUTER_API_URL, json=body, headers=headers, timeout=60)
        if res.status_code != 200: return None
        content = res.json()["choices"][0]["message"]["content"]
        text = re.sub(r"```(?:json)?", "", content).strip()
        return json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as e:
        AI_STATUS["error"] = str(e)
        return None

def analyze(symbol, interval="4h"):
    try:
        df = fetch_ohlcv(symbol, interval)
        if df is None: return None
        ind = compute_indicators(df, interval)
        qscore, qreasons = quant_score(ind)
        qaction = score_to_action(qscore)

        payload = {
            "symbol": symbol, "timeframe": interval,
            "technical": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in ind.items()},
            "quant": {"score": qscore, "action": qaction, "reasons": qreasons},
            "derivatives": get_derivatives(symbol) if is_crypto(symbol) else None,
            "fear_greed_index": get_fear_greed() if is_crypto(symbol) else None,
            "smc": get_smc(df)
        }
        ai = ask_ai(payload)

        final = ai.get("action", qaction) if ai else qaction
        final, guard_note = level_guard(final, ind)

        return {
            "symbol": symbol, "interval": interval, "price": ind["price"],
            "action": final, "quant_score": qscore, "indicators": ind,
            "levels": trade_levels(final, ind["price"], ind["atr"]),
            "note": guard_note or (ai.get("reasoning") if ai else "تحليل كمي وتفاضلي مباشر")
        }
    except Exception:
        logger.exception("Analysis failed for %s", symbol)
        return None

# ==========================================
# تشغيل خادم Flask وسيرفر تليجرام
# ==========================================
app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"status": "running", "bot": "Quant Trading Bot v2.4"})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/dashboard")
def dashboard():
    return """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>Dashboard</title></head><body style="background:#111;color:#fff;text-align:center;padding:50px;"><h1>لوحة تحكم بوت التداول الكمي</h1><p>النظام يعمل بكفاءة تفاضلية عالية.</p></body></html>"""

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك في بوت التداول الكمي المتطور!\nاستخدم `/smart <رمز>` للتحليل.", parse_mode="Markdown")

async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الاستخدام الصحيح: `/smart BTC-USD 4h`", parse_mode="Markdown")
        return
    symbol = normalize_symbol(context.args[0])
    interval = context.args[1].lower() if len(context.args) > 1 else "4h"
    
    await update.message.reply_text(f"جاري تطبيق التفاضل والتكامل وتحليل {symbol}...")
    res = analyze(symbol, interval)
    if not res:
        await update.message.reply_text("فشل في جلب البيانات أو إجراء التحليل.")
        return
    
    diff = res['indicators']
    msg = (
        f"📊 **تحليل {res['symbol']} ({res['interval'].upper()})**\n"
        f"السعر الحالي: `{fmt_price(res['price'])}`\n"
        f"التوصية: **{res['action']}** (النقاط: {res['quant_score']})\n\n"
        f"📐 **التحليل الرياضي المتقدم:**\n"
        f"• السرعة اللحظية (التفاضل 1): `{diff['velocity']:+.4f}`\n"
        f"• التسارع (التفاضل 2): `{diff['acceleration']:+.4f}`\n"
        f"• معامل مخاطر التكامل (CVaR): `{diff['cvar_risk']:.4f}`\n\n"
        f"🎯 **مستويات التداول:**\n"
        f"SL: `{fmt_price(res['levels']['sl'])}`\n"
        f"TP1: `{fmt_price(res['levels']['tp1'])}`\n"
        f"TP2: `{fmt_price(res['levels']['tp2'])}`\n\n"
        f"📝 ملاحظة: {res['note']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is missing!")
        return
    
    threading.Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("smart", smart_command))
    
    logger.info("Bot is polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
