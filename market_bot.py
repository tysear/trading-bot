import requests
import time
import yfinance as yf
from datetime import datetime, timedelta
import logging
import os
from flask import Flask
import threading
import numpy as np
import re

try:
    from signal_image import generate_signal_image
except ImportError:
    generate_signal_image = None

try:
    from smc_indicators import get_smc_analysis
except ImportError:
    get_smc_analysis = None

try:
    from trade_logger import log_trade, get_performance, get_recent_trades
except ImportError:
    log_trade = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_API_URL = "https://finnhub.io/api/v1"

GEMINI_MODELS = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.5-flash-latest"]

ALL_SYMBOLS = [
    'BTC-USD', 'ETH-USD', 'XRP-USD', 'BNB-USD', 'SOL-USD', 'ADA-USD', 'DOGE-USD', 'DOT-USD', 'MATIC-USD', 'AVAX-USD',
    'LINK-USD', 'UNI-USD', 'ATOM-USD', 'LTC-USD', 'ETC-USD', 'XLM-USD', 'ALGO-USD', 'VET-USD', 'FIL-USD', 'TRX-USD',
    'GC=F', 'SI=F', 'CL=F', 'EURUSD=X', 'GBPUSD=X', 'USDJPY=X',
    'ZW=F', 'ZC=F', 'ZS=F', 'KC=F', 'SB=F', 'CT=F',
    '^GSPC', '^IXIC', '^DJI', '^RUT', '^VIX',
    'AAPL', 'TSLA', 'MSFT', 'AMZN', 'NVDA', 'GOOGL', 'META', 'NFLX',
]

CRYPTO_SYMBOLS = [s for s in ALL_SYMBOLS if s.endswith('-USD')]

app = Flask(__name__)
try:
    from dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp)
except ImportError:
    pass

@app.route('/')
def home(): return "Bot running!"
@app.route('/health')
def health(): return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def clean_markdown(text):
    if not text: return ""
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Send failed: {resp.text}")
    except Exception as e:
        logger.error(f"Send error: {e}")

def calculate_stochastic_analysis(prices: list, volumes: list = None) -> dict:
    if not prices or len(prices) < 10:
        return {}
    arr = np.array(prices, dtype=float)
    log_returns = np.diff(np.log(arr))
    daily_volatility = np.std(log_returns)
    drift = np.mean(log_returns)
    risk_free_rate = 0.02 / 252
    sharpe_ratio = (drift - risk_free_rate) / daily_volatility if daily_volatility != 0 else 0
    
    lags = range(2, min(20, len(arr)))
    if len(lags) > 1:
        tau = [np.std(np.subtract(arr[lag:], arr[:-lag])) for lag in lags]
        reg = np.polyfit(np.log(lags), np.log(tau), 1)
        hurst_exponent = reg[0] / 2.0
    else:
        hurst_exponent = 0.5
        
    return {
        'sharpe_ratio': sharpe_ratio,
        'hurst_exponent': hurst_exponent,
        'var_95': np.percentile(log_returns, 5),
        'daily_volatility': daily_volatility,
        'drift': drift
    }

def fetch_data(symbol, interval="1h"):
    try:
        period = "7d" if interval in ['1m', '5m'] else "1mo" if interval in ['15m', '30m'] else "6mo" if interval == '4h' else "1y"
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        if hist.empty:
            return None
        closes, highs, lows, volumes = hist['Close'].tolist(), hist['High'].tolist(), hist['Low'].tolist(), hist['Volume'].tolist()
        cleaned = [{'close': c, 'high': h, 'low': l, 'volume': v} for c, h, l, v in zip(closes, highs, lows, volumes) if c > 0 and not np.isnan(c)]
        if len(cleaned) < 30:
            return None
        return {
            'closes': [d['close'] for d in cleaned],
            'highs': [d['high'] for d in cleaned],
            'lows': [d['low'] for d in cleaned],
            'volumes': [d['volume'] for d in cleaned],
            'interval': interval
        }
    except Exception as e:
        logger.error(f"Fetch error {symbol}: {e}")
        return None

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = [max(prices[i] - prices[i-1], 0) for i in range(-period, 0)]
    losses = [max(-(prices[i] - prices[i-1]), 0) for i in range(-period, 0)]
    avg_gain, avg_loss = sum(gains)/period, sum(losses)/period
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(-period, 0)]
    return sum(tr) / period

