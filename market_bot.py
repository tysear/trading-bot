import requests
import time
import schedule
import yfinance as yf
from datetime import datetime
import logging
import os
import google.generativeai as genai

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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# تهيئة Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    logger.info("✅ تم تهيئة Gemini AI بنجاح")
else:
    model = None
    logger.warning("️ مفتاح Gemini غير موجود - الذكاء الاصطناعي معطل")

# ========== متغير لتتبع آخر رسالة ==========
LAST_UPDATE_ID = 0

# ========== قوائم الأصول ==========
CRYPTO_LIST = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT', 'DOGEUSDT']
STOCKS_LIST = ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'META']
METALS_LIST = ['GC=F', 'SI=F']
FOREX_LIST = ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X']

# ========== 1. جلب البيانات ==========
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

# ========== 2. المؤشرات الفنية ==========
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

# ========== 3. التحليل الشامل ==========
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
        signals.append(f"❌ RSI={rsi:.1f} تشبع شرائي")
    else:
        signals.append(f"⚪ RSI={rsi:.1f} محايد")
    
    if price > ema20 > ema50:
        score += 2
        signals.append("✅ السعر فوق EMA20 و EMA50")
    elif price < ema20 < ema50:
        score -= 2
        signals.append("❌ السعر تحت EMA20 و EMA50")
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
        signals.append("❌ تسارع هبوطي")
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

# ========== 4. إرسال الرسائل ==========
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
    payload = {"chat_id": CHAT_ID, "text": f"🚨 <b>تنبيه عاجل!</b>\n\n{message}", "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Error sending alert: {e}")
        return False

def format_signal(r):
    msg = f"<b>📊 {r['symbol']}</b> ({r['type']})\n"
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

# ========== 5. التحليل الشامل ==========
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
        message += "\n⚪ <b>انتظار:</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
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
                msg = f"<b>{result['symbol']}</b>\nRSI = {result['rsi']:.1f} (تشبع بيعي قوي!)\nالسعر: ${result['price']:.2f}\n\n🟢 فرصة شراء قوية!"
                send_urgent_alert(msg)
            elif result['rsi'] > 75:
                msg = f"<b>{result['symbol']}</b>\nRSI = {result['rsi']:.1f} (تشبع شرائي قوي!)\nالسعر: ${result['price']:.2f}\n\n🔴 فرصة بيع قوية!"
                send_urgent_alert(msg)
        time.sleep(0.5)

# ========== 6. الذكاء الاصطناعي - Gemini ==========
def ask_gemini(question, context=""):
    """إرسال سؤال إلى Gemini والحصول على رد ذكي"""
    if not model:
        return "⚠️ الذكاء الاصطناعي غير مفعّل حالياً. يرجى إضافة مفتاح GEMINI_API_KEY."
    
    try:
        prompt = f"""أنت مساعد تداول ذكي محترف. أجب باللغة العربية بشكل واضح ومختصر.

{context}

سؤال المستخدم: {question}

⚠️ مهم: اذكر دائماً أن هذا ليس نصيحة مالية، وأن التداول ينطوي على مخاطر."""
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return f"❌ حدث خطأ في الذكاء الاصطناعي: {str(e)}"

def get_asset_context(symbol):
    """الحصول على بيانات السهم لاستخدامها كسياق للذكاء الاصطناعي"""
    asset_type = 'crypto' if symbol in CRYPTO_LIST else 'stock'
    result = analyze_asset(symbol, asset_type)
    if not result:
        return None
    
    context = f"""بيانات {symbol} الحالية:
- السعر: ${result['price']:.2f}
- RSI: {result['rsi']:.1f}
- EMA20: ${result['ema20']:.2f}
- التوصية: {result['recommendation']}
- النقاط: {result['score']}
- وقف الخسارة: ${result['sl']:.2f}
- الأهداف: ${result['tp1']:.2f}, ${result['tp2']:.2f}, ${result['tp3']:.2f}
- المخاطرة/العائد: 1:{result['rr_ratio']:.1f}"""
    return context

# ========== 7. معالجة الأوامر ==========
def handle_command(command, user_message=""):
    """معالجة الأوامر والرسائل"""
    cmd = command.strip().lower()
    
    if cmd == '/start':
        msg = """
🤖 <b>مرحباً بك في بوت التداول الذكي!</b>

<b>📊 الأوامر المتاحة:</b>
/report - تقرير شامل للأسواق
/crypto - تحليل العملات فقط
/stocks - تحليل الأسهم فقط
/metals - تحليل المعادن
/forex - تحليل الفوركس
/urgent - فحص التنبيهات العاجلة

<b>🧠 أوامر الذكاء الاصطناعي:</b>
/ask <سؤالك> - اسأل أي شيء عن التداول
/analyze <رمز> - تحليل ذكي معمق (مثل: /analyze BTC)
/strategy - نصائح استراتيجية مخصصة
/news - آخر الأخبار الاقتصادية
/help - المساعدة

💬 <b>يمكنك أيضاً إرسال أي سؤال مباشرة!</b>
"""
        send_message(msg)
    
    elif cmd == '/report':
        send_message("⏳ جاري إعداد التقرير الشامل...")
        analyze_all()
    
    elif cmd == '/crypto':
        send_message("⏳ جاري تحليل العملات...")
        message = "🪙 <b>تقرير العملات الرقمية</b>\n\n"
        for symbol in CRYPTO_LIST:
            result = analyze_asset(symbol, 'crypto')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/stocks':
        send_message(" جاري تحليل الأسهم...")
        message = "📈 <b>تقرير الأسهم</b>\n\n"
        for symbol in STOCKS_LIST:
            result = analyze_asset(symbol, 'stock')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/metals':
        send_message("⏳ جاري تحليل المعادن...")
        message = " <b>تقرير المعادن</b>\n\n"
        for symbol in METALS_LIST:
            result = analyze_asset(symbol, 'metal')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/forex':
        send_message("⏳ جاري تحليل الفوركس...")
        message = " <b>تقرير الفوركس</b>\n\n"
        for symbol in FOREX_LIST:
            result = analyze_asset(symbol, 'forex')
            if result: message += format_signal(result)
        send_message(message)
    
    elif cmd == '/urgent':
        send_message("🔍 جاري فحص التنبيهات العاجلة...")
        check_urgent_signals()
        send_message("✅ تم الفحص!")
    
    elif cmd.startswith('/ask '):
        question = command[5:].strip()
        if not question:
            send_message("❌ يرجى كتابة سؤال بعد /ask\nمثال: /ask ما هو أفضل وقت لشراء Bitcoin؟")
            return
        send_message("🧠 جاري التفكير...")
        answer = ask_gemini(question)
        send_message(f"🤖 <b>إجابة الذكاء الاصطناعي:</b>\n\n{answer}")
    
    elif cmd.startswith('/analyze '):
        symbol = command[9:].strip().upper()
        send_message(f"🔍 جاري التحليل الذكي لـ {symbol}...")
        context = get_asset_context(symbol)
        if not context:
            send_message(f"❌ لم أتمكن من العثور على بيانات {symbol}")
            return
        question = f"حلل {symbol} بشكل معمق وأعطِ توصية واضحة مع الأسباب"
        answer = ask_gemini(question, context)
        send_message(f" <b>تحليل ذكي لـ {symbol}:</b>\n\n{answer}")
    
    elif cmd == '/strategy':
        send_message("🧠 جاري إعداد نصائح استراتيجية...")
        question = "أعطني 5 نصائح استراتيجية مهمة للتداول الآمن وإدارة المخاطر للمبتدئين"
        answer = ask_gemini(question)
        send_message(f"📚 <b>نصائح استراتيجية:</b>\n\n{answer}")
    
    elif cmd == '/news':
        send_message("🧠 جاري جلب آخر الأخبار الاقتصادية...")
        question = "ما هي أهم الأخبار الاقتصادية والتطورات في أسواق المال اليوم؟ (كريبتو، أسهم، فوركس)"
        answer = ask_gemini(question)
        send_message(f"📰 <b>آخر الأخبار:</b>\n\n{answer}")
    
    elif cmd == '/help':
        msg = """
📚 <b>دليل البوت الذكي:</b>

<b> التقارير:</b>
/report - تقرير شامل
/crypto - العملات فقط
/stocks - الأسهم فقط
/metals - الذهب والفضة
/forex - الفوركس

<b>🧠 الذكاء الاصطناعي:</b>
/ask <سؤال> - اسأل أي شيء
/analyze <رمز> - تحليل معمق
/strategy - نصائح استراتيجية
/news - آخر الأخبار

<b>🔔 التنبيهات:</b>
/urgent - فحص فوري

️ <i>التداول ينطوي على مخاطر.</i>
"""
        send_message(msg)
    
    else:
        # رسالة عادية - الرد بالذكاء الاصطناعي
        if model:
            send_message("🧠 جاري التفكير في إجابتك...")
            answer = ask_gemini(command)
            send_message(f"🤖 {answer}")
        else:
            send_message("❓ الأوامر المتاحة: /start, /report, /help, /ask")

# ========== 8. الاستماع للأوامر ==========
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
                    
                    # التأكد من أن الرسالة من المالك فقط
                    if chat_id == CHAT_ID:
                        if text.startswith('/'):
                            handle_command(text)
                        else:
                            # رسالة عادية - استخدم الذكاء الاصطناعي
                            if model:
                                send_message(" جاري التفكير...")
                                answer = ask_gemini(text)
                                send_message(f" {answer}")
                            else:
                                send_message("💬 استخدم /help لرؤية الأوامر المتاحة")
                        
                        logger.info(f"تم معالجة: {text[:50]}")
                
                LAST_UPDATE_ID = max(LAST_UPDATE_ID, update_id)
    except Exception as e:
        logger.error(f"Error listening: {e}")

# ========== 9. الجدولة ==========
def scheduled_tasks():
    schedule.every(6).hours.do(analyze_all)
    schedule.every().day.at("09:00").do(analyze_all)
    schedule.every().day.at("15:00").do(analyze_all)
    schedule.every().day.at("21:00").do(analyze_all)
    schedule.every(1).hours.do(check_urgent_signals)
    schedule.every(10).seconds.do(listen_for_commands)
    logger.info("✅ تم جدولة جميع المهام!")

# ========== 10. نقطة البداية ==========
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 بوت التداول الذكي - مع Gemini AI")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Gemini AI: {'✅ مفعّل' if model else '❌ غير مفعّل'}")
    print("\n✅ البوت جاهز للعمل!")
    print("📊 التقارير التلقائية: كل 6 ساعات")
    print("🔔 التنبيهات العاجلة: كل ساعة")
    print("🧠 الذكاء الاصطناعي: نشط")
    print("\nاضغط Ctrl+C للإيقاف\n")
    
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
        logger.error(f"خطأ غير متوقع: {e}")
        send_message(f"❌ خطأ في البوت: {str(e)}")
