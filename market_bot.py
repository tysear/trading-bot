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
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

LAST_UPDATE_ID = 0
CRYPTO_LIST = ['BTC-USD', 'ETH-USD', 'XRP-USD', 'SOL-USD', 'BNB-USD', 'ADA-USD', 'DOGE-USD']
STOCKS_LIST = ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'META']
METALS_LIST = ['GC=F', 'SI=F']
FOREX_LIST = ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X']

app = Flask(__name__)
@app.route('/')
def home(): return "Bot running!"
@app.route('/health')
def health(): return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def fetch_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty: return None
        return {
            'closes': hist['Close'].tolist(),
            'highs': hist['High'].tolist(),
            'lows': hist['Low'].tolist(),
            'volumes': hist['Volume'].tolist()
        }
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None

def fetch_news(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news or len(news) == 0:
            return ["لا توجد أخبار حديثة."]
        news_list = []
        for item in news[:3]:
            title = item.get('title', 'بدون عنوان')
            publisher = item.get('publisher', 'مجهول')
            news_list.append(f"• {title} ({publisher})")
        return news_list
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return ["تعذر جلب الأخبار."]

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    gains, losses = [], []
    for i in range(-period, 0):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
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

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return closes[-1] * 0.02
    true_ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(-period, 0)]
    return sum(true_ranges) / period

def unified_analysis(symbol, asset_type, balance=10000):
    try:
        data = fetch_data(symbol)
        if not data or len(data['closes']) < 50: return None
        
        closes = np.array(data['closes'], dtype=float)
        highs = np.array(data['highs'], dtype=float)
        lows = np.array(data['lows'], dtype=float)
        volumes = data.get('volumes', [])
        news = fetch_news(symbol)
        current_price = float(closes[-1])
        
        rsi = calc_rsi(closes)
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        macd = calc_macd(closes)
        atr = calc_atr(highs, lows, closes)
        
        returns = np.diff(np.log(closes))
        lookback = min(50, len(closes) - 1)
        slope, intercept, r_val, p_val, std_err = linregress(closes[-lookback-1:-1], np.diff(closes[-lookback-1:]))
        ou_theta = float(-slope) if slope < 0 else 0.01
        ou_mu = float(np.mean(closes[-lookback:]))
        ou_sigma = float(np.std(returns[-lookback:])) * np.sqrt(252)
        ou_half_life = float(np.log(2) / ou_theta) if ou_theta > 0 else 999.0
        ou_z_score = float((current_price - ou_mu) / (ou_sigma * current_price)) if ou_sigma > 0 else 0.0
        
        wins = sum(1 for i in range(-min(50, len(closes)-5), -5) if closes[i+5] > closes[i])
        total = min(50, len(closes) - 5)
        win_rate = float(wins / total) if total > 0 else 0.5
        win_loss_ratio = 2.0 if current_price > ema20 else 1.5
        kelly_edge = float((win_rate * win_loss_ratio) - (1 - win_rate))
        kelly_full = float(kelly_edge / win_loss_ratio) if win_loss_ratio > 0 else 0.0
        kelly_half = min(float(kelly_full / 2), 0.25)
        
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
        percentile_5 = float(np.percentile(final_prices, 5))
        percentile_95 = float(np.percentile(final_prices, 95))
        
        vol_msg = "محايد"
        if len(volumes) >= 20:
            recent_vol = np.mean(volumes[-5:])
            avg_vol = np.mean(volumes[-20:])
            if recent_vol > avg_vol * 1.2: vol_msg = "مرتفع (تأكيد الاتجاه) 📈"
            elif recent_vol < avg_vol * 0.8: vol_msg = "منخفض (ضعف الاتجاه) 📉"
        
        risk_amount = balance * kelly_half
        sl_dist = atr * 1.5
        position_size = risk_amount / sl_dist if sl_dist > 0 else 0.0
        
        # ✅ نظام التصويت لتحديد الاتجاه (Majority Voting)
        buy_signals = 0
        if current_price > ema20: buy_signals += 1
        if current_price > ema50: buy_signals += 1
        if macd > 0: buy_signals += 1
        if rsi < 75: buy_signals += 1
        if prob_profit > 55: buy_signals += 1
        if ou_z_score < 1.5: buy_signals += 1
        
        direction = 'buy' if buy_signals >= 4 else 'sell'
        
        if direction == 'buy':
            sl = current_price - (atr*1.5)
            tp1 = current_price + (atr*2)
            tp2 = current_price + (atr*3)
            tp3 = current_price + (atr*4.5)
        else:
            sl = current_price + (atr*1.5)
            tp1 = current_price - (atr*2)
            tp2 = current_price - (atr*3)
            tp3 = current_price - (atr*4.5)
        rr = float(abs(tp2 - current_price) / abs(current_price - sl)) if abs(current_price - sl) > 0 else 0.0
        
        tech_score = 0
        if rsi < 30: tech_score += 3
        elif rsi > 70: tech_score -= 3
        if current_price > ema20 > ema50: tech_score += 3
        elif current_price < ema20 < ema50: tech_score -= 3
        if macd > 0: tech_score += 2
        else: tech_score -= 2
        if ou_z_score < -2: tech_score += 3
        elif ou_z_score > 2: tech_score -= 3
        if prob_profit > 60: tech_score += 2
        elif prob_profit < 40: tech_score -= 2
        if kelly_edge > 0.1: tech_score += 2
        elif kelly_edge < 0: tech_score -= 3
        
        return {
            'symbol': symbol, 'type': asset_type, 'price': current_price, 'rsi': rsi, 
            'ema20': ema20, 'ema50': ema50, 'macd': macd, 'atr': atr, 
            'direction': direction, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 
            'risk_reward': rr, 'ou_theta': ou_theta, 'ou_mu': ou_mu, 
            'ou_z_score': ou_z_score, 'ou_half_life': ou_half_life, 
            'win_rate': win_rate, 'kelly_edge': kelly_edge, 'kelly_half': kelly_half, 
            'prob_profit': prob_profit, 'prob_loss_10': prob_loss_10, 
            'percentile_5': percentile_5, 'percentile_95': percentile_95,
            'vol_msg': vol_msg, 'position_size': position_size, 'risk_amount': risk_amount,
            'tech_score': tech_score, 'news': news
        }
    except Exception as e:
        logger.error(f"Error in unified_analysis: {e}")
        return None

