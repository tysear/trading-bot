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
from scipy.stats import linregress

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8917209003:AAFEDVugxuj6LEzELv8NtkoCav5Zwqn8f_E")
CHAT_ID = os.environ.get("CHAT_ID", "1814016230")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

LAST_UPDATE_ID = 0
CRYPTO_LIST = ['BTC-USD', 'ETH-USD', 'XRP-USD', 'SOL-USD', 'BNB-USD', 'ADA-USD', 'DOGE-USD']
STOCKS_LIST = ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'META']
METALS_LIST = ['GC=F', 'SI=F']
FOREX_LIST = ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X']

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running perfectly!"
@app.route('/health')
def health(): return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def fetch_crypto(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty: return None
        return {'closes': hist['Close'].tolist(), 'highs': hist['High'].tolist(), 
                'lows': hist['Low'].tolist(), 'volumes': hist['Volume'].tolist()}
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None

def fetch_stock(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty: return None
        return {'closes': hist['Close'].tolist(), 'highs': hist['High'].tolist(), 
                'lows': hist['Low'].tolist(), 'volumes': hist['Volume'].tolist()}
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None

def fetch_news(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news or len(news) == 0:
            return ["لا توجد أخبار حديثة متاحة."]
        news_list = []
        for item in news[:3]:
            title = item.get('title', 'بدون عنوان')
            publisher = item.get('publisher', 'مجهول')
            news_list.append(f"• {title} ({publisher})")
        return news_list
    except Exception as e:
        logger.error(f"Error fetching news for {symbol}: {e}")
        return ["تعذر جلب الأخبار."]

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    gains, losses = [], []
    for i in range(-period, 0):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0)); losses.append(max(-change, 0))
    avg_gain, avg_loss = sum(gains)/period, sum(losses)/period
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calc_ema(prices, period):
    if len(prices) < period: return prices[-1]
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]: ema = (price - ema) * multiplier + ema
    return ema

def calc_macd(prices): return calc_ema(prices, 12) - calc_ema(prices, 26)

def calc_bollinger(prices, period=20):
    if len(prices) < period: return prices[-1], prices[-1]
    recent = prices[-period:]
    sma = sum(recent) / period
    std = (sum((p - sma) ** 2 for p in recent) / period) ** 0.5
    return sma + (std * 2), sma - (std * 2)

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return closes[-1] * 0.02
    true_ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(-period, 0)]
    return sum(true_ranges) / period

