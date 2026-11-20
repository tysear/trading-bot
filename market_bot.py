import asyncio
import json
import logging
import os
import threading
import time
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from scipy.stats import norm
from flask import Flask, jsonify, request
from flask_sock import Sock

try:
    import yfinance as yf
except ImportError:
    yf = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("quantbot")

# ==========================================
# الإعدادات
# ==========================================
DEFAULT_CLAUDE_MODEL = "anthropic/claude-sonnet-5.5"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = (os.environ.get("OPENROUTER_MODEL", DEFAULT_CLAUDE_MODEL) or DEFAULT_CLAUDE_MODEL).strip()
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

BINANCE_SPOT_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
CACHE_TTL = {"15m": 120, "1h": 300, "4h": 600, "1d": 1800}

FNG_AR = {"extreme fear": "خوف شديد", "fear": "خوف", "neutral": "محايد", "greed": "طمع", "extreme greed": "طمع شديد"}

# قائمة المتصلين عبر WebSockets لبث التنبيهات التلقائية لهم
_ws_clients = set()
_ws_lock = threading.Lock()

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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantBot/4.1)"}

def http_get(url, params=None, timeout=12):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
    except requests.RequestException as e:
        logger.warning("Request failed %s: %s", url, e)
    return None

def is_crypto(symbol):
    return symbol.endswith("-USD") and "=" not in symbol

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
        source = (it.findtext("source") or "Google News").strip()
        if title: items.append({"title": title, "link": link, "source": source})
        if len(items) >= limit: break
    return items

def get_news(symbol):
    key = f"news_{symbol}"
    cached = cache_get(key, 600)
    if cached is not None: return cached
    clean_sym = symbol.split("-")[0].replace("=X", "").replace("=F", "") if ("-" in symbol or "=" in symbol) else symbol
    asset = news_google(f"{clean_sym} crypto" if is_crypto(symbol) else f"{clean_sym} market OR stock", 4)
    macro = news_google("Federal Reserve OR inflation OR CPI", 4)
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
        return {"value": 45, "label": "neutral", "label_ar": "محايد"}

# ==========================================
# جلب البيانات والمؤشرات والهندسة العكسية
# ==========================================
def fetch_binance(symbol, interval):
    base = symbol.split("-")[0] if "-" in symbol else symbol
    pair = base + "USDT"
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
        yf_sym = symbol
        period = {"15m": "1mo", "1h": "3mo", "4h": "3mo", "1d": "1y"}.get(interval, "3mo")
        raw = yf.Ticker(yf_sym).history(period=period, interval="1h" if interval == "4h" else interval)
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
    c, h, l = df["close"], df["high"], df["low"]
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

    distance_to_support = ((price - support) / price) * 100
    distance_to_resistance = ((resistance - price) / price) * 100
    
    if distance_to_support < 1.5:
        smc_status = "منطقة فخ سيولة سفلية (احتمال كسر كاذب لضرب الوقف وارتداد صاعد)"
    elif distance_to_resistance < 1.5:
        smc_status = "منطقة تجمع سيولة علوية (احتمال اختراق وهمي لتصريف العقود)"
    else:
        smc_status = "منطقة وسطية (مراقبة هادئة / تجميع مؤسسي)"

    return {
        "price": price, "rsi": float(rsi_wilder(c).iloc[-1]), "atr": atr,
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "macd_hist": float(hist.iloc[-1]),
        "velocity": velocity, "acceleration": acceleration, "cvar_risk": cvar_val,
        "support_50": support, "resistance_50": resistance,
        "bs_call": bs_call, "bs_delta": bs_delta, "bs_gamma": bs_gamma, "bs_theta": bs_theta, "bs_vega": bs_vega, "iv": historical_vol * 100,
        "smc_status": smc_status, "distance_support": distance_to_support, "distance_resistance": distance_to_resistance
    }

def quant_score(ind):
    score, reasons = 0, []
    p, e20, e50 = ind["price"], ind["ema20"], ind["ema50"]
    if p > e50 and e20 > e50: score += 2; reasons.append("اتجاه صاعد")
    elif p < e50 and e20 < e50: score -= 2; reasons.append("اتجاه هابط")
    else: score += 1 if p > e50 else -1

    if ind["macd_hist"] > 0: score += 1; reasons.append("زخم إيجابي")
    elif ind["macd_hist"] < 0: score -= 1; reasons.append("زخم سلبي")

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

