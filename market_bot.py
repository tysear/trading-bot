import asyncio
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import math
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests
from scipy.stats import norm
from flask import Flask, Response, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

BINANCE_SPOT_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
BINANCE_FUTURES = "https://fapi.binance.com"

BARS_PER_DAY = {"15m": 96, "1h": 24, "4h": 6, "1d": 1}
CACHE_TTL = {"15m": 120, "1h": 300, "4h": 600, "1d": 1800}

CRYPTO_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "XRP": "XRP Ripple", "BNB": "BNB Binance",
    "SOL": "Solana", "ADA": "Cardano", "DOGE": "Dogecoin",
}
FNG_AR = {"extreme fear": "خوف شديد", "fear": "خوف", "neutral": "محايد", "greed": "طمع", "extreme greed": "طمع شديد"}

# ==========================================
# نموذج بلاك-شولز المتقدم (Black-Scholes Model)
# ==========================================
def black_scholes_pricing(S, K, T, r, sigma, option_type="call"):
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    if option_type == "call":
        price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        delta = norm.cdf(d1)
    else:
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = norm.cdf(d1) - 1.0
        
    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * norm.pdf(d1) * math.sqrt(T) / 100.0
    theta = (- (S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365.0
    
    return float(price), float(delta), float(gamma), float(theta), float(vega)

# ==========================================
# أدوات عامة والأخبار
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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantBot/3.5)"}

def http_get(url, params=None, timeout=12):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
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

def news_google(query, limit):
    r = http_get("https://news.google.com/rss/search", {"q": f"{query} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if r is None: return []
    try: root = ET.fromstring(r.content)
    except ET.ParseError: return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if title: items.append({"title": title, "link": link, "source": (it.findtext("source") or "Google News").strip()})
        if len(items) >= limit: break
    return items

def get_news(symbol):
    key = f"news_{symbol}"
    cached = cache_get(key, 600)
    if cached is not None: return cached
    asset = news_google(f"{symbol} crypto" if is_crypto(symbol) else f"{symbol} stock", 4)
    macro = news_google("Federal Reserve OR inflation OR CPI", 3)
    result = {"asset": asset, "macro": macro}
    return cache_set(key, result)

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

# ==========================================
# جلب البيانات والمؤشرات (التفاضل والتكامل)
# ==========================================
def fetch_binance(symbol, interval):
    pair = symbol[:-4] + "USDT"
    for host in BINANCE_SPOT_HOSTS:
        r = http_get(f"{host}/api/v3/klines", {"symbol": pair, "interval": interval, "limit": 300})
        if r is None: continue
        try:
            rows = r.json()
        except ValueError:
            continue
        if not isinstance(rows, list) or len(rows) < 60: continue
        df = pd.DataFrame(rows).iloc[:, :6]
        df.columns = ["time", "open", "high", "low", "close", "volume"]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df.index = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df[["open", "high", "low", "close", "volume"]]
    return None

def fetch_yfinance(symbol, interval):
    if yf is None: return None
    try:
        if interval == "4h":
            raw = yf.Ticker(symbol).history(period="3mo", interval="1h")
        else:
            period = {"15m": "1mo", "1h": "3mo", "1d": "1y"}[interval]
            raw = yf.Ticker(symbol).history(period=period, interval=interval)
        if raw is None or raw.empty: return None
        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        if interval == "4h":
            df = df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        df = df.dropna(subset=["close"])
        return df if len(df) >= 60 else None
    except Exception as e:
        logger.warning("yfinance error: %s", e)
        return None

def fetch_ohlcv(symbol, interval):
    key = f"ohlcv_{symbol}_{interval}"
    cached = cache_get(key, CACHE_TTL[interval])
    if cached is not None: return cached
    df = fetch_binance(symbol, interval) if is_crypto(symbol) else None
    if df is None: df = fetch_yfinance(symbol, interval)
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
    if len(prices) < 20: return 0.0
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

    price = float(c.iloc[-1])
    atr = float(atr_wilder(h, l, c).iloc[-1])
    support, resistance = float(df["low"].iloc[-51:-1].min()), float(df["high"].iloc[-51:-1].max())
    
    velocity = float(np.diff(c.iloc[-5:].values)[-1]) if len(c) >= 5 else 0.0
    acceleration = float(np.diff(np.diff(c.iloc[-5:].values))[-1]) if len(c) >= 6 else 0.0
    cvar_val = calculate_cvar_risk(c.tolist())

    returns = np.diff(np.log(c.values[-30:]))
    historical_vol = float(np.std(returns) * math.sqrt(365)) if len(returns) > 10 else 0.60

    bs_call, bs_delta, bs_gamma, bs_theta, bs_vega = black_scholes_pricing(
        S=price, K=price, T=30/365.0, r=0.05, sigma=max(historical_vol, 0.1), option_type="call"
    )

    return {
        "price": price, "rsi": float(rsi_wilder(c).iloc[-1]), "atr": atr,
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "macd_hist": float(hist.iloc[-1]),
        "velocity": velocity, "acceleration": acceleration, "cvar_risk": cvar_val,
        "support_50": support, "resistance_50": resistance,
        "bs_call": bs_call, "bs_delta": bs_delta, "bs_gamma": bs_gamma, "bs_theta": bs_theta, "bs_vega": bs_vega, "iv": historical_vol * 100
    }

def quant_score(ind):
    score, reasons = 0, []
    p, e20, e50 = ind["price"], ind["ema20"], ind["ema50"]
    if p > e50 and e20 > e50: score += 2; reasons.append("اتجاه صاعد")
    elif p < e50 and e20 < e50: score -= 2; reasons.append("اتجاه هابط")
    else: score += 1 if p > e50 else -1

    if ind["macd_hist"] > 0: score += 1; reasons.append("زخم إيجابي")
    elif ind["macd_hist"] < 0: score -= 1; reasons.append("زخم سلبي")

    if ind["velocity"] > 0 and ind["acceleration"] > 0: score += 1
    elif ind["velocity"] < 0 and ind["acceleration"] < 0: score -= 1

    return score, reasons

def score_to_action(score):
    if score >= 3: return "شراء قوي"
    if score == 2: return "شراء حذر"
    if score <= -3: return "بيع قوي"
    if score == -2: return "بيع حذر"
    return "انتظار"

def trade_levels(action, price, atr):
    d = 1 if "شراء" in action else -1 if "بيع" in action else 0
    if d == 0: return None
    return {
        "sl": price - d * 1.5 * atr,
        "tp1": price + d * 1.5 * atr,
        "tp2": price + d * 3.0 * atr,
        "tp3": price + d * 4.5 * atr,
    }

def ask_ai(payload):
    if not OPENROUTER_API_KEY:
        return None
    
    system_prompt = (
        "أنت محلل مالي محترف وخبير تداول كمي. قم بتقديم تحليل شامل باللغة العربية.\n"
        "يجب أن يتضمن التحليل:\n"
        "1. قراءة للتحليل الفني والرياضي (بلاك-شولز، السرعة، والتسارع).\n"
        "2. تحليل الأخبار الحية المرفقة (بما فيها أخبار التضخم و CPI والفيدرالي) وتأثيرها على معنويات السوق.\n"
        "3. خلاصة التوصية والقرار بفقرات واضحة وبدون قطع."
    )
    
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"حلل هذا الأصل بناءً على المؤشرات والأخبار الحية التالية: {json.dumps(payload, ensure_ascii=False)}"},
        ],
        "temperature": 0.2,
        "max_tokens": 750,
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    try:
        res = requests.post(OPENROUTER_API_URL, json=body, headers=headers, timeout=30)
        if res.status_code == 200:
            content = res.json()["choices"][0]["message"]["content"]
            return content.replace("###", "").replace("**", "").strip()
    except Exception as e:
        logger.error(f"AI Error: {e}")
    return None

def analyze(symbol, interval="4h"):
    try:
        df = fetch_ohlcv(symbol, interval)
        if df is None: return None
        ind = compute_indicators(df, interval)
        qscore, qreasons = quant_score(ind)
        qaction = score_to_action(qscore)
        news = get_news(symbol)
        fng = get_fear_greed() if is_crypto(symbol) else None

        news_titles = [item["title"] for item in news.get("asset", [])] if news else []
        macro_titles = [item["title"] for item in news.get("macro", [])] if news else []

        payload = {
            "symbol": symbol, "interval": interval,
            "technical": ind, "quant_action": qaction, "score": qscore,
            "fear_and_greed": fng, "latest_news_asset": news_titles, "latest_news_macro": macro_titles
        }
        ai_comment = ask_ai(payload)

        return {
            "symbol": symbol, "interval": interval, "price": ind["price"],
            "action": qaction, "quant_score": qscore, "indicators": ind,
            "levels": trade_levels(qaction, ind["price"], ind["atr"]),
            "ai_analysis": ai_comment, "news": news, "fear_greed": fng
        }
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return None

# ==========================================
# لوحة تحكم ويب شاملة مع الأخبار ونموذج بلاك-شولز
# ==========================================
app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"status": "running", "bot": "Quant Trading Bot v3.5 Proactive AI & Alerts"})