def analyze_asset(symbol, asset_type):
    data = fetch_crypto(symbol) if asset_type == 'crypto' else fetch_stock(symbol)
    if not data: return None
    closes, highs, lows = data['closes'], data['highs'], data['lows']
    price, prev_price = closes[-1], closes[-2]
    rsi, ema20, ema50 = calc_rsi(closes), calc_ema(closes, 20), calc_ema(closes, 50)
    macd, atr = calc_macd(closes), calc_atr(highs, lows, closes)
    
    score, signals = 0, []
    if rsi < 30: score += 2; signals.append("RSI تشبع بيعي")
    elif rsi > 70: score -= 2; signals.append("RSI تشبع شرائي")
    
    if price > ema20 > ema50: score += 2; signals.append("سعر فوق EMA")
    elif price < ema20 < ema50: score -= 2; signals.append("سعر تحت EMA")
    
    if macd > 0: score += 1; signals.append("MACD إيجابي")
    else: score -= 1; signals.append("MACD سلبي")
    
    direction = 'buy' if score >= 2 else ('sell' if score <= -2 else None)
    if direction == 'buy':
        sl, tp1, tp2, tp3 = price - (atr*1.5), price + (atr*2), price + (atr*3), price + (atr*4.5)
    elif direction == 'sell':
        sl, tp1, tp2, tp3 = price + (atr*1.5), price - (atr*2), price - (atr*3), price - (atr*4.5)
    else:
        sl, tp1, tp2, tp3 = price - (atr*1.5), price + (atr*2), price + (atr*3), price + (atr*4.5)
        
    rr = abs(tp2 - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    rec = "Strong Buy" if score >= 5 else ("Buy" if score >= 3 else ("Strong Sell" if score <= -5 else ("Sell" if score <= -3 else "Wait")))
    
    return {'symbol': symbol, 'type': asset_type, 'price': price, 'rsi': rsi, 'ema20': ema20, 
            'score': score, 'recommendation': rec, 'direction': direction, 'sl': sl, 
            'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'rr_ratio': rr}

def unified_analysis(symbol, asset_type, balance=10000):
    try:
        data = fetch_crypto(symbol) if asset_type == 'crypto' else fetch_stock(symbol)
        if not data or len(data['closes']) < 50: return None
        
        closes = np.array(data['closes'], dtype=float)
        highs, lows = np.array(data['highs'], dtype=float), np.array(data['lows'], dtype=float)
        volumes = data.get('volumes', [])
        news = fetch_news(symbol)
        current_price = float(closes[-1])
        
        rsi, ema20, ema50 = calc_rsi(closes), calc_ema(closes, 20), calc_ema(closes, 50)
        macd, atr = calc_macd(closes), calc_atr(highs, lows, closes)
        
        # Ornstein-Uhlenbeck
        returns = np.diff(np.log(closes))
        lookback = min(50, len(closes) - 1)
        slope, intercept, r_val, p_val, std_err = linregress(closes[-lookback-1:-1], np.diff(closes[-lookback-1:]))
        ou_theta = float(-slope) if slope < 0 else 0.01
        ou_mu = float(np.mean(closes[-lookback:]))
        ou_sigma = float(np.std(returns[-lookback:])) * np.sqrt(252)
        ou_half_life = float(np.log(2) / ou_theta) if ou_theta > 0 else 999.0
        ou_z_score = float((current_price - ou_mu) / (ou_sigma * current_price)) if ou_sigma > 0 else 0.0
        
        # Kelly Criterion
        wins = sum(1 for i in range(-min(50, len(closes)-5), -5) if closes[i+5] > closes[i])
        total = min(50, len(closes) - 5)
        win_rate = float(wins / total) if total > 0 else 0.5
        win_loss_ratio = 2.0 if current_price > ema20 else 1.5
        kelly_edge = float((win_rate * win_loss_ratio) - (1 - win_rate))
        kelly_full = float(kelly_edge / win_loss_ratio) if win_loss_ratio > 0 else 0.0
        kelly_half = min(float(kelly_full / 2), 0.25)
        
        # Monte Carlo (Exact GBM / Milstein-level accuracy)
        mu_daily, sigma_daily = float(np.mean(returns)), float(np.std(returns))
        np.random.seed(42)
        simulated = np.zeros((1000, 31))
        simulated[:, 0] = current_price
        for t in range(1, 31):
            Z = np.random.normal(0, 1, 1000)
            simulated[:, t] = simulated[:, t-1] * np.exp(mu_daily + sigma_daily * Z)
        
        final_prices = simulated[:, -1]
        prob_profit = float(np.sum(final_prices > current_price) / 1000 * 100)
        prob_loss_10 = float(np.sum(final_prices < current_price * 0.9) / 1000 * 100)
        percentile_5, percentile_95 = float(np.percentile(final_prices, 5)), float(np.percentile(final_prices, 95))
        
        # Volume Analysis
        vol_msg = "محايد"
        if len(volumes) >= 20:
            recent_vol, avg_vol = np.mean(volumes[-5:]), np.mean(volumes[-20:])
            if recent_vol > avg_vol * 1.2: vol_msg = "مرتفع (تأكيد الاتجاه) 📈"
            elif recent_vol < avg_vol * 0.8: vol_msg = "منخفض (ضعف الاتجاه) 📉"
        
        # Risk & Position Sizing
        risk_amount = balance * kelly_half
        sl_dist = atr * 1.5
        position_size = risk_amount / sl_dist if sl_dist > 0 else 0.0
        
        direction = 'buy' if current_price > ema20 and rsi < 70 else 'sell'
        if direction == 'buy':
            sl, tp1, tp2, tp3 = current_price - (atr*1.5), current_price + (atr*2), current_price + (atr*3), current_price + (atr*4.5)
        else:
            sl, tp1, tp2, tp3 = current_price + (atr*1.5), current_price - (atr*2), current_price - (atr*3), current_price - (atr*4.5)
        rr = float(abs(tp2 - current_price) / abs(current_price - sl)) if abs(current_price - sl) > 0 else 0.0
        
        # Scoring
        score = 0
        if rsi < 30: score += 3
        elif rsi > 70: score -= 3
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
        
        if score >= 8: rec, conf = "Strong Buy 🟢", "Very High"
        elif score >= 5: rec, conf = "Buy 🟢", "High"
        elif score >= 2: rec, conf = "Cautious Buy 🟡", "Medium"
        elif score <= -8: rec, conf = "Strong Sell 🔴", "Very High"
        elif score <= -5: rec, conf = "Sell 🔴", "High"
        elif score <= -2: rec, conf = "Cautious Sell 🟡", "Medium"
        else: rec, conf = "Wait ⚪", "Low"
        
        return {
            'symbol': symbol, 'type': asset_type, 'price': current_price, 'rsi': rsi, 'ema20': ema20, 
            'ema50': ema50, 'macd': macd, 'atr': atr, 'direction': direction, 'sl': sl, 'tp1': tp1, 
            'tp2': tp2, 'tp3': tp3, 'risk_reward': rr, 'ou_theta': ou_theta, 'ou_mu': ou_mu, 
            'ou_z_score': ou_z_score, 'ou_half_life': ou_half_life, 'win_rate': win_rate, 
            'kelly_edge': kelly_edge, 'kelly_half': kelly_half, 'prob_profit': prob_profit, 
            'prob_loss_10': prob_loss_10, 'percentile_5': percentile_5, 'percentile_95': percentile_95,
            'vol_msg': vol_msg, 'position_size': position_size, 'risk_amount': risk_amount,
            'score': score, 'recommendation': rec, 'confidence': conf, 'news': news
        }
    except Exception as e:
        logger.error(f"Error in unified_analysis: {e}")
        return None

def format_unified_analysis(r):
    news_text = "\n".join(r.get('news', ['لا توجد أخبار']))
    return f"""
📊 <b>تحليل شامل متقدم لـ {r['symbol']}</b>
{'='*40}
💰 السعر: ${r['price']:,.2f} | RSI: {r['rsi']:.1f} | MACD: {r['macd']:.2f}
📈 حجم التداول: {r['vol_msg']}

📰 <b>آخر الأخبار المؤثرة:</b>
{news_text}

🧮 <b>نموذج OU (العودة للمتوسط):</b>
• المتوسط: ${r['ou_mu']:,.2f} | Z-Score: {r['ou_z_score']:.2f}
• عمر النصف: {r['ou_half_life']:.1f} يوم

🎲 <b>Kelly Criterion (إدارة رأس المال):</b>
• نسبة النجاح: {r['win_rate']*100:.1f}% | Edge: {r['kelly_edge']*100:.2f}%
• الحجم الآمن (Half Kelly): {r['kelly_half']*100:.2f}% 

🎲 <b>مونت كارلو (دقة Exact GBM/Milstein):</b>
• احتمال الربح: {r['prob_profit']:.1f}% | احتمال خسارة 10%+: {r['prob_loss_10']:.1f}%
• نطاق الثقة 90%: ${r['percentile_5']:,.2f} - ${r['percentile_95']:,.2f}

{'='*40}
🎯 <b>{r['recommendation']}</b> (الثقة: {r['confidence']} | النقاط: {r['score']})

💰 <b>تفاصيل الصفقة:</b>
• الاتجاه: {r['direction'].upper()}
• حجم الوحدة: {r['position_size']:.4f} | المخاطرة: ${r['risk_amount']:,.2f}
• وقف الخسارة (SL): ${r['sl']:,.2f}
• الأهداف: TP1: ${r['tp1']:,.2f} | TP2: ${r['tp2']:,.2f} | TP3: ${r['tp3']:,.2f}
• المخاطرة/العائد: 1:{r['risk_reward']:.2f}

⚠️ <i>هذا ليس نصيحة مالية. تداول بمسؤوليتك.</i>
"""

def send_message(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception as e: logger.error(f"Send error: {e}")

def format_signal(r):
    return f"<b>{r['symbol']}</b> ({r['type']})\nالسعر: ${r['price']:.2f} | RSI: {r['rsi']:.1f}\nالإشارة: {r['recommendation']} (Score: {r['score']})\nSL: ${r['sl']:.2f} | TP2: ${r['tp2']:.2f}\n━━━━━━━━━━━━\n"

def analyze_all():
    msg = f"<b>تقرير الأسواق الشامل</b>\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    results = []
    for sym in CRYPTO_LIST + STOCKS_LIST + METALS_LIST + FOREX_LIST:
        atype = 'crypto' if sym in CRYPTO_LIST else ('stock' if sym in STOCKS_LIST else ('metal' if sym in METALS_LIST else 'forex'))
        res = analyze_asset(sym, atype)
        if res: results.append(res)
        time.sleep(0.3)
    
    results.sort(key=lambda x: x['score'], reverse=True)
    for r in results[:5]: 
        if r['score'] >= 3: msg += f"🟢 {format_signal(r)}"
    for r in results[-5:]: 
        if r['score'] <= -3: msg += f"🔴 {format_signal(r)}"
    send_message(msg)

def ask_qwen(question, context=""):
    if not OPENROUTER_API_KEY: return "OpenRouter غير مفعّل"
    try:
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://github.com/tysear/trading-bot"}
        payload = {"model": "qwen/qwen-2.5-72b-instruct", "messages": [{"role": "user", "content": f"أجب بالعربية باختصار واحترافية.\n\n{context}\n\nالسؤال: {question}\n\n⚠️ هذا ليس نصيحة مالية."}], "temperature": 0.3, "max_tokens": 1024}
        res = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=30).json()
        return res['choices'][0]['message']['content'] if 'choices' in res else "Error"
    except: return "Error"

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start':
        send_message("<b>بوت التداول الذكي المتقدم (النسخة النهائية)</b>\n\nالأوامر:\n/report - تقرير شامل\n/crypto - عملات\n/stocks - أسهم\n/smart <رمز> - تحليل متقدم شامل مع الأخبار\n/help - المساعدة")
    elif cmd == '/report':
        send_message("جاري إعداد التقرير..."); analyze_all()
    elif cmd == '/crypto':
        send_message("جاري التحليل..."); msg = "<b>العملات الرقمية</b>\n\n"
        for sym in CRYPTO_LIST:
            r = analyze_asset(sym, 'crypto')
            if r: msg += format_signal(r)
        send_message(msg)
    elif cmd == '/stocks':
        send_message("جاري التحليل..."); msg = "<b>الأسهم الأمريكية</b>\n\n"
        for sym in STOCKS_LIST:
            r = analyze_asset(sym, 'stock')
            if r: msg += format_signal(r)
        send_message(msg)
    elif cmd.startswith('/smart'):
        parts = command.split()
        if len(parts) < 2:
            send_message("<b>الاستخدام:</b>\n/smart <رمز>\nمثال: /smart BTC-USD\n/smart AAPL")
            return
        sym = parts[1].upper()
        atype = 'crypto' if sym in CRYPTO_LIST else ('stock' if sym in STOCKS_LIST else ('metal' if sym in METALS_LIST else 'forex'))
        send_message(f"🧮 جاري التحليل المتقدم لـ {sym} (مع الأخبار)...")
        res = unified_analysis(sym, atype)
        if not res:
            send_message(f"فشل التحليل. تأكد من الرمز (جرب {sym.replace('-USD', 'USDT') if 'USD' in sym else sym}-USD)")
            return
        send_message(format_unified_analysis(res))
        send_message("🧠 جاري تحليل الذكاء الاصطناعي للبيانات والأخبار...")
        news_text = "\n".join(res.get('news', ['لا توجد أخبار']))
        ctx = f"السعر: ${res['price']:.2f}, RSI: {res['rsi']:.1f}, Z-Score: {res['ou_z_score']:.2f}, Kelly: {res['kelly_half']*100:.1f}%, احتمال الربح: {res['prob_profit']:.1f}%, حجم التداول: {res['vol_msg']}\n\nالأخبار:\n{news_text}"
        ans = ask_qwen("بناءً على البيانات الرياضية والأخبار أعلاه:\n1. هل الأخبار تدعم الإشارة الفنية؟\n2. ما هو تأثير الأخبار على الصفقة؟\n3. هل تنصح بالدخول الآن أم الانتظار؟\n4. ما هو الخطر الرئيسي؟", ctx)
        send_message(f"🤖 <b>تحليل Qwen النهائي:</b>\n\n{ans}")
    elif cmd == '/help':
        send_message("<b>الأوامر:</b>\n/smart <رمز> (مثال: /smart AAPL)\n/report\n/crypto\n/stocks\n\nالتداول ينطوي على مخاطر.")
    else:
        send_message("استخدم /help للأوامر المتاحة.")

def listen_for_commands():
    global LAST_UPDATE_ID
    try:
        res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={'offset': LAST_UPDATE_ID + 1, 'timeout': 10}, timeout=10).json()
        if res.get('ok') and res.get('result'):
            for u in res['result']:
                if 'message' in u and 'text' in u['message'] and str(u['message']['chat']['id']) == CHAT_ID:
                    handle_command(u['message']['text'].strip())
                LAST_UPDATE_ID = max(LAST_UPDATE_ID, u['update_id'])
    except: pass

def scheduled_tasks():
    schedule.every(6).hours.do(analyze_all)
    schedule.every(10).seconds.do(listen_for_commands)
    logger.info("Tasks scheduled!")

if __name__ == "__main__":
    logger.info("Starting Ultimate Trading Bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    scheduled_tasks()
    analyze_all()
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped")
