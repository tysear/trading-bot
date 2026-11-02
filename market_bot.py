import yfinance as yf
import numpy as np
import logging
import os
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    from signal_image import generate_signal_image
except ImportError:
    generate_signal_image = None

try:
    from smc_indicators import get_smc_analysis
except ImportError:
    get_smc_analysis = None

try:
    from trade_logger import log_trade, get_performance
except ImportError:
    log_trade = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

ALL_SYMBOLS = [
    'BTC-USD', 'ETH-USD', 'XRP-USD', 'BNB-USD', 'SOL-USD', 'ADA-USD', 'DOGE-USD',
    'GC=F', 'SI=F', 'CL=F', 'EURUSD=X', 'GBPUSD=X', 'USDJPY=X',
    '^GSPC', '^IXIC', '^DJI', 'AAPL', 'TSLA', 'MSFT', 'NVDA', 'GOOGL', 'META',
]

def calculate_stochastic_analysis(prices):
    if not prices or len(prices) < 10:
        return {}
    arr = np.array(prices, dtype=float)
    log_returns = np.diff(np.log(arr))
    daily_volatility = np.std(log_returns)
    drift = np.mean(log_returns)
    sharpe_ratio = (drift - 0.02/252) / daily_volatility if daily_volatility != 0 else 0
    return {'sharpe_ratio': sharpe_ratio, 'var_95': np.percentile(log_returns, 5)}

def fetch_data(symbol, interval="4h"):
    try:
        period = "6mo" if interval == '4h' else "1y"
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        if hist.empty:
            return None
        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        volumes = hist['Volume'].tolist()
        cleaned = [c for c in closes if c > 0 and not np.isnan(c)]
        if len(cleaned) < 30:
            return None
        return {'closes': cleaned, 'highs': highs[:len(cleaned)], 'lows': lows[:len(cleaned)], 'volumes': volumes[:len(cleaned)]}
    except Exception as e:
        logger.error(f"Fetch error: {e}")
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

def get_recommendation(rsi, sharpe, smc_score, smc_zone):
    score = 0
    if rsi < 30: score += 3
    elif rsi > 70: score -= 3
    if sharpe > 1.0: score += 2
    elif sharpe < -0.5: score -= 2
    if smc_score: score += smc_score
    if smc_zone == 'LOWER_DISCOUNT': score += 2
    elif smc_zone == 'UPPER_PREMIUM': score -= 2
    
    if score >= 4: return 'شراء قوي 🟢', score, 'عالية'
    elif score >= 1: return 'شراء حذر 🟡', score, 'متوسطة'
    elif score <= -4: return 'بيع قوي 🔴', score, 'عالية'
    elif score <= -1: return 'بيع حذر 🟠', score, 'متوسطة'
    else: return 'انتظار ⚪', score, 'منخفضة'

def unified_analysis(symbol, interval="4h"):
    try:
        data = fetch_data(symbol, interval)
        if not data:
            return None
        closes, highs, lows = data['closes'], data['highs'], data['lows']
        price = float(closes[-1])
        rsi = calc_rsi(closes)
        atr = calc_atr(highs, lows, closes)
        stochastic = calculate_stochastic_analysis(closes)
        
        smc_data = None
        smc_score = 0
        smc_zone = 'محايد'
        if get_smc_analysis:
            try:
                smc_data = get_smc_analysis(closes, highs, lows, data.get('volumes', []))
                smc_score = smc_data.get('score', 0)
                smc_zone = smc_data.get('premium_discount', {}).get('zone', 'محايد')
            except:
                pass
        
        action, score, conf = get_recommendation(rsi, stochastic.get('sharpe_ratio', 0), smc_score, smc_zone)
        
        if 'شراء' in action:
            sl, tp1, tp2 = price - (atr * 0.8), price + (atr * 1.5), price + (atr * 2.5)
        elif 'بيع' in action:
            sl, tp1, tp2 = price + (atr * 0.8), price - (atr * 1.5), price - (atr * 2.5)
        else:
            sl, tp1, tp2 = price - (atr * 0.8), price + (atr * 1.5), price + (atr * 2.5)
        
        rr = abs(tp1 - price) / abs(price - sl) if abs(price - sl) > 0 else 0
        
        return {
            'symbol': symbol, 'price': price, 'interval': interval, 'rsi': rsi, 'atr': atr,
            'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rr': rr, 'action': action, 'score': score,
            'confidence': conf, 'stochastic': stochastic, 'smc_zone': smc_zone
        }
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return None