@app.route("/dashboard")
def dashboard():
    return """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><title>لوحة التحكم الشاملة للتداول الكمي</title>
<style>
  body{font-family:'Segoe UI', Tahoma, sans-serif;background:#0d1117;color:#e6edf3;padding:15px;margin:0;}
  .container{max-width:550px;margin:0 auto;background:#161b22;padding:20px;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,0.6);}
  .top-bar{display:flex;gap:10px;margin-bottom:15px;}
  select, button{background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:10px;border-radius:8px;font-size:14px;cursor:pointer;}
  select{flex:1;}
  button.analyze-btn{background:#238636;color:#fff;font-weight:bold;flex:2;border:none;}
  button.analyze-btn:hover{background:#2ea043;}
  
  .action-banner{padding:18px;border-radius:10px;text-align:center;font-size:22px;font-weight:bold;margin-bottom:15px;}
  .buy{background:#238636;color:#fff;}
  .sell{background:#da3633;color:#fff;}
  .wait{background:#9e6a03;color:#fff;}

  .card{background:#21262d;border:1px solid #30363d;border-radius:10px;padding:15px;margin-bottom:15px;}
  .card-title{color:#58a6ff;font-size:15px;font-weight:bold;margin-bottom:10px;border-bottom:1px solid #30363d;padding-bottom:5px;}
  
  .row-item{display:flex;justify-content:space-between;padding:6px 0;font-size:14px;border-bottom:1px solid rgba(255,255,255,0.05);}
  .row-item span:last-child{font-weight:bold;}

  .news-item{margin-bottom:12px;font-size:13px;border-bottom:1px solid #30363d;padding-bottom:8px;}
  .news-item a{color:#58a6ff;text-decoration:none;}
  .news-item a:hover{text-decoration:underline;}
  .news-source{font-size:11px;color:#8b949e;margin-top:2px;}
  
  .loader{text-align:center;color:#58a6ff;padding:30px;font-size:16px;}
</style>
</head>
<body>
<div class="container">
  <div class="top-bar">
    <select id="symbolSelect">
      <option value="BTC-USD">BTC-USD</option>
      <option value="ETH-USD">ETH-USD</option>
      <option value="SOL-USD">SOL-USD</option>
      <option value="XRP-USD">XRP-USD</option>
      <option value="GC=F">الذهب (GC=F)</option>
    </select>
    <select id="intervalSelect">
      <option value="15m">15m</option>
      <option value="1h">1h</option>
      <option value="4h" selected>4h</option>
      <option value="1d">1d</option>
    </select>
    <button class="analyze-btn" onclick="runAnalysis()">تحليل</button>
  </div>

  <div id="contentArea">
    <div class="loader">اختر الأصل والفترة الزمنية واضغط على تحليل...</div>
  </div>
</div>

<script>
  async function runAnalysis() {
    const symbol = document.getElementById('symbolSelect').value;
    const interval = document.getElementById('intervalSelect').value;
    const area = document.getElementById('contentArea');
    
    area.innerHTML = '<div class="loader">⏳ جاري جلب التحليل وقراءة الأخبار بواسطة الذكاء الاصطناعي...</div>';
    
    try {
      let res = await fetch(`/api/analyze?symbol=${symbol}&interval=${interval}`);
      let data = await res.json();
      
      if(!data.success) {
        area.innerHTML = '<div style="color:#da3633;text-align:center;padding:20px;">❌ فشل في جلب التحليل.</div>';
        return;
      }

      let actionClass = data.action.includes('شراء') ? 'buy' : data.action.includes('بيع') ? 'sell' : 'wait';
      
      let levelsHtml = '';
      if(data.levels) {
        levelsHtml = `
          <div class="card">
            <div class="card-title">مستويات الصفقة المقترحة</div>
            <div class="row-item"><span>الدخول (السعر الحالي)</span><span style="color:#58a6ff;">${data.price}</span></div>
            <div class="row-item"><span>وقف الخسارة (SL)</span><span style="color:#da3633;">${data.levels.sl}</span></div>
            <div class="row-item"><span>الهدف الأول (1R)</span><span style="color:#3fb950;">${data.levels.tp1}</span></div>
            <div class="row-item"><span>الهدف الثاني (2R)</span><span style="color:#3fb950;">${data.levels.tp2}</span></div>
            <div class="row-item"><span>الهدف الثالث (3R)</span><span style="color:#3fb950;">${data.levels.tp3}</span></div>
          </div>
        `;
      }

      let fngHtml = data.fng ? `
        <div class="card">
          <div class="card-title">السياق والمشاعر</div>
          <div class="row-item"><span>الخوف والطمع</span><span>${data.fng.value} (${data.fng.label_ar})</span></div>
        </div>
      ` : '';

      let aiHtml = data.ai ? `<div class="card"><div class="card-title">تحليل الذكاء الاصطناعي شامل الأخبار (Qwen)</div><div style="font-size:13px;line-height:1.6;color:#c9d1d9;white-space:pre-wrap;">${data.ai}</div></div>` : '';

      let newsHtml = '';
      if(data.news && data.news.asset && data.news.asset.length > 0) {
        let items = data.news.asset.map(n => `
          <div class="news-item">
            <div><a href="${n.link}" target="_blank">• ${n.title}</a></div>
            <div class="news-source">${n.source}</div>
          </div>
        `).join('');
        newsHtml = `<div class="card"><div class="card-title">أخبار الأصل الفورية</div>${items}</div>`;
      }

      let macroHtml = '';
      if(data.news && data.news.macro && data.news.macro.length > 0) {
        let items = data.news.macro.map(n => `
          <div class="news-item">
            <div><a href="${n.link}" target="_blank">• ${n.title}</a></div>
            <div class="news-source">${n.source}</div>
          </div>
        `).join('');
        macroHtml = `<div class="card"><div class="card-title">أخبار الاقتصاد الكلي (الفيدرالي / التضخم / CPI)</div>${items}</div>`;
      }

      area.innerHTML = `
        <div class="action-banner ${actionClass}">${data.action}</div>
        ${levelsHtml}
        <div class="card">
          <div class="card-title">نموذج بلاك-شولز والخيارات</div>
          <div class="row-item"><span>السعر العادل للخيار (Call)</span><span style="color:#3fb950;">${data.bs_call}</span></div>
          <div class="row-item"><span>التقلب الضمني (IV)</span><span>${data.iv}%</span></div>
          <div class="row-item"><span>دلتا (Delta)</span><span>${data.bs_delta}</span></div>
          <div class="row-item"><span>جاما (Gamma)</span><span>${data.bs_gamma}</span></div>
          <div class="row-item"><span>فيجا (Vega)</span><span>${data.bs_vega}</span></div>
        </div>
        <div class="card">
          <div class="card-title">التحليل الكمي والتفاضلي (${data.score})</div>
          <div class="row-item"><span>السرعة (التفاضل 1)</span><span>${data.velocity}</span></div>
          <div class="row-item"><span>التسارع (التفاضل 2)</span><span>${data.acceleration}</span></div>
          <div class="row-item"><span>مخاطر التكامل (CVaR)</span><span>${data.cvar}</span></div>
          <div class="row-item"><span>مؤشر القوة النسبية (RSI)</span><span>${data.rsi}</span></div>
          <div class="row-item"><span>متوسط الحركة (ATR)</span><span>${data.atr}</span></div>
        </div>
        ${fngHtml}
        ${aiHtml}
        ${newsHtml}
        ${macroHtml}
      `;
    } catch(e) {
      area.innerHTML = '<div style="color:#da3633;text-align:center;padding:20px;">❌ حدث خطأ في الاتصال بالخادم.</div>';
    }
  }
  window.onload = () => { runAnalysis(); };
</script>
</body>
</html>"""

