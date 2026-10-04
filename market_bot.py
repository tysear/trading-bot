import requests
import time
import schedule
import yfinance as yf
from datetime import datetime
import logging
import os
from flask import Flask
import threading
import numpy as np
from scipy import stats
from scipy.stats import linregress

# ========== إعدادات السجل ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== الإعدادات الرئيسية ==========
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8917209003:AAFEDVugxuj6LEzELv8NtkoCav5Zwqn8f_E")
CHAT_ID = os.environ.get("CHAT_ID", "1814016230")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

if GROQ_API_KEY:
    logger.info("✅ Groq AI مفعّل")
if OPENROUTER_API_KEY:
    logger.info("✅ OpenRouter (Qwen) مفعّل")

LAST_UPDATE_ID = 0

CRYPTO_LIST = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT', 'DOGEUSDT']
STOCKS_LIST = ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'META']
METALS_LIST = ['GC=F', 'SI=F']
FOREX_LIST = ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X']

# ========== Flask Server ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 بوت التداول الذكي يعمل بنجاح!"

@app.route('/health')
def health():
    return {"status": "ok", "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ========== جلب البيانات ==========
def fetch_crypto(symbol):
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': '1d', 'limit': 100}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        closes = [float(candle[4]) for candle in data]
        highs = [float(candle[2]) for candle in data]
        lows = [float(candle[3]) for candle in data]
        return {'closes': closes, 'highs': highs, 'lows': lows}
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None

def fetch_stock(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty:
            return None
        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        return {'closes': closes, 'highs': highs, 'lows': lows}
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None

# ========== المؤشرات الفنية ==========
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(-period, 0):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calc_macd(prices):
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    return ema12 - ema26

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        return prices[-1], prices[-1]
    recent = prices[-period:]
    sma = sum(recent) / period
    std = (sum((p - sma) ** 2 for p in recent) / period) ** 0.5
    return sma + (std * 2), sma - (std * 2)

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02
    true_ranges = []
    for i in range(-period, 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        true_ranges.append(tr)
    return sum(true_ranges) / period

def calc_sl_tp(entry_price, atr, direction='buy'):
    if direction == 'buy':
        sl = entry_price - (atr * 1.5)
        tp1 = entry_price + (atr * 2)
        tp2 = entry_price + (atr * 3)
        tp3 = entry_price + (atr * 4.5)
    else:
        sl = entry_price + (atr * 1.5)
        tp1 = entry_price - (atr * 2)
        tp2 = entry_price - (atr * 3)
        tp3 = entry_price - (atr * 4.5)
    return sl, tp1, tp2, tp3

# ========== التحليل ==========
def analyze_asset(symbol, asset_type):
    if asset_type == 'crypto':
        data = fetch_crypto(symbol)
    else:
        data = fetch_stock(symbol)
    
    if not data:
        return None
    
    closes = data['closes']
    highs = data['highs']
    lows = data['lows']
    
    price = closes[-1]
    prev_price = closes[-2]
    
    rsi = calc_rsi(closes)
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    macd = calc_macd(closes)
    bb_upper, bb_lower = calc_bollinger(closes)
    atr = calc_atr(highs, lows, closes)
    
    p1 = price - prev_price
    p2 = p1 - (prev_price - closes[-3])
    
    score = 0
    signals = []
    
    if rsi < 30:
        score += 2
        signals.append(f"✅ RSI={rsi:.1f} تشبع بيعي")
    elif rsi > 70:
        score -= 2
        signals.append(f"⚠️ RSI={rsi:.1f} تشبع شرائي")
    else:
        signals.append(f"⚪ RSI={rsi:.1f} محايد")
    
    if price > ema20 > ema50:
        score += 2
        signals.append("✅ السعر فوق EMA20 و EMA50")
    elif price < ema20 < ema50:
        score -= 2
        signals.append(" السعر تحت EMA20 و EMA50")
    else:
        signals.append("⚪ EMA محايد")
    
    if macd > 0:
        score += 1
        signals.append("✅ MACD إيجابي")
    else:
        score -= 1
        signals.append("❌ MACD سلبي")
    
    if price <= bb_lower:
        score += 2
        signals.append("✅ السعر عند الحد السفلي BB")
    elif price >= bb_upper:
        score -= 2
        signals.append("❌ السعر عند الحد العلوي BB")
    
    if p1 > 0 and p2 > 0:
        score += 1
        signals.append("✅ تسارع صعودي")
    elif p1 < 0 and p2 < 0:
        score -= 1
        signals.append(" تسارع هبوطي")
    elif p1 < 0 and p2 > 0:
        score += 1
        signals.append("🔄 تباطؤ الهبوط")
    
    if score >= 5:
        rec = "🟢 شراء قوي جداً"
        direction = 'buy'
    elif score >= 3:
        rec = "🟢 شراء"
        direction = 'buy'
    elif score <= -5:
        rec = "🔴🔴 بيع قوي جداً"
        direction = 'sell'
    elif score <= -3:
        rec = "🔴 بيع"
        direction = 'sell'
    else:
        rec = "⚪ انتظار"
        direction = None
    
    sl, tp1, tp2, tp3 = calc_sl_tp(price, atr, direction if direction else 'buy')
    risk = abs(price - sl)
    reward = abs(tp2 - price)
    rr_ratio = reward / risk if risk > 0 else 0
    
    return {
        'symbol': symbol, 'type': asset_type, 'price': price, 'rsi': rsi,
        'ema20': ema20, 'score': score, 'recommendation': rec,
        'direction': direction, 'signals': signals,
        'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'rr_ratio': rr_ratio
    }

# ========== نظام التحليل الموحد ==========
def unified_analysis(symbol, asset_type, balance=10000):
    """تحليل شامل يجمع كل المعادلات"""
    
    if asset_type == 'crypto':
        data = fetch_crypto(symbol)
    else:
        data = fetch_stock(symbol)
    
    if not data or len(data['closes']) < 100:
        return None
    
    closes = np.array(data['closes'])
    highs = np.array(data['highs'])
    lows = np.array(data['lows'])
    current_price = closes[-1]
    
    rsi = calc_rsi(closes)
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    macd = calc_macd(closes)
    atr = calc_atr(highs, lows, closes)
    
    returns = np.diff(np.log(closes))
    spread_lag = closes[:-1]
    spread_diff = np.diff(closes)
    
    slope, intercept, r_value, p_value, std_err = linregress(spread_lag[-50:], spread_diff[-50:])
    
    ou_theta = -slope if slope < 0 else 0.01
    ou_mu = np.mean(closes[-100:])
    ou_sigma = np.std(returns[-100:]) * np.sqrt(252)
    ou_half_life = np.log(2) / ou_theta if ou_theta > 0 else 999
    ou_z_score = (current_price - ou_mu) / (ou_sigma * current_price)
    
    wins = 0
    total = 0
    for i in range(-50, -5):
        if closes[i+5] > closes[i]:
            wins += 1
        total += 1
    
    win_rate = wins / total if total > 0 else 0.5
    
    if current_price > ema20:
        win_loss_ratio = 2.0
    else:
        win_loss_ratio = 1.5
    
    kelly_edge = (win_rate * win_loss_ratio) - (1 - win_rate)
    kelly_full = kelly_edge / win_loss_ratio if win_loss_ratio > 0 else 0
    kelly_half = min(kelly_full / 2, 0.25)
    kelly_quarter = kelly_full / 4
    
    mu_return = np.mean(returns)
    sigma_return = np.std(returns)
    
    np.random.seed(42)
    simulations = 1000
    days = 30
    simulated = np.zeros((simulations, days + 1))
    simulated[:, 0] = current_price
    
    for t in range(1, days + 1):
        random_returns = np.random.normal(mu_return, sigma_return, simulations)
        simulated[:, t] = simulated[:, t-1] * np.exp(random_returns)
    
    final_prices = simulated[:, -1]
    prob_profit = np.sum(final_prices > current_price) / simulations * 100
    prob_loss_10 = np.sum(final_prices < current_price * 0.9) / simulations * 100
    prob_gain_10 = np.sum(final_prices > current_price * 1.1) / simulations * 100
    percentile_5 = np.percentile(final_prices, 5)
    percentile_95 = np.percentile(final_prices, 95)
    
    vol_current = np.std(returns[-20:]) * np.sqrt(252)
    vol_long_term = np.std(returns) * np.sqrt(252)
    
    risk_amount = balance * kelly_half
    sl_distance = atr * 1.5
    position_size = risk_amount / sl_distance if sl_distance > 0 else 0
    notional_value = position_size * current_price
    
    direction = 'buy' if current_price > ema20 and rsi < 70 else 'sell'
    
    if direction == 'buy':
        sl = current_price - (atr * 1.5)
        tp1 = current_price + (atr * 2)
        tp2 = current_price + (atr * 3)
        tp3 = current_price + (atr * 4.5)
    else:
        sl = current_price + (atr * 1.5)
        tp1 = current_price - (atr * 2)
        tp2 = current_price - (atr * 3)
        tp3 = current_price - (atr * 4.5)
    
    risk_reward = abs(tp2 - current_price) / abs(current_price - sl) if abs(current_price - sl) > 0 else 0
    
    score = 0
    if rsi < 30: score += 3
    elif rsi < 40: score += 2
    elif rsi > 70: score -= 3
    elif rsi > 60: score -= 1
    
    if current_price > ema20 > ema50: score += 3
    elif current_price < ema20 < ema50: score -= 3
    
    if macd > 0: score += 2
    else: score -= 2
    
    if ou_z_score < -2: score += 3
    elif ou_z_score > 2: score -= 3
    
    if prob_profit > 60: score += 2
    elif prob_profit < 40: score -= 2
    
    if kelly_edge > 0.1: score += 2
    elif kelly_edge < 0: score -= 3
    
    if score >= 8:
        recommendation = "🟢🟢 شراء قوي جداً"
        confidence = "عالية جداً"
    elif score >= 5:
        recommendation = " شراء"
        confidence = "عالية"
    elif score >= 2:
        recommendation = "🟡 شراء حذر"
        confidence = "متوسطة"
    elif score <= -8:
        recommendation = "🔴🔴 بيع قوي جداً"
        confidence = "عالية جداً"
    elif score <= -5:
        recommendation = "🔴 بيع"
        confidence = "عالية"
    elif score <= -2:
        recommendation = "🟡 بيع حذر"
        confidence = "متوسطة"
    else:
        recommendation = "⚪ انتظار"
        confidence = "منخفضة"
    
    return {
        'symbol': symbol, 'type': asset_type, 'price': current_price,
        'rsi': rsi, 'ema20': ema20, 'ema50': ema50, 'macd': macd, 'atr': atr,
        'direction': direction, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
        'risk_reward': risk_reward, 'ou_theta': ou_theta, 'ou_mu': ou_mu,
        'ou_z_score': ou_z_score, 'ou_half_life': ou_half_life,
        'win_rate': win_rate, 'kelly_edge': kelly_edge, 'kelly_full': kelly_full,
        'kelly_half': kelly_half, 'kelly_quarter': kelly_quarter,
        'position_size': position_size, 'notional_value': notional_value,
        'risk_amount': risk_amount, 'prob_profit': prob_profit,
        'prob_loss_10': prob_loss_10, 'prob_gain_10': prob_gain_10,
        'percentile_5': percentile_5, 'percentile_95': percentile_95,
        'vol_current': vol_current, 'vol_long_term': vol_long_term,
        'score': score, 'recommendation': recommendation, 'confidence': confidence
    }

def format_unified_analysis(result):
    msg = f"""
 <b>تحليل شامل لـ {result['symbol']}</b>
━━━━━━━━━━━━━━━━━━━━

💰 <b>السعر الحالي:</b> ${result['price']:,.2f}

📊 <b>التحليل الفني:</b>
• RSI: {result['rsi']:.1f}
• EMA20: ${result['ema20']:,.2f}
• EMA50: ${result['ema50']:,.2f}
• MACD: {result['macd']:.4f}
• ATR: ${result['atr']:.2f}

🧮 <b>نموذج Ornstein-Uhlenbeck:</b>
• المتوسط طويل الأجل: ${result['ou_mu']:,.2f}
• سرعة العودة (θ): {result['ou_theta']:.4f}
• عمر النصف: {result['ou_half_life']:.1f} يوم
• Z-Score: {result['ou_z_score']:.2f}

🎲 <b>معادلة Kelly Criterion:</b>
• نسبة النجاح: {result['win_rate']*100:.1f}%
• Edge: {result['kelly_edge']*100:.2f}%
• Kelly الآمن (Half): {result['kelly_half']*100:.2f}% ⭐

🎲 <b>محاكاة Monte Carlo (30 يوم):</b>
• احتمال الربح: {result['prob_profit']:.1f}%
• احتمال خسارة 10%+: {result['prob_loss_10']:.1f}%
• نطاق الثقة 90%: ${result['percentile_5']:,.2f} - ${result['percentile_95']:,.2f}

📈 <b>نموذج Heston (التقلب):</b>
• التقلب الحالي: {result['vol_current']*100:.2f}%
• التقلب طويل الأجل: {result['vol_long_term']*100:.2f}%

━━━━━━━━━━━━━━━━━━━━
{result['recommendation']}
الثقة: {result['confidence']} | النقاط: {result['score']}

💰 <b>تفاصيل الصفقة (Half Kelly):</b>
• الاتجاه: {result['direction'].upper()}
• حجم الصفقة: {result['position_size']:.4f} وحدة
• القيمة: ${result['notional_value']:,.2f}
• المخاطرة: ${result['risk_amount']:,.2f}

🛑 <b>إدارة المخاطر:</b>
• SL: ${result['sl']:,.2f}
• TP1: ${result['tp1']:,.2f}
• TP2: ${result['tp2']:,.2f}
• TP3: ${result['tp3']:,.2f}
• R:R = 1:{result['risk_reward']:.2f}

️ <i>هذا ليس نصيحة مالية.</i>
"""
    return msg

# ========== إرسال الرسائل ==========
def send_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

def send_urgent_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": f" <b>تنبيه عاجل!</b>\n\n{message}", "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Error sending alert: {e}")
        return False

def format_signal(r):
    msg = f"<b> {r['symbol']}</b> ({r['type']})\n"
    msg += f"💰 السعر: ${r['price']:.2f}\n"
    msg += f"📈 RSI: {r['rsi']:.1f}\n"
    msg += f"🎯 التوصية: {r['recommendation']}\n"
    msg += f"📊 النقاط: {r['score']}\n\n"
    if r['direction']:
        msg += f"🛑 وقف الخسارة: ${r['sl']:.2f}\n"
        msg += f"🎯 الهدف 1: ${r['tp1']:.2f}\n"
        msg += f"🎯 الهدف 2: ${r['tp2']:.2f}\n"
        msg += f"🎯 الهدف 3: ${r['tp3']:.2f}\n"
        msg += f"⚖️ المخاطرة/العائد: 1:{r['rr_ratio']:.1f}\n\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    return msg

# ========== التحليل الشامل ==========
def analyze_all():
    logger.info("بدء التحليل الشامل...")
    message = f"🤖 <b>تقرير الأسواق الشامل</b>\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    all_results = []
    
    for symbol in CRYPTO_LIST:
        result = analyze_asset(symbol, 'crypto')
        if result: all_results.append(result)
        time.sleep(0.5)
    for symbol in STOCKS_LIST:
        result = analyze_asset(symbol, 'stock')
        if result: all_results.append(result)
        time.sleep(0.5)
    for symbol in METALS_LIST:
        result = analyze_asset(symbol, 'metal')
        if result: all_results.append(result)
        time.sleep(0.5)
    for symbol in FOREX_LIST:
        result = analyze_asset(symbol, 'forex')
        if result: all_results.append(result)
        time.sleep(0.5)
    
    all_results.sort(key=lambda x: x['score'], reverse=True)
    
    buys = [r for r in all_results if r['score'] >= 3]
    if buys:
        message += "🟢 <b>فرص الشراء:</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in buys[:5]: message += format_signal(r)
    
    sells = [r for r in all_results if r['score'] <= -3]
    if sells:
        message += "\n🔴 <b>فرص البيع:</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in sells[:5]: message += format_signal(r)
    
    waits = [r for r in all_results if -3 < r['score'] < 3]
    if waits:
        message += "\n <b>انتظار:</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in waits[:7]:
            message += f"• <b>{r['symbol']}</b> ({r['type']})\n  السعر: ${r['price']:.2f} | RSI: {r['rsi']:.1f}\n\n"
    
    message += "\n⚠️ <i>هذا ليس نصيحة مالية. تداول بمسؤوليتك.</i>"
    send_message(message)
    logger.info("تم إرسال التقرير الشامل!")

def check_urgent_signals():
    logger.info("فحص التنبيهات العاجلة...")
    for symbol in CRYPTO_LIST[:3]:
        result = analyze_asset(symbol, 'crypto')
        if result:
            if result['rsi'] < 25:
                msg = f"<b>{result['symbol']}</b>\nRSI = {result['rsi']:.1f} (تشبع بيعي قوي!)\nالسعر: ${result['price']:.2f}\n\n فرصة شراء قوية!"
                send_urgent_alert(msg)
            elif result['rsi'] > 75:
                msg = f"<b>{result['symbol']}</b>\nRSI = {result['rsi']:.1f} (تشبع شرائي قوي!)\nالسعر: ${result['price']:.2f}\n\n🔴 فرصة بيع قوية!"
                send_urgent_alert(msg)
        time.sleep(0.5)

# ========== الذكاء الاصطناعي ==========
def ask_groq(question, context=""):
    if not GROQ_API_KEY:
        return "❌ Groq غير مفعّل"
    
    try:
        prompt = f"""أنت مساعد تداول ذكي محترف. أجب باللغة العربية بشكل واضح ومختصر.

{context}

سؤال المستخدم: {question}

⚠️ مهم: اذكر دائماً أن هذا ليس نصيحة مالية."""
        
        url = GROQ_API_URL
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openai/gpt-oss-120b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1024
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()
        
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        else:
            return f"❌ خطأ Groq: {data.get('error', {}).get('message', 'غير معروف')}"
    except Exception as e:
        return f"❌ خطأ Groq: {str(e)}"

def ask_qwen(question, context=""):
    if not OPENROUTER_API_KEY:
        return "❌ OpenRouter غير مفعّل"
    
    try:
        prompt = f"""أنت محلل مالي خبير. أجب باللغة العربية بشكل مفصل.

{context}

سؤال المستخدم: {question}

⚠️ مهم: اذكر دائماً أن هذا ليس نصيحة مالية."""
        
        url = OPENROUTER_API_URL
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/tysear/trading-bot",
            "X-Title": "Trading Bot"
        }
        payload = {
            "model": "qwen/qwen-2.5-72b-instruct",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 4096
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        data = response.json()
        
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        else:
            return f"❌ خطأ Qwen: {data.get('error', {}).get('message', 'غير معروف')}"
    except Exception as e:
        return f"❌ خطأ Qwen: {str(e)}"

def ask_ai(question, context="", model_preference="auto"):
    if model_preference == "auto":
        if len(question) < 100:
            model = "groq"
        else:
            model = "qwen"
    else:
        model = model_preference
    
    if model == "qwen":
        try:
            answer = ask_qwen(question, context)
            if not answer.startswith("❌"):
                return answer
        except Exception as e:
            logger.error(f"Qwen failed: {e}")
    
    if model in ["groq", "auto"]:
        try:
            answer = ask_groq(question, context)
            if not answer.startswith("❌"):
                return answer
        except Exception as e:
            logger.error(f"Groq failed: {e}")
    
    return " عذراً، لم أتمكن من الحصول على إجابة."

# ========== معالجة الأوامر ==========
def handle_command(command):
    cmd = command.strip().lower()
    
    if cmd == '/start':
        msg = """
🤖 <b>مرحباً بك في بوت التداول الذكي!</b>

<b>📊 الأوامر المتاحة:</b>
/report - تقرير شامل
/crypto - العملات فقط
/stocks - الأسهم فقط
/metals - المعادن
/forex - الفوركس
/urgent - تنبيهات عاجلة

<b>🧠 الذكاء الاصطناعي:</b>
/ask <سؤال> - سؤال تلقائي
/qwen <سؤال> - Qwen 2.5 72B
/ai <نموذج> <سؤال> - اختر النموذج

<b>🎯 التحليل المتقدم:</b>
/smart <رمز> - تحليل شامل مع كل المعادلات

/help - المساعدة
"""
        send_message(msg)
    
    elif cmd == '/report':
        send_message(" جاري إعداد التقرير...")
        analyze_all()
    
    elif cmd == '/crypto':
        send_message("⏳ جاري تحليل العملات...")
        message = "🪙 <b>تقرير العملات</b>\n\n"
        for symbol in CRYPTO_LIST:
            result = analyze_asset(symbol, 'crypto')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/stocks':
        send_message("⏳ جاري تحليل الأسهم...")
        message = "📈 <b>تقرير الأسهم</b>\n\n"
        for symbol in STOCKS_LIST:
            result = analyze_asset(symbol, 'stock')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/metals':
        send_message("⏳ جاري تحليل المعادن...")
        message = "🥇 <b>تقرير المعادن</b>\n\n"
        for symbol in METALS_LIST:
            result = analyze_asset(symbol, 'metal')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/forex':
        send_message("⏳ جاري تحليل الفوركس...")
        message = "💱 <b>تقرير الفوركس</b>\n\n"
        for symbol in FOREX_LIST:
            result = analyze_asset(symbol, 'forex')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/urgent':
        send_message("🔍 جاري الفحص...")
        check_urgent_signals()
        send_message("✅ تم الفحص!")
    
    elif cmd.startswith('/ask '):
        question = command[5:].strip()
        if not question:
            send_message("❌ يرجى كتابة سؤال بعد /ask")
            return
        send_message(" جاري التفكير...")
        answer = ask_ai(question)
        send_message(f" <b>الإجابة:</b>\n\n{answer}")
    
    elif cmd.startswith('/qwen '):
        question = command[6:].strip()
        if not question:
            send_message("❌ يرجى كتابة سؤال بعد /qwen")
            return
        send_message("🧠 جاري التفكير مع Qwen...")
        answer = ask_qwen(question)
        send_message(f"🤖 <b>Qwen 2.5 72B:</b>\n\n{answer}")
    
    elif cmd.startswith('/ai '):
        parts = command.split(maxsplit=2)
        if len(parts) < 3:
            send_message("""
<b>اختر النموذج:</b>
/ai groq <سؤال>
/ai qwen <سؤال>
""")
            return
        model = parts[1].lower()
        question = parts[2]
        if model not in ['groq', 'qwen']:
            send_message("❌ النماذج: groq, qwen")
            return
        send_message(f"🧠 جاري التفكير مع {model.upper()}...")
        answer = ask_ai(question, model_preference=model)
        send_message(f" <b>{model.upper()}:</b>\n\n{answer}")
    
    elif cmd.startswith('/smart'):
        parts = command.split()
        if len(parts) < 2:
            send_message("""
🎯 <b>التحليل الشامل الموحد:</b>

/smart <رمز> - تحليل كامل مع كل المعادلات

أمثلة:
/smart BTCUSDT
/smart AAPL
/smart GC=F

يجمع: RSI + EMA + MACD + OU + Kelly + Monte Carlo + Heston
""")
            return
        
        symbol = parts[1].upper()
        
        if symbol in CRYPTO_LIST:
            asset_type = 'crypto'
        elif symbol in STOCKS_LIST:
            asset_type = 'stock'
        elif symbol in METALS_LIST:
            asset_type = 'metal'
        elif symbol in FOREX_LIST:
            asset_type = 'forex'
        else:
            asset_type = 'stock'
        
        send_message(f" جاري التحليل الشامل لـ {symbol}...")
        
        result = unified_analysis(symbol, asset_type)
        
        if not result:
            send_message(f"❌ فشل في تحليل {symbol}")
            return
        
        msg = format_unified_analysis(result)
        send_message(msg)
        
        send_message("🧠 جاري التحليل الذكي...")
        
        context = f"""
بيانات {result['symbol']}:
- السعر: ${result['price']:,.2f}
- RSI: {result['rsi']:.1f}
- OU Z-Score: {result['ou_z_score']:.2f}
- Kelly Half: {result['kelly_half']*100:.2f}%
- احتمال الربح: {result['prob_profit']:.1f}%
- التقلب: {result['vol_current']*100:.2f}%
- التوصية: {result['recommendation']}
- النقاط: {result['score']}
"""
        
        question = f"""بناءً على هذه المعادلات:
1. هل الصفقة موصى بها؟
2. ما المخاطر الرئيسية؟
3. ما نصيحتك النهائية؟"""
        
        ai_answer = ask_qwen(question, context)
        send_message(f"🤖 <b>تحليل Qwen:</b>\n\n{ai_answer}")
    
    elif cmd == '/help':
        msg = """
📚 <b>دليل البوت:</b>

📊 التقارير:
/report, /crypto, /stocks, /metals, /forex

🧠 الذكاء الاصطناعي:
/ask, /qwen, /ai

🎯 التحليل المتقدم:
/smart <رمز> - تحليل شامل

🔔 التنبيهات:
/urgent

️ التداول ينطوي على مخاطر.
"""
        send_message(msg)
    
    else:
        if OPENROUTER_API_KEY or GROQ_API_KEY:
            send_message("🧠 جاري التفكير...")
            answer = ask_ai(command)
            send_message(f"🤖 {answer}")
        else:
            send_message("❓ الأوامر: /start, /help")

# ========== الاستماع للأوامر ==========
def listen_for_commands():
    global LAST_UPDATE_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {'offset': LAST_UPDATE_ID + 1, 'timeout': 10}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get('ok') and data.get('result'):
            for update in data['result']:
                update_id = update['update_id']
                if 'message' in update and 'text' in update['message']:
                    text = update['message']['text'].strip()
                    chat_id = str(update['message']['chat']['id'])
                    
                    if chat_id == CHAT_ID:
                        if text.startswith('/'):
                            handle_command(text)
                        else:
                            if OPENROUTER_API_KEY or GROQ_API_KEY:
                                send_message("🧠 جاري التفكير...")
                                answer = ask_ai(text)
                                send_message(f"🤖 {answer}")
                            else:
                                send_message("💬 استخدم /help")
                        logger.info(f"تم معالجة: {text[:50]}")
                
                LAST_UPDATE_ID = max(LAST_UPDATE_ID, update_id)
    except Exception as e:
        logger.error(f"Error listening: {e}")

# ========== الجدولة ==========
def scheduled_tasks():
    schedule.every(6).hours.do(analyze_all)
    schedule.every().day.at("09:00").do(analyze_all)
    schedule.every().day.at("15:00").do(analyze_all)
    schedule.every().day.at("21:00").do(analyze_all)
    schedule.every(1).hours.do(check_urgent_signals)
    schedule.every(10).seconds.do(listen_for_commands)
    logger.info("✅ تم جدولة جميع المهام!")

# ========== نقطة البداية ==========
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 بوت التداول الذكي - Qwen + Groq")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Groq: {'✅' if GROQ_API_KEY else '❌'}")
    print(f"🧠 OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌'}")
    print("\n✅ البوت جاهز!")
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server يعمل")
    
    scheduled_tasks()
    logger.info("تشغيل تقرير أولي...")
    analyze_all()
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت")
    except Exception as e:
        logger.error(f"خطأ: {e}")
        send_message(f"❌ خطأ: {str(e)}")