# ==========================================
# دالة الذكاء الاصطناعي (مع الترويسات المطلوبة لـ OpenRouter)
# ==========================================
def ask_ai(payload):
    if not OPENROUTER_API_KEY:
        return "الذكاء الاصطناعي غير متاحة؛ يرجى التحقق من إضافة المفتاح وعمل Redeploy."
    
    model_name = (OPENROUTER_MODEL or DEFAULT_CLAUDE_MODEL).strip()
    body = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "أنت خبير تداول كمي وهندسة عكسية ومحلل أسواق مالية. اكتب تحليلاً منظماً، دقيقاً، ومختصراً باللغة العربية."
            },
            {
                "role": "user",
                "content": f"قدم تحليلاً مختصراً وواضحاً باللغة العربية بناءً على هذه البيانات: {json.dumps(payload, ensure_ascii=False)}"
            }
        ]
    }
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://railway.app",
        "X-Title": "Quantitative Trading Dashboard",
        "Content-Type": "application/json"
    }
    
    try:
        res = requests.post(OPENROUTER_API_URL, json=body, headers=headers, timeout=30)
        if res.status_code == 200:
            data = res.json()
            if "choices" in data and len(data["choices"]) > 0:
                message = data["choices"][0].get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                if str(content).strip():
                    return str(content).strip()
            return "استجابة فارغة من النموذج."
        else:
            logger.error("OpenRouter HTTP %s (model=%s): %s", res.status_code, model_name, res.text)
            return f"خطأ من مزود الذكاء الاصطناعي (رمز {res.status_code})."
    except Exception as e:
        logger.error("AI Error: %s", e)
        return "تعذر الاتصال بمحرك الذكاء الاصطناعي حالياً."

def analyze(symbol, interval="4h", include_ai=True):
    try:
        df = fetch_ohlcv(symbol, interval)
        if df is None: return None
        ind = compute_indicators(df, interval)
        qscore, qreasons = quant_score(ind)
        qaction = score_to_action(qscore)
        news = get_news(symbol)
        fng = get_fear_greed()

        ai_comment = None
        if include_ai:
            payload = {
                "symbol": symbol, "interval": interval,
                "technical": ind, "quant_action": qaction, "score": qscore,
                "fear_and_greed": fng, "latest_news": news
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
# بث التنبيهات التلقائية ومراقب السوق في الخلفية
# ==========================================
def broadcast_alert(alert_data):
    msg = json.dumps({"type": "AUTO_ALERT", "alert": alert_data}, ensure_ascii=False)
    with _ws_lock:
        dead_clients = set()
        for client in _ws_clients:
            try:
                client.send(msg)
            except Exception:
                dead_clients.add(client)
        _ws_clients.difference_update(dead_clients)

def auto_market_scanner():
    watchlist = [
        ("BTC-USD", "1h"),
        ("ETH-USD", "1h"),
        ("SOL-USD", "1h"),
        ("XRP-USD", "1h"),
        ("BNB-USD", "1h"),
        ("GC=F", "1h"),
        ("EURUSD=X", "1h"),
        ("NVDA", "1h"),
    ]
    logger.info("Auto Market Scanner started in background...")
    time.sleep(10)
    
    while True:
        try:
            for symbol, interval in watchlist:
                res = analyze(symbol, interval, include_ai=False)
                if not res:
                    continue
                
                ind = res["indicators"]
                is_strong_signal = abs(res["quant_score"]) >= 2
                is_trap_zone = ind["distance_support"] < 1.5 or ind["distance_resistance"] < 1.5
                
                if is_strong_signal or is_trap_zone:
                    alert_payload = {
                        "symbol": symbol,
                        "interval": interval,
                        "action": res["action"],
                        "price": fmt_price(res["price"]),
                        "score": res["quant_score"],
                        "smc_status": ind["smc_status"],
                        "time": datetime.now(timezone.utc).strftime("%H:%M UTC")
                    }
                    broadcast_alert(alert_payload)
                    logger.info(f"Broadcasted auto alert for {symbol}: {res['action']}")
                
                time.sleep(5)
        except Exception as e:
            logger.error(f"Scanner error: {e}")
        
        time.sleep(300)

# ==========================================
# خادم الويب وتطبيق Flask مع WebSockets
# ==========================================
app = Flask(__name__)
sock = Sock(app)

@app.route("/")
def index():
    return dashboard_html()

@app.route("/dashboard")
def dashboard():
    return dashboard_html()

@app.route("/api/analyze")
def api_analyze():
    symbol = request.args.get("symbol", "BTC-USD")
    interval = request.args.get("interval", "4h")
    include_ai = request.args.get("include_ai", "true").lower() in ("1", "true", "yes")
    res = analyze(symbol, interval, include_ai=include_ai)
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
        "smc_status": ind["smc_status"],
        "support": fmt_price(ind["support_50"]),
        "resistance": fmt_price(ind["resistance_50"]),
        "velocity": f"{ind['velocity']:+.4f}",
        "acceleration": f"{ind['acceleration']:+.4f}",
        "cvar": f"{ind['cvar_risk']:.4f}",
        "rsi": f"{ind['rsi']:.1f}",
        "atr": f"{ind['atr']:.2f}",
        "ema20": fmt_price(ind["ema20"]),
        "ema50": fmt_price(ind["ema50"]),
        "macd_hist": f"{ind['macd_hist']:.2f}",
        "bs_call": fmt_price(ind["bs_call"]),
        "iv": f"{ind['iv']:.1f}",
        "bs_delta": f"{ind['bs_delta']:.4f}",
        "bs_gamma": f"{ind['bs_gamma']:.4f}",
        "levels": formatted_levels,
        "ai": res["ai_analysis"],
        "fng": res["fear_greed"],
        "news": res["news"]
    })