def ask_qwen_unified(data):
    if not OPENROUTER_API_KEY: return None
    
    news_text = "\n".join(data.get('news', ['لا توجد أخبار']))
    
    prompt = f"""أنت محلل مالي خبير. حلل البيانات التالية وأعطِ نتيجة واحدة موحدة.

📊 البيانات الرياضية لـ {data['symbol']}:
• السعر: ${data['price']:,.2f}
• RSI: {data['rsi']:.1f}
• EMA20: ${data['ema20']:,.2f} | EMA50: ${data['ema50']:,.2f}
• MACD: {data['macd']:.2f}
• ATR: ${data['atr']:,.2f}
• OU Z-Score: {data['ou_z_score']:.2f} (المتوسط: ${data['ou_mu']:,.2f})
• عمر النصف: {data['ou_half_life']:.1f} يوم
• نسبة النجاح: {data['win_rate']*100:.1f}%
• Kelly الآمن: {data['kelly_half']*100:.1f}%
• احتمال الربح (مونت كارلو): {data['prob_profit']:.1f}%
• احتمال خسارة 10%+: {data['prob_loss_10']:.1f}%
• حجم التداول: {data['vol_msg']}
• نقاط التحليل الفني: {data['tech_score']}

📰 آخر الأخبار:
{news_text}

🎯 المطلوب (بالعربية، بشكل منظم):
1. القرار النهائي الموحد: (شراء قوي 🟢 / شراء 🟢 / انتظار ⚪ / بيع 🔴 / بيع قوي 🔴🔴)
2. التفسير: لماذا هذا القرار؟ (ادمج بين الفني والأخبار في تفسير واحد)
3. حجم الصفقة المقترح: (نسبة من رأس المال)
4. المخاطر الرئيسية
5. نصيحة عملية

⚠️ هذا ليس نصيحة مالية."""

    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/tysear/trading-bot",
            "X-Title": "Trading Bot"
        }
        payload = {
            "model": "qwen/qwen-2.5-72b-instruct",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1500
        }
        res = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=45).json()
        if 'choices' in res and len(res['choices']) > 0:
            return res['choices'][0]['message']['content']
        return None
    except Exception as e:
        logger.error(f"Qwen error: {e}")
        return None

def format_final_report(data, ai_analysis):
    news_text = "\n".join(data.get('news', ['لا توجد أخبار']))
    
    report = f"""
🎯 <b>التقرير الشامل الموحد لـ {data['symbol']}</b>
{'='*45}

💰 <b>السعر الحالي:</b> ${data['price']:,.2f}

📊 <b>التحليل الفني والرياضي:</b>
• RSI: {data['rsi']:.1f} | MACD: {data['macd']:.2f}
• EMA20: ${data['ema20']:,.2f} | EMA50: ${data['ema50']:,.2f}
• ATR: ${data['atr']:,.2f}
• حجم التداول: {data['vol_msg']}

🧮 <b>نموذج OU (العودة للمتوسط):</b>
• المتوسط: ${data['ou_mu']:,.2f}
• Z-Score: {data['ou_z_score']:.2f}
• عمر النصف: {data['ou_half_life']:.1f} يوم

🎲 <b>Kelly Criterion:</b>
• نسبة النجاح: {data['win_rate']*100:.1f}%
• الحجم الآمن (Half Kelly): {data['kelly_half']*100:.1f}%

 <b>مونت كارلو (30 يوم):</b>
• احتمال الربح: {data['prob_profit']:.1f}%
• احتمال خسارة 10%+: {data['prob_loss_10']:.1f}%
• نطاق الثقة 90%: ${data['percentile_5']:,.2f} - ${data['percentile_95']:,.2f}

📰 <b>آخر الأخبار:</b>
{news_text}

{'='*45}
🤖 <b>التحليل النهائي الموحد (Qwen AI):</b>
{'='*45}

{ai_analysis if ai_analysis else '⚠️ تعذر الحصول على تحليل الذكاء الاصطناعي'}

{'='*45}
💰 <b>مستويات التداول:</b>
• الاتجاه: {data['direction'].upper()}
• وقف الخسارة (SL): ${data['sl']:,.2f}
• الهدف 1 (TP1): ${data['tp1']:,.2f}
• الهدف 2 (TP2): ${data['tp2']:,.2f}
• الهدف 3 (TP3): ${data['tp3']:,.2f}
• المخاطرة/العائد: 1:{data['risk_reward']:.2f}

⚠️ <i>هذا ليس نصيحة مالية. تداول بمسؤوليتك.</i>
"""
    return report