async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("الاستخدام: /smart <رمز> [تايم_فريم]\nمثال: /smart BTC-USD 4h")
            return
        
        symbol = args[0].strip()
        interval = args[1].strip().lower() if len(args) > 1 else "4h"
        if interval not in ['15m', '1h', '4h', '1d']:
            interval = "4h"
        
        await update.message.reply_text(f"⏳ جاري تحليل {symbol}...")
        
        data = unified_analysis(symbol, interval)
        if not data:
            await update.message.reply_text(f"❌ فشل تحليل {symbol}. تأكد من صحة الرمز.")
            return
        
        # إرسال صورة الإشارة
        if generate_signal_image:
            try:
                image_bytes = generate_signal_image(data)
                from io import BytesIO
                await update.message.reply_photo(photo=BytesIO(image_bytes), caption=f"📊 {symbol}")
            except Exception as e:
                logger.error(f"Image error: {e}")
        
        # إرسال التقرير المختصر
        emoji = "🟢" if 'شراء' in data['action'] else "🔴" if 'بيع' in data['action'] else "⚪"
        dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
        
        msg = f"""{emoji} **{data['symbol']} ({data['interval'].upper()})**
💰 **السعر:** `${data['price']:,.2f}`
🎯 **التوصية:** {data['action']} (Score: {data['score']})
⚖️ **العائد/المخاطرة:** 1:{data['rr']:.2f}

📌 **مستويات التداول:**
🛑 SL: `${data['sl']:,.2f}`
🎯 TP1: `${data['tp1']:,.2f}` | TP2: `${data['tp2']:,.2f}`

🧠 **السبب:** RSI: {data['rsi']:.1f} | Sharpe: {data['stochastic'].get('sharpe_ratio', 0):.2f} | المنطقة: {data['smc_zone']}

🔗 للتفاصيل: {dashboard_url}/dashboard
⚠️ _هذه ليست نصيحة مالية._"""
        
        await update.message.reply_text(msg, parse_mode='Markdown')
        
        # تسجيل في Trade Logger
        if log_trade:
            log_trade(symbol=data['symbol'], action=data['action'], price=data['price'], 
                     score=data['score'], tp1=data['tp1'], tp2=data['tp2'], sl=data['sl'])
        
    except Exception as e:
        logger.error(f"Command error: {e}")
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
    await update.message.reply_text(f"""🌍 **بوت التداول الكمي المتقدم**
معادلات تفاضلية + Stochastic Calculus + Smart Money

الأوامر:
/smart <رمز> [تايم_فريم]
/stats
/dashboard

🔗 {dashboard_url}/dashboard""", parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_performance:
        stats = get_performance()
        await update.message.reply_text(f"📊 الإجمالي: {stats.get('total', 0)}\n✅ الرابحة: {stats.get('wins', 0)}\n❌ الخاسرة: {stats.get('losses', 0)}\n📈 النجاح: {stats.get('win_rate', 0)}%")
    else:
        await update.message.reply_text("⚠️ إحصائيات غير متاحة")

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
    await update.message.reply_text(f"🔗 {dashboard_url}/dashboard")

def main():
    logger.info("🧮 Starting bot with python-telegram-bot...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("smart", smart_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    
    logger.info("✅ Bot started successfully!")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
