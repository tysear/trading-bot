
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

# ==========================================
# أدوات عامة
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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantBot/2.5)"}

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

# ==========================================
# جلب بيانات الأسعار والمؤشرات
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

    return {
        "price": price, "rsi": float(rsi_wilder(c).iloc[-1]), "atr": atr,
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "macd_hist": float(hist.iloc[-1]),
        "velocity": velocity, "acceleration": acceleration, "cvar_risk": cvar_val,
        "support_50": support, "resistance_50": resistance,
    }

def quant_score(ind):
    score, reasons = 0, []
    p, e20, e50 = ind["price"], ind["ema20"], ind["ema50"]
    if p > e50 and e20 > e50: score += 2
    elif p < e50 and e20 < e50: score -= 2
    else: score += 1 if p > e50 else -1

    if ind["macd_hist"] > 0: score += 1
    elif ind["macd_hist"] < 0: score -= 1

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
    }

def analyze(symbol, interval="4h"):
    try:
        df = fetch_ohlcv(symbol, interval)
        if df is None: return None
        ind = compute_indicators(df, interval)
        qscore, qreasons = quant_score(ind)
        qaction = score_to_action(qscore)

        return {
            "symbol": symbol, "interval": interval, "price": ind["price"],
            "action": qaction, "quant_score": qscore, "indicators": ind,
            "levels": trade_levels(qaction, ind["price"], ind["atr"]),
        }
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return None

# ==========================================
# خادم الويب (Flask Dashboard مع الأصول والتايم فريم)
# ==========================================
app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"status": "running", "bot": "Quant Trading Bot v2.6"})

@app.route("/dashboard")
def dashboard():
    return """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><title>لوحة تحكم بوت التداول</title>
<style>
  body{font-family:'Segoe UI', Tahoma, sans-serif;background:#0f0c29;color:#fff;padding:20px;text-align:center;}
  .container{max-width:800px;margin:0 auto;background:rgba(255,255,255,0.05);padding:30px;border-radius:15px;backdrop-filter:blur(10px);}
  h1{color:#00d2ff;margin-bottom:10px;}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin-top:25px;}
  .card{background:rgba(255,255,255,0.1);padding:20px;border-radius:10px;border:1px solid rgba(255,255,255,0.2);}
  .card h3{margin-bottom:10px;color:#3a7bd5;}
</style>
</head>
<body>
<div class="container">
  <h1>لوحة تحكم بوت التداول الكمي والتفاضلي</h1>
  <p>النظام يعمل بكفاءة رياضية عالية ومتصل بتليجرام بنجاح.</p>
  <div class="grid">
    <div class="card"><h3>العملات المدعومة</h3><p>BTC-USD, ETH-USD, XRP-USD, SOL-USD</p></div>
    <div class="card"><h3>الفواصل الزمنية</h3><p>15m, 1h, 4h, 1d</p></div>
    <div class="card"><h3>النماذج المتاحة</h3><p>التفاضل والتكامل + CVaR + SMC</p></div>
  </div>
</div>
</body>
</html>"""

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# أوامر تليجرام مع الأزرار التفاعلية
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("BTC-USD (4h)", callback_data="BTC-USD_4h"), InlineKeyboardButton("ETH-USD (4h)", callback_data="ETH-USD_4h")],
        [InlineKeyboardButton("SOL-USD (4h)", callback_data="SOL-USD_4h"), InlineKeyboardButton("XRP-USD (4h)", callback_data="XRP-USD_4h")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 **أهلاً بك في بوت التداول الكمي المطور!**\nاختر العملة المطلوبة أدناه للتحليل الفوري بضغطة زر:",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("الاستخدام: `/smart BTC-USD 4h` أو اضغط على الأوامر.", parse_mode="Markdown")
        return
    symbol = normalize_symbol(args[0])
    interval = args[1].lower() if len(args) > 1 else "4h"
    await process_and_send(update.message, symbol, interval)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_parts = query.data.split("_")
    symbol, interval = data_parts[0], data_parts[1]
    await process_and_send(query.message, symbol, interval)

async def process_and_send(message, symbol, interval):
    status_msg = await message.reply_text(f"⏳ جاري جلب البيانات وتطبيق التفاضل والتكامل على {symbol}...")
    res = analyze(symbol, interval)
    if not res:
        await status_msg.edit_text("❌ فشل في جلب البيانات أو إجراء التحليل.")
        return
    
    diff = res['indicators']
    levels_txt = ""
    if res['levels']:
        levels_txt = f"\n🎯 **مستويات التداول:**\nSL: `{fmt_price(res['levels']['sl'])}`\nTP1: `{fmt_price(res['levels']['tp1'])}`\nTP2: `{fmt_price(res['levels']['tp2'])}`"

    msg = (
        f"📊 **تحليل {res['symbol']} ({res['interval'].upper()})**\n"
        f"السعر الحالي: `{fmt_price(res['price'])}`\n"
        f"التوصية: **{res['action']}** (النقاط: {res['quant_score']})\n\n"
        f"📐 **التحليل الرياضي المتقدم:**\n"
        f"• السرعة اللحظية (التفاضل 1): `{diff['velocity']:+.4f}`\n"
        f"• التسارع (التفاضل 2): `{diff['acceleration']:+.4f}`\n"
        f"• مخاطر التكامل (CVaR): `{diff['cvar_risk']:.4f}`"
        f"{levels_txt}"
    )
    await status_msg.edit_text(msg, parse_mode="Markdown")

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is missing!")
        return
    
    threading.Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("smart", smart_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot is polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