def send_message(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=15)
    except Exception as e: logger.error(f"Send error: {e}")

def handle_command(command):
    cmd = command.strip().lower()
    
    if cmd == '/start':
        send_message("""<b>🤖 بوت التداول الذكي - النسخة الموحدة</b>

الأوامر:
/smart <رمز> - تحليل شامل موحد (فني + أخبار + AI)
/report - تقرير السوق
/crypto - العملات
/stocks - الأسهم
/help - المساعدة""")
    
    elif cmd.startswith('/smart'):
        parts = command.split()
        if len(parts) < 2:
            send_message("<b>الاستخدام:</b>\n/smart <رمز>\nمثال: /smart BTC-USD\n/smart AAPL")
            return
        
        sym = parts[1].upper()
        atype = 'crypto' if sym in CRYPTO_LIST else ('stock' if sym in STOCKS_LIST else ('metal' if sym in METALS_LIST else 'forex'))
        
        send_message(f" جاري التحليل الشامل الموحد لـ {sym}...")
        
        data = unified_analysis(sym, atype)
        if not data:
            send_message(f"❌ فشل التحليل. تأكد من الرمز.")
            return
        
        send_message("🤖 جاري دمج التحليل الفني مع الأخبار عبر الذكاء الاصطناعي...")
        
        ai_analysis = ask_qwen_unified(data)
        
        final_report = format_final_report(data, ai_analysis)
        send_message(final_report)
    
    elif cmd == '/report':
        send_message("جاري إعداد التقرير...")
        msg = f"<b>تقرير الأسواق</b>\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        for sym in CRYPTO_LIST[:3] + STOCKS_LIST[:3]:
            atype = 'crypto' if sym in CRYPTO_LIST else 'stock'
            data = unified_analysis(sym, atype)
            if data:
                msg += f"• <b>{sym}</b>: ${data['price']:,.2f} | RSI: {data['rsi']:.1f} | النقاط: {data['tech_score']}\n"
        send_message(msg)
    
    elif cmd == '/crypto':
        send_message("جاري تحليل العملات...")
        msg = "<b>العملات الرقمية</b>\n\n"
        for sym in CRYPTO_LIST:
            data = unified_analysis(sym, 'crypto')
            if data:
                msg += f"• <b>{sym}</b>: ${data['price']:,.2f} | RSI: {data['rsi']:.1f}\n"
        send_message(msg)
    
    elif cmd == '/stocks':
        send_message("جاري تحليل الأسهم...")
        msg = "<b>الأسهم</b>\n\n"
        for sym in STOCKS_LIST:
            data = unified_analysis(sym, 'stock')
            if data:
                msg += f"• <b>{sym}</b>: ${data['price']:,.2f} | RSI: {data['rsi']:.1f}\n"
        send_message(msg)
    
    elif cmd == '/help':
        send_message("""<b>الأوامر:</b>
/smart <رمز> - تحليل شامل موحد
/report - تقرير السوق
/crypto - العملات
/stocks - الأسهم

التداول ينطوي على مخاطر.""")
    
    else:
        send_message("استخدم /help للأوامر.")

def listen_for_commands():
    global LAST_UPDATE_ID
    try:
        res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", 
                          params={'offset': LAST_UPDATE_ID + 1, 'timeout': 10}, timeout=10).json()
        if res.get('ok') and res.get('result'):
            for u in res['result']:
                if 'message' in u and 'text' in u['message'] and str(u['message']['chat']['id']) == CHAT_ID:
                    handle_command(u['message']['text'].strip())
                LAST_UPDATE_ID = max(LAST_UPDATE_ID, u['update_id'])
    except: pass

def scheduled_tasks():
    schedule.every(10).seconds.do(listen_for_commands)
    logger.info("Tasks scheduled!")

if __name__ == "__main__":
    logger.info("Starting Unified Trading Bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    scheduled_tasks()
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped")