@app.route("/api/analyze")
def api_analyze():
    symbol = request.args.get("symbol", "BTC-USD")
    interval = request.args.get("interval", "4h")
    res = analyze(symbol, interval)
    if not res:
        return jsonify({"success": False})
    
    ind = res["indicators"]
    levels = res["levels"]
    formatted_levels = None
    if levels:
        formatted_levels = {
            "sl": fmt_price(levels["sl"]),
            "tp1": fmt_price(levels["tp1"]),
            "tp2": fmt_price(levels["tp2"]),
            "tp3": fmt_price(levels["tp3"]),
        }

    return jsonify({
        "success": True,
        "symbol": res["symbol"],
        "interval": res["interval"],
        "price": fmt_price(res["price"]),
        "action": res["action"],
        "score": res["quant_score"],
        "velocity": f"{ind['velocity']:+.4f}",
        "acceleration": f"{ind['acceleration']:+.4f}",
        "cvar": f"{ind['cvar_risk']:.4f}",
        "rsi": f"{ind['rsi']:.1f}",
        "atr": f"{ind['atr']:.2f}",
        "bs_call": fmt_price(ind["bs_call"]),
        "iv": f"{ind['iv']:.1f}",
        "bs_delta": f"{ind['bs_delta']:.4f}",
        "bs_gamma": f"{ind['bs_gamma']:.4f}",
        "bs_vega": f"{ind['bs_vega']:.4f}",
        "levels": formatted_levels,
        "ai": res["ai_analysis"],
        "fng": res["fear_greed"],
        "news": res["news"]
    })

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# نظام التنبيهات المجدولة في الخلفية (Background Alerts)
# ==========================================
# حفظ آخر تتبع للمستخدمين الذين تفاعلوا مع البوت لإرسال التنبيهات إليهم
_active_chat_ids = set()