@sock.route('/ws/alerts')
def websocket_alerts(ws):
    with _ws_lock:
        _ws_clients.add(ws)
    try:
        while True:
            data = ws.receive()
            if data is None:
                break
    except Exception:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(ws)

def dashboard_html():
    return """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>لوحة التداول الكمي والهندسة العكسية - Live</title>
<style>
  body{font-family:'Segoe UI', Tahoma, sans-serif;background:#0d1117;color:#e6edf3;padding:15px;margin:0;}
  .container{max-width:600px;margin:0 auto;background:#161b22;padding:20px;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,0.6);}
  .top-bar{display:flex;gap:10px;margin-bottom:15px;}
  select, button{background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:10px;border-radius:8px;font-size:14px;cursor:pointer;}
  select{flex:1;}
  button.analyze-btn{background:#238636;color:#fff;font-weight:bold;flex:2;border:none;}
  button.analyze-btn:hover{background:#2ea043;}
  
  .auto-alerts-box{background:#1c2128;border:1px solid #f0883e;border-radius:10px;padding:12px;margin-bottom:15px;}
  .auto-alerts-header{display:flex;justify-content:space-between;align-items:center;color:#f0883e;font-weight:bold;font-size:14px;margin-bottom:8px;}
  .auto-alert-item{background:#0d1117;border-right:4px solid #f0883e;padding:10px;border-radius:6px;margin-bottom:8px;font-size:13px;}
  .auto-alert-item.buy-alert{border-right-color:#3fb950;}
  .auto-alert-item.sell-alert{border-right-color:#da3633;}
  .load-alert-btn{background:#30363d;color:#58a6ff;border:none;padding:4px 8px;border-radius:4px;font-size:11px;margin-top:6px;cursor:pointer;}

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
  .ws-status{font-size:11px;color:#3fb950;text-align:left;margin-bottom:10px;}

  .ai-prose{font-size:13px;line-height:1.7;color:#c9d1d9;overflow-x:auto;}
  .ai-prose h1,.ai-prose h2,.ai-prose h3{color:#58a6ff;margin:10px 0 6px;font-size:14px;}
  .ai-prose p{margin:6px 0;}
  .ai-prose ul{margin:6px 0;padding-right:18px;}
  .ai-prose li{margin-bottom:4px;}
  .ai-prose table{width:100%;border-collapse:collapse;margin:8px 0;font-size:12px;}
  .ai-prose th,.ai-prose td{border:1px solid #30363d;padding:6px;text-align:right;}
  .ai-prose th{background:#161b22;color:#e6edf3;}
</style>
</head>
<body>
<div class="container">
  <div class="ws-status" id="wsStatus">🟢 الويب سوكيت: متصل (Live)</div>
  
  <div class="top-bar">
    <select id="symbolSelect">
      <option value="BTC-USD">BTC-USD</option>
      <option value="ETH-USD">ETH-USD</option>
      <option value="SOL-USD">SOL-USD</option>
      <option value="XRP-USD">XRP-USD</option>
      <option value="BNB-USD">BNB-USD</option>
      <option value="GC=F">الذهب (GC=F)</option>
      <option value="EURUSD=X">اليورو / دولار (EURUSD=X)</option>
      <option value="NVDA">إنفيديا (NVDA)</option>
    </select>
    <select id="intervalSelect">
      <option value="15m">15m</option>
      <option value="1h">1h</option>
      <option value="4h" selected>4h</option>
      <option value="1d">1d</option>
    </select>
    <button class="analyze-btn" onclick="runAnalysis(true)">تحليل فوري</button>
  </div>

  <div class="auto-alerts-box">
    <div class="auto-alerts-header">
      <span>🚨 رادار التنبيهات التلقائية الحية (Auto-Alerts)</span>
      <span style="font-size:11px;color:#8b949e;">فحص كل 5 دقائق</span>
    </div>
    <div id="autoAlertsList">
      <div style="font-size:12px;color:#8b949e;text-align:center;padding:8px;">
        الرادار نشط يراقب الأصول المختارة.. ستظهر التنبيهات هنا تلقائياً فور رصد سيولة أو إشارة قوية.
      </div>
    </div>
  </div>

  <div id="contentArea">
    <div class="loader">اختر الأصل والفترة الزمنية واضغط على تحليل...</div>
  </div>
</div>

<script>
  let ws;
  function initWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(proto + window.location.host + '/ws/alerts');
    ws.onopen = () => { document.getElementById('wsStatus').innerText = '🟢 الويب سوكيت: متصل (Live)'; };
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'AUTO_ALERT' && msg.alert) {
          addAutoAlertToUI(msg.alert);
        }
      } catch (e) {}
    };
    ws.onclose = () => { 
      document.getElementById('wsStatus').innerText = '🔴 الويب سوكيت: انقطع الاتصال، جاري إعادة المحاولة...';
      setTimeout(initWebSocket, 3000);
    };
  }
  initWebSocket();

  function escapeHtml(str) {
    return String(str || '')
      .split('&').join('&amp;')
      .split('<').join('&lt;')
      .split('>').join('&gt;');
  }

  function formatBoldText(raw) {
    const safe = escapeHtml(raw);
    const parts = safe.split('**');
    let out = '';
    for (let i = 0; i < parts.length; i++) {
      out += (i % 2 === 1) ? ('<strong>' + parts[i] + '</strong>') : parts[i];
    }
    return out;
  }

  function isSeparatorRow(line) {
    if (line.indexOf('|') === -1) return false;
    const cleaned = line.split('|').join('').split('-').join('').split(':').join('').trim();
    return cleaned.length === 0;
  }

  function renderMarkdownLite(mdText) {
    const normalized = String(mdText || '').split('\r\n').join('\n').split('\r').join('\n');
    const lines = normalized.split('\n');
    let html = '';
    let inList = false;
    let inTable = false;

    const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
    const closeTable = () => { if (inTable) { html += '</tbody></table>'; inTable = false; } };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) {
        closeList();
        closeTable();
        continue;
      }

      if (isSeparatorRow(line)) {
        continue;
      }

      if (line.indexOf('|') !== -1) {
        closeList();
        let rowStr = line;
        if (rowStr.startsWith('|')) rowStr = rowStr.slice(1);
        if (rowStr.endsWith('|')) rowStr = rowStr.slice(0, -1);
        const cells = rowStr.split('|').map(c => c.trim());
        if (!inTable) {
          html += '<table><thead><tr>' + cells.map(c => ('<th>' + formatBoldText(c) + '</th>')).join('') + '</tr></thead><tbody>';
          inTable = true;
        } else {
          html += '<tr>' + cells.map(c => ('<td>' + formatBoldText(c) + '</td>')).join('') + '</tr>';
        }
        continue;
      } else {
        closeTable();
      }

      if (line.startsWith('### ')) {
        closeList();
        html += '<h3>' + formatBoldText(line.slice(4)) + '</h3>';
      } else if (line.startsWith('## ')) {
        closeList();
        html += '<h2>' + formatBoldText(line.slice(3)) + '</h2>';
      } else if (line.startsWith('# ')) {
        closeList();
        html += '<h1>' + formatBoldText(line.slice(2)) + '</h1>';
      } else if (line.startsWith('- ') || line.startsWith('* ') || line.startsWith('• ')) {
        closeTable();
        if (!inList) { html += '<ul>'; inList = true; }
        html += '<li>' + formatBoldText(line.slice(2)) + '</li>';
      } else {
        closeList();
        html += '<p>' + formatBoldText(line) + '</p>';
      }
    }

    closeList();
    closeTable();
    return html;
  }

  function addAutoAlertToUI(alert) {
    const list = document.getElementById('autoAlertsList');
    if (list.innerText.includes('الرادار نشط يراقب')) {
      list.innerHTML = '';
    }
    const cls = alert.action.includes('شراء') ? 'buy-alert' : alert.action.includes('بيع') ? 'sell-alert' : '';
    const item = document.createElement('div');
    item.className = 'auto-alert-item ' + cls;
    item.innerHTML = `
      <div style="display:flex;justify-content:space-between;font-weight:bold;">
        <span>🔔 ${alert.symbol} (${alert.interval}) — ${alert.action}</span>
        <span style="color:#8b949e;font-size:11px;">${alert.time}</span>
      </div>
      <div style="margin-top:4px;color:#c9d1d9;">السعر: <b>${alert.price}</b> | التقييم: <b>${alert.score}</b></div>
      <div style="margin-top:3px;color:#f0883e;font-size:12px;">${alert.smc_status}</div>
      <button class="load-alert-btn" onclick="loadAlertAnalysis('${alert.symbol}', '${alert.interval}')">عرض بيانات التنبيه (بدون استهلاك رصيد AI) ⬅️</button>
    `;
    list.prepend(item);
    if (list.children.length > 5) {
      list.removeChild(list.lastChild);
    }
  }

  function loadAlertAnalysis(sym, intv) {
    document.getElementById('symbolSelect').value = sym;
    document.getElementById('intervalSelect').value = intv;
    runAnalysis(false);
  }

  async function runAnalysis(withAi = true) {
    const symbol = document.getElementById('symbolSelect').value;
    const interval = document.getElementById('intervalSelect').value;
    const area = document.getElementById('contentArea');
    
    area.innerHTML = '<div class="loader">⏳ جاري تفكيك صفقات صناع السوق وقراءة الأخبار المباشرة...</div>';
    
    try {
      let res = await fetch('/api/analyze?symbol=' + encodeURIComponent(symbol) + '&interval=' + encodeURIComponent(interval) + '&include_ai=' + (withAi ? 'true' : 'false'));
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

      let smcHtml = `
        <div class="card" style="border-color:#58a6ff;">
          <div class="card-title">🔍 الهندسة العكسية وصناع السوق (Smart Money)</div>
          <div class="row-item"><span>حالة السيولة وفخاخ الحيتان</span><span style="color:#f0883e;">${data.smc_status}</span></div>
          <div class="row-item"><span>الدعم المؤسسي</span><span>${data.support}</span></div>
          <div class="row-item"><span>المقاومة المؤسسي</span><span>${data.resistance}</span></div>
        </div>
      `;

      let techHtml = `
        <div class="card">
          <div class="card-title">المؤشرات الفنية (Technical Indicators)</div>
          <div class="row-item"><span>RSI (14)</span><span>${data.rsi}</span></div>
          <div class="row-item"><span>ATR</span><span>${data.atr}</span></div>
          <div class="row-item"><span>EMA20 / EMA50</span><span>${data.ema20} / ${data.ema50}</span></div>
          <div class="row-item"><span>MACD Hist</span><span>${data.macd_hist}</span></div>
        </div>
      `;

      let fngHtml = data.fng ? `
        <div class="card">
          <div class="card-title">السياق والمشاعر</div>
          <div class="row-item"><span>الخوف والطمع</span><span>${data.fng.value} (${data.fng.label_ar})</span></div>
        </div>
      ` : '';

      let aiHtml = data.ai ? ('<div class="card"><div class="card-title">تحليل الذكاء الاصطناعي وهندسة السوق</div><div class="ai-prose">' + renderMarkdownLite(data.ai) + '</div></div>') : '';

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
        ${smcHtml}
        ${levelsHtml}
        ${techHtml}
        <div class="card">
          <div class="card-title">نموذج بلاك-شولز والخيارات</div>
          <div class="row-item"><span>السعر العادل للخيار (Call)</span><span style="color:#3fb950;">${data.bs_call}</span></div>
          <div class="row-item"><span>التقلب الضمني (IV)</span><span>${data.iv}%</span></div>
          <div class="row-item"><span>دلتا (Delta)</span><span>${data.bs_delta}</span></div>
          <div class="row-item"><span>جاما (Gamma)</span><span>${data.bs_gamma}</span></div>
        </div>
        <div class="card">
          <div class="card-title">التحليل الكمي والتفاضلي (${data.score})</div>
          <div class="row-item"><span>السرعة (التفاضل 1)</span><span>${data.velocity}</span></div>
          <div class="row-item"><span>التسارع (التفاضل 2)</span><span>${data.acceleration}</span></div>
          <div class="row-item"><span>مخاطر التكامل (CVaR)</span><span>${data.cvar}</span></div>
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
  window.onload = () => { runAnalysis(false); };
</script>
</body>
</html>"""

# ==========================================
# تشغيل التطبيق
# ==========================================
def main():
    threading.Thread(target=auto_market_scanner, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask-Sock server with Auto-Scanner on port {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
