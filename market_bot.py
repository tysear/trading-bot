import requests
import time
import schedule
import yfinance as yf
from datetime import datetime
import logging
import os

# ========== إعدادات السجل (Logs) ==========
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
TELEGRAM_TOKEN = "8917209003:AAFEDVugxuj6LEzELv8NtkoCav5Zwqn8f_E"
CHAT_ID = "1814016230"

# ========== قوائم الأصول ==========
CRYPTO_LIST = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT', 'DOGEUSDT']
STOCKS_LIST = ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'META']
METALS_LIST = ['GC=F', 'SI=F']  # الذهب والفضة
FOREX_LIST = ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X']  # الفوركس

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

# ========== 3. حساب وقف الخسارة والأهداف ==========
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

# ========== 4. التحليل الشامل ==========
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
    
    # RSI
    if rsi < 30:
        score += 2
        signals.append(f"✅ RSI={rsi:.1f} تشبع بيعي")
    elif rsi > 70:
        score -= 2
        signals.append(f"❌ RSI={rsi:.1f} تشبع شرائي")
    else:
        signals.append(f"⚪ RSI={rsi:.1f} محايد")
    
    # EMA
    if price > ema20 > ema50:
        score += 2
        signals.append("✅ السعر فوق EMA20 و EMA50")
    elif price < ema20 < ema50:
        score -= 2
        signals.append("❌ السعر تحت EMA20 و EMA50")
    else:
        signals.append("⚪ EMA محايد")
    
    # MACD
    if macd > 0:
        score += 1
        signals.append("✅ MACD إيجابي")
    else:
        score -= 1
        signals.append("❌ MACD سلبي")
    
    # Bollinger
    if price <= bb_lower:
        score += 2
        signals.append("✅ السعر عند الحد السفلي BB")
    elif price >= bb_upper:
        score -= 2
        signals.append(" السعر عند الحد العلوي BB")
    
    # المشتقات
    if p1 > 0 and p2 > 0:
        score += 1
        signals.append("✅ تسارع صعودي")
    elif p1 < 0 and p2 < 0:
        score -= 1
        signals.append("❌ تسارع هبوطي")
    elif p1 < 0 and p2 > 0:
        score += 1
        signals.append("🔄 تباطؤ الهبوط")
    
    # التوصية
    if score >= 5:
        rec = "🟢🟢 شراء قوي جداً"
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
        'symbol': symbol,
        'type': asset_type,
        'price': price,
        'rsi': rsi,
        'ema20': ema20,
        'score': score,
        'recommendation': rec,
        'direction': direction,
        'signals': signals,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'rr_ratio': rr_ratio
    }

# ========== 5. إرسال الرسائل ==========
def send_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