def background_market_watcher(bot_app):
    """مهمة تعمل في الخلفية لمراقبة الأسواق وإرسال تنبيهات قوية تلقائياً"""
    symbols_to_watch = ["BTC-USD", "ETH-USD"]
    logger.info("Background market watcher started.")
    
    # الانتظار قليلاً حتى يبدأ السيرفر بالكامل
    time.sleep(30)
    
    while True:
        try:
            if _active_chat_ids:
                for symbol in symbols_to_watch:
                    res = analyze(symbol, "4h")
                    if res and abs(res["quant_score"]) >= 3: # تنبيه عند وجود إشارة قوية (شراء/بيع قوي)
                        action = res["action"]
                        price = fmt_price(res["price"])
                        score = res["quant_score"]
                        
                        alert_msg = (
                            f"🚨 **تنبيه قنص استباقي من البوت!**\n"
                            f"رصد فرصة قوية على `{symbol}`:\n"
                            f"• التوصية: **{action}** (النقاط: {score})\n"
                            f"• السعر الحالي: `{price}`\n"
                            f"• ملخص الذكاء الاصطناعي:\n{res['ai_analysis'][:300]}..."
                        )
                        
                        for chat_id in _active_chat_ids:
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    bot_app.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown"),
                                    bot_app.loop
                                )
                            except Exception as ex:
                                logger.error(f"Failed to send alert to {chat_id}: {ex}")
            
            # فحص السوق كل ساعتين لتجنب الإزعاج المتكرر
            time.sleep(7200)
        except Exception as e:
            logger.error(f"Error in background watcher: {e}")
            time.sleep(300)