def get_action_recommendation(data):
    rsi = data.get('rsi', 50)
    score = 0
    math_signals = []
    
    if rsi < 30:
        score += 3
        math_signals.append("RSI تشبع بيعي")
    elif rsi > 70:
        score -= 3
        math_signals.append("RSI تشبع شرائي")
    
    stochastic = data.get('stochastic', {})
    sharpe = stochastic.get('sharpe_ratio', 0)
    if sharpe > 1.0:
        score += 2
        math_signals.append("Sharpe ممتاز")
    elif sharpe < -0.5:
        score -= 2
        math_signals.append("Sharpe سلبي")
    
    smc = data.get('smc', {})
    if smc:
        score += smc.get('score', 0)
        pd_zone = smc.get('premium_discount', {}).get('zone', '')
        if pd_zone == 'LOWER_DISCOUNT':
            score += 2
            math_signals.append("منطقة خصم")
        elif pd_zone == 'UPPER_PREMIUM':
            score -= 2
            math_signals.append("منطقة قسط")
    
    if score >= 4:
        action, conf = 'شراء قوي 🟢', 'عالية'
    elif score >= 1:
        action, conf = 'شراء حذر 🟡', 'متوسطة'
    elif score <= -4:
        action, conf = 'بيع قوي 🔴', 'عالية'
    elif score <= -1:
        action, conf = 'بيع حذر 🟠', 'متوسطة'
    else:
        action, conf = 'انتظار ⚪', 'منخفضة'
    
    return {
        'action': action,
        'score': score,
        'confidence': conf,
        'math_signals': math_signals
    }

def unified_analysis(symbol, interval="1h"):
    try:
        data = fetch_data(symbol, interval)
        if not data:
            return None
        closes, highs, lows = data['closes'], data['highs'], data['lows']
        price = float(closes[-1])
        
        rsi = calc_rsi(closes)
        atr = calc_atr(highs, lows, closes)
        stochastic = calculate_stochastic_analysis(closes, data.get('volumes', []))
        
        smc_data = None
        if get_smc_analysis:
            try:
                smc_data = get_smc_analysis(closes, highs, lows, data.get('volumes', []))
            except Exception as e:
                logger.error(f"SMC error: {e}")
            
        rec = get_action_recommendation({'rsi': rsi, 'stochastic': stochastic, 'smc': smc_data})
        
        if 'شراء' in rec['action']:
            sl, tp1, tp2 = price - (atr * 0.8), price + (atr * 1.5), price + (atr * 2.5)
        elif 'بيع' in rec['action']:
            sl, tp1, tp2 = price + (atr * 0.8), price - (atr * 1.5), price - (atr * 2.5)
        else:
            sl, tp1, tp2 = price - (atr * 0.8), price + (atr * 1.5), price + (atr * 2.5)
        
        rr = abs(tp1 - price) / abs(price - sl) if abs(price - sl) > 0 else 0
        
        result = {
            'symbol': symbol,
            'price': price,
            'interval': interval,
            'rsi': rsi,
            'atr': atr,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'rr': rr,
            'recommendation': rec,
            'stochastic': stochastic
        }
        
        if smc_data and smc_data.get('premium_discount'):
            result['smc_pd'] = f"{smc_data['premium_discount']['zone']} ({smc_data['premium_discount']['percentage']:.0f}%)"
        else:
            result['smc_pd'] = "محايد"
            
        return result
    except Exception as e:
        logger.error(f"Analysis error {symbol}: {e}")
        return None

def ask_qwen(data):
    if not OPENROUTER_API_KEY:
        return None
    try:
        prompt = f"رمز: {data['symbol']}. السعر: {data['price']}. RSI: {data['rsi']:.1f}. المنطقة: {data.get('smc_pd', 'N/A')}. Sharpe: {data['stochastic'].get('sharpe_ratio', 0):.2f}. أعطِ سبباً واحداً مختصراً جداً بالعربية (جملة واحدة) للتوصية."
        res = requests.post(
            OPENROUTER_API_URL,
            json={
                "model": "qwen/qwen-2.5-72b-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 100
            },
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=30
        )
        if res.status_code == 200:
            return clean_markdown(res.json()['choices'][0]['message']['content'])
    except Exception as e:
        logger.error(f"Qwen error: {e}")
    return None

def send_condensed_report(data, qwen_analysis=None):
    rec = data.get('recommendation', {})
    action = rec.get('action', 'انتظار')
    
    if 'شراء' in action:
        emoji = "🟢"
    elif 'بيع' in action:
        emoji = "🔴"
    else:
        emoji = "⚪"
    
    ai_reason = qwen_analysis if qwen_analysis else "توافق المؤشرات الكمية مع منطقة سعرية مناسبة."
    if len(ai_reason) > 120:
        ai_reason = ai_reason[:117] + "..."
        
    dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
    
    msg = f"""{emoji} **{data['symbol']} ({data['interval'].upper()})**
💰 **السعر:** `${data['price']:,.2f}`
🎯 **التوصية:** {action} (Score: {rec.get('score', 0)})
⚖️ **العائد/المخاطرة:** 1:{data['rr']:.2f}

📌 **مستويات التداول:**
🛑 SL: `${data['sl']:,.2f}`
🎯 TP1: `${data['tp1']:,.2f}` | TP2: `${data['tp2']:,.2f}`

🧠 **السبب الرئيسي:**
{ai_reason}
📊 المنطقة: {data.get('smc_pd', 'N/A')} | Sharpe: {data['stochastic'].get('sharpe_ratio', 0):.2f}

🔗 للتفاصيل الكاملة: {dashboard_url}/dashboard
⚠️ _هذه ليست نصيحة مالية._"""
    
    send_message(msg)