def send_urgent_alert(message):
    """إرسال تنبيه عاجل"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": f" <b>تنبيه عاجل!</b>\n\n{message}",
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Error sending alert: {e}")
        return False

# ========== 6. تنسيق الإشارة ==========
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
        msg += f" الهدف 3: ${r['tp3']:.2f}\n"
        msg += f"️ المخاطرة/العائد: 1:{r['rr_ratio']:.1f}\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    return msg

# ========== 7. التحليل الشامل ==========
def analyze_all():
    logger.info("بدء التحليل الشامل...")
    
    message = f"🤖 <b>تقرير الأسواق الشامل</b>\n"
    message += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    
    all_results = []
    
    # الكريبتو
    logger.info("تحليل العملات...")
    for symbol in CRYPTO_LIST:
        result = analyze_asset(symbol, 'crypto')
        if result:
            all_results.append(result)
        time.sleep(0.5)
    
    # الأسهم
    logger.info("تحليل الأسهم...")
    for symbol in STOCKS_LIST:
        result = analyze_asset(symbol, 'stock')
        if result:
            all_results.append(result)
        time.sleep(0.5)
    
    # المعادن
    logger.info("تحليل المعادن...")
    for symbol in METALS_LIST:
        result = analyze_asset(symbol, 'metal')
        if result:
            all_results.append(result)
        time.sleep(0.5)
    
    # الفوركس
    logger.info("تحليل الفوركس...")
    for symbol in FOREX_LIST:
        result = analyze_asset(symbol, 'forex')
        if result:
            all_results.append(result)
        time.sleep(0.5)
    
    all_results.sort(key=lambda x: x['score'], reverse=True)
    
    # فرص الشراء
    buys = [r for r in all_results if r['score'] >= 3]
    if buys:
        message += "🟢 <b>فرص الشراء:</b>\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in buys[:5]:
            message += format_signal(r)
    
    # فرص البيع
    sells = [r for r in all_results if r['score'] <= -3]
    if sells:
        message += "\n <b>فرص البيع:</b>\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in sells[:5]:
            message += format_signal(r)
    
    # الانتظار
    waits = [r for r in all_results if -3 < r['score'] < 3]
    if waits:
        message += "\n⚪ <b>انتظار:</b>\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in waits[:7]:
            message += f"• <b>{r['symbol']}</b> ({r['type']})\n"
            message += f"  السعر: ${r['price']:.2f} | RSI: {r['rsi']:.1f}\n\n"
    
    message += "\n⚠️ <i>هذا ليس نصيحة مالية. تداول بمسؤوليتك.</i>"
    
    send_message(message)
    logger.info("تم إرسال التقرير الشامل!")

# ========== 8. التنبيهات الفورية ==========
def check_urgent_signals():
    """فحص الفرص القوية جداً وإرسال تنبيه فوري"""
    logger.info("فحص التنبيهات العاجلة...")
    
    urgent_assets = CRYPTO_LIST[:3]  # فحص أهم 3 عملات
    
    for symbol in urgent_assets:
        result = analyze_asset(symbol, 'crypto')
        if result:
            # RSI متطرف
            if result['rsi'] < 25:
                msg = f"<b>{result['symbol']}</b>\n"
                msg += f"RSI = {result['rsi']:.1f} (تشبع بيعي قوي جداً!)\n"
                msg += f"السعر: ${result['price']:.2f}\n"
                msg += "\n🟢 فرصة شراء قوية جداً!"
                send_urgent_alert(msg)
                logger.info(f"تنبيه عاجل: {symbol} RSI={result['rsi']}")
            
            elif result['rsi'] > 75:
                msg = f"<b>{result['symbol']}</b>\n"
                msg += f"RSI = {result['rsi']:.1f} (تشبع شرائي قوي جداً!)\n"
                msg += f"السعر: ${result['price']:.2f}\n"
                msg += "\n🔴 فرصة بيع قوية جداً!"
                send_urgent_alert(msg)
                logger.info(f"تنبيه عاجل: {symbol} RSI={result['rsi']}")
            
            # إشارة قوية جداً
            elif result['score'] >= 5:
                msg = format_signal(result)
                send_urgent_alert(f"إشارة شراء قوية:\n\n{msg}")
                logger.info(f"تنبيه عاجل: {symbol} Score={result['score']}")
        
        time.sleep(0.5)

# ========== 9. أوامر Telegram ==========
def handle_command(command):
    """معالجة الأوامر من المستخدم"""
    if command == '/start':
        msg = """
🤖 <b>مرحباً بك في بوت التداول الذكي!</b>

الأوامر المتاحة:
/report - تقرير شامل للأسواق
/crypto - تحليل العملات فقط
/stocks - تحليل الأسهم فقط
/metals - تحليل المعادن
/forex - تحليل الفوركس
/urgent - فحص التنبيهات العاجلة
/help - المساعدة