# ==========================================
# تليجرام والأزرار التفاعلية
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _active_chat_ids.add(chat_id)
    
    keyboard = [
        [InlineKeyboardButton("BTC-USD (4h)", callback_data="BTC-USD_4h"), InlineKeyboardButton("ETH-USD (4h)", callback_data="ETH-USD_4h")],
        [InlineKeyboardButton("SOL-USD (4h)", callback_data="SOL-USD_4h"), InlineKeyboardButton("XRP-USD (4h)", callback_data="XRP-USD_4h")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 **أهلاً بك في بوت التداول الكمي المطور (الإصدار الاستباقي v3.5)!**\n"
        "تم تفعيل نظام المراقبة التلقائية والتنبيهات. اختر الأصل المطلوب أدناه للتحليل الفوري:",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _active_chat_ids.add(chat_id)
    
    args = context.args
    if not args:
        await update.message.reply_text("الاستخدام: `/smart BTC-USD 4h`", parse_mode="Markdown")
        return
    symbol = normalize_symbol(args[0])
    interval = args[1].lower() if len(args) > 1 else "4h"
    await process_and_send(update.message, symbol, interval)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    _active_chat_ids.add(chat_id)
    
    await query.answer()
    data_parts = query.data.split("_")
    symbol, interval = data_parts[0], data_parts[1]
    await process_and_send(query.message, symbol, interval)

async def process_and_send(message, symbol, interval):
    status_msg = await message.reply_text(f"⏳ جاري حساب البيانات وقراءة الأخبار بواسطة الذكاء الاصطناعي لـ {symbol}...")
    res = analyze(symbol, interval)
    if not res:
        await status_msg.edit_text("❌ فشل في جلب البيانات أو إجراء التحليل.")
        return
    
    diff = res['indicators']
    msg = (
        f"📊 **تحليل {res['symbol']} ({res['interval'].upper()})**\n"
        f"السعر الحالي: `{fmt_price(res['price'])}`\n"
        f"التوصية: **{res['action']}** (النقاط: {res['quant_score']})\n\n"
        f"📐 **نموذج بلاك-شولز والخيارات:**\n"
        f"• السعر العادل (Call): `{fmt_price(diff['bs_call'])}`\n"
        f"• التقلب الضمني (IV): `{diff['iv']:.1f}%`\n"
        f"• دلتا (Delta): `{diff['bs_delta']:.4f}` | جاما (Gamma): `{diff['bs_gamma']:.4f}`\n\n"
        f"🤖 **تحليل الذكاء الاصطناعي والأخبار الفورية:**\n{res['ai_analysis'] or 'غير متوفر'}"
    )
    await status_msg.edit_text(msg, parse_mode="Markdown")

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is missing!")
        return
    
    # تشغيل سيرفر الويب Flask في الخلفية
    threading.Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("smart", smart_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل مراقب السوق الخلفي للتنبيهات الاستباقية
    threading.Thread(target=background_market_watcher, args=(application,), daemon=True).start()
    
    logger.info("Bot v3.5 Proactive AI & Alerts is polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