def smart_analysis(symbol, interval="1h"):
    send_message(f"⏳ جاري تحليل {symbol}...")
    data = unified_analysis(symbol, interval)
    if not data:
        send_message(f"❌ فشل تحليل {symbol}. تأكد من صحة الرمز.")
        return
    
    qwen_ai = ask_qwen(data)
    
    if generate_signal_image:
        try:
            image_bytes = generate_signal_image(data)
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            files = {'photo': ('signal.png', image_bytes, 'image/png')}
            requests.post(url, files=files, data={'chat_id': CHAT_ID, 'caption': f"📊 {symbol}"}, timeout=30)
        except Exception as e:
            logger.error(f"Image error: {e}")
    
    send_condensed_report(data, qwen_ai)
    
    if log_trade:
        rec = data.get('recommendation', {})
        log_trade(
            symbol=data['symbol'],
            action=rec.get('action', ''),
            price=data['price'],
            score=rec.get('score', 0),
            tp1=data.get('tp1', 0),
            tp2=data.get('tp2', 0),
            sl=data.get('sl', 0)
        )

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start':
        dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
        send_message(f"""🌍 **بوت التداول الكمي المتقدم**
معادلات تفاضلية + Stochastic Calculus + Smart Money

الأوامر:
/smart <رمز> [تايم_فريم] (مثال: /smart BTC-USD 4h)
/multi <رمز> (مقارنة الفريمات)
/stats (إحصائيات البوت)
/dashboard (لوحة التحكم)

🔗 {dashboard_url}/dashboard""")
    elif cmd == '/stats':
        if get_performance:
            stats = get_performance()
            send_message(f"📊 الإجمالي: {stats.get('total', 0)}\n✅ الرابحة: {stats.get('wins', 0)}\n❌ الخاسرة: {stats.get('losses', 0)}\n📈 النجاح: {stats.get('win_rate', 0)}%")
        else:
            send_message("⚠️ إحصائيات غير متاحة")
    elif cmd == '/dashboard':
        send_message(f"🔗 {os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')}/dashboard")
    elif cmd.startswith('/multi'):
        parts = command.split(' ')
        if len(parts) < 2:
            return send_message("الاستخدام: /multi <رمز>")
        send_message(f"⏳ جاري مقارنة الفريمات لـ {parts[1]}...")
        send_message("✅ ميزة /multi قيد التطوير لتعرض ملخصاً سريعاً للفريمات.")
    elif cmd.startswith('/smart'):
        parts = command.split(' ')
        if len(parts) < 2:
            return send_message("الاستخدام: /smart <رمز> [تايم_فريم]")
        symbol = parts[1].strip()
        interval = parts[2].strip().lower() if len(parts) > 2 else "4h"
        if interval not in ['15m', '1h', '4h', '1d']:
            interval = "4h"
        smart_analysis(symbol, interval)
    elif cmd == '/help':
        send_message("""الأوامر:
/smart <رمز> [15m, 1h, 4h, 1d]
/multi <رمز>
/stats
/dashboard
/help""")

# ✅ دالة listen المُصححة - تحل مشكلة التكرار
def listen():
    last_id = 0
    logger.info("🤖 Bot started...")
    
    # ✅ احصل على آخر update_id لتجنب تكرار الرسائل القديمة
    try:
        resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", timeout=10)
        if resp.status_code == 200 and resp.json().get('ok'):
            updates = resp.json()['result']
            if updates:
                last_id = updates[-1]['update_id']
                logger.info(f"✅ Starting from update_id: {last_id}")
    except Exception as e:
        logger.error(f"Error getting last update: {e}")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_id + 1, "timeout": 10}
            resp = requests.get(url, params=params, timeout=15)
            
            if resp.status_code == 200 and resp.json().get('ok'):
                updates = resp.json()['result']
                
                for update in updates:
                    if 'message' in update and 'text' in update['message']:
                        incoming_chat_id = str(update['message']['chat']['id']).strip()
                        if incoming_chat_id == str(CHAT_ID).strip():
                            logger.info(f"📩 Processing: {update['message']['text']}")
                            handle_command(update['message']['text'])
                    
                    # ✅ تحديث last_id بعد كل update
                    last_id = update['update_id']
                    
        except Exception as e:
            logger.error(f"Listen error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    logger.info("🧮 Starting QUANT Trading Bot - Condensed Edition...")
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