⚙️ البوت يعمل تلقائياً كل 6 ساعات
"""
        send_message(msg)
    
    elif command == '/report':
        send_message("⏳ جاري إعداد التقرير الشامل...")
        analyze_all()
    
    elif command == '/crypto':
        send_message("⏳ جاري تحليل العملات...")
        message = " <b>تقرير العملات الرقمية</b>\n\n"
        for symbol in CRYPTO_LIST:
            result = analyze_asset(symbol, 'crypto')
            if result:
                message += format_signal(result)
        send_message(message)
    
    elif command == '/stocks':
        send_message("⏳ جاري تحليل الأسهم...")
        message = " <b>تقرير الأسهم</b>\n\n"
        for symbol in STOCKS_LIST:
            result = analyze_asset(symbol, 'stock')
            if result:
                message += format_signal(result)
        send_message(message)
    
    elif command == '/metals':
        send_message("⏳ جاري تحليل المعادن...")
        message = "🥇 <b>تقرير المعادن</b>\n\n"
        for symbol in METALS_LIST:
            result = analyze_asset(symbol, 'metal')
            if result:
                message += format_signal(result)
        send_message(message)
    
    elif command == '/forex':
        send_message(" جاري تحليل الفوركس...")
        message = "💱 <b>تقرير الفوركس</b>\n\n"
        for symbol in FOREX_LIST:
            result = analyze_asset(symbol, 'forex')
            if result:
                message += format_signal(result)
        send_message(message)
    
    elif command == '/urgent':
        send_message("🔍 جاري فحص التنبيهات العاجلة...")
        check_urgent_signals()
        send_message("✅ تم الفحص!")
    
    elif command == '/help':
        msg = """
📚 <b>دليل البوت:</b>

<b>التقارير:</b>
/report - تقرير شامل (كريبتو + أسهم + معادن + فوركس)
/crypto - العملات الرقمية فقط
/stocks - الأسهم الأمريكية
/metals - الذهب والفضة
/forex - أزواج العملات

<b>التنبيهات:</b>
/urgent - فحص فوري للفرص القوية

<b>التشغيل التلقائي:</b>
• تقرير شامل كل 6 ساعات
• تنبيهات فورية عند RSI متطرف
• يعمل 24/7 بدون توقف

⚠️ <i>التداول ينطوي على مخاطر. استخدم إدارة رأس مال مناسبة.</i>
"""
        send_message(msg)

# ========== 10. الاستماع للأوامر ==========
def listen_for_commands():
    """الاستماع لأوامر المستخدم من Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('ok') and data.get('result'):
            for update in data['result']:
                if 'message' in update and 'text' in update['message']:
                    command = update['message']['text'].strip()
                    if command.startswith('/'):
                        handle_command(command)
                        logger.info(f"تم تنفيذ الأمر: {command}")
    except Exception as e:
        logger.error(f"Error listening: {e}")

# ========== 11. التشغيل التلقائي ==========
def scheduled_tasks():
    """المهام المجدولة"""
    # تقرير شامل كل 6 ساعات
    schedule.every(6).hours.do(analyze_all)
    
    # تقارير في أوقات محددة
    schedule.every().day.at("09:00").do(analyze_all)
    schedule.every().day.at("15:00").do(analyze_all)
    schedule.every().day.at("21:00").do(analyze_all)
    
    # فحص التنبيهات العاجلة كل ساعة
    schedule.every(1).hours.do(check_urgent_signals)
    
    # الاستماع للأوامر كل 30 ثانية
    schedule.every(30).seconds.do(listen_for_commands)
    
    logger.info("تم جدولة جميع المهام!")

# ========== 12. نقطة البداية ==========
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 بوت التداول الذكي - الإصدار الاحترافي")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n✅ البوت جاهز للعمل!")
    print("📊 التقارير التلقائية: كل 6 ساعات")
    print("🔔 التنبيهات العاجلة: كل ساعة")
    print("💬 الأوامر التفاعلية: كل 30 ثانية")
    print("\nاضغط Ctrl+C للإيقاف\n")
    
    # جدولة المهام
    scheduled_tasks()
    
    # تشغيل تقرير فوري عند البدء
    logger.info("تشغيل تقرير أولي...")
    analyze_all()
    
    # حلقة التشغيل المستمرة
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        logger.error(f"خطأ غير متوقع: {e}")
        send_message(f"❌ خطأ في البوت: {str(e)}")