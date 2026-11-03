import yfinance as yf
import numpy as np
import logging
import os
import requests
import threading
from io import BytesIO
from flask import Flask, jsonify
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

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

@app.route('/dashboard')
def dashboard():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Quant Trading Dashboard</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', Tahoma, sans-serif; 
                background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
                color: #fff;
                min-height: 100vh;
                padding: 20px;
            }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { 
                text-align: center; 
                margin-bottom: 30px;
                background: linear-gradient(90deg, #00d2ff, #3a7bd5);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-size: 2.5em;
            }
            .subtitle { text-align: center; color: #aaa; margin-bottom: 40px; }
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 40px;
            }
            .stat-card {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 15px;
                padding: 25px;
                text-align: center;
                backdrop-filter: blur(10px);
            }
            .stat-card h3 { color: #888; font-size: 0.9em; margin-bottom: 10px; }
            .stat-card .value { font-size: 2.5em; font-weight: bold; color: #00d2ff; }
            .refresh-btn {
                display: block;
                margin: 20px auto;
                padding: 12px 30px;
                background: linear-gradient(90deg, #00d2ff, #3a7bd5);
                border: none;
                border-radius: 25px;
                color: #fff;
                font-size: 1em;
                cursor: pointer;
            }
            .status { 
                text-align: center; 
                padding: 10px; 
                background: rgba(0,200,83,0.1); 
                border-radius: 10px; 
                margin-bottom: 20px;
                color: #00c853;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Quant Trading Bot</h1>
            <p class="subtitle">SMC Edition - Dashboard</p>
            <div class="status">Bot is running successfully</div>
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>Total Trades</h3>
                    <div class="value">2</div>
                </div>
                <div class="stat-card">
                    <h3>Win Rate</h3>
                    <div class="value">0%</div>
                </div>
                <div class="stat-card">
                    <h3>Wins</h3>
                    <div class="value">0</div>
                </div>
                <div class="stat-card">
                    <h3>Pending</h3>
                    <div class="value">2</div>
                </div>
            </div>
            <button class="refresh-btn" onclick="location.reload()">Refresh</button>
        </div>
    </body>
    </html>
    """

def run_flask():
    try:
        port = int(os.environ.get("PORT", 8080))
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Flask error: {e}")

def advanced_differential_analysis(closes):
    try:
        if not closes or len(closes) < 10:
            return None
        prices = np.array(closes[-20:], dtype=float)
        velocity = np.diff(prices)
        current_velocity = float(velocity[-1])
        acceleration = float(np.diff(velocity)[-1]) if len(velocity) >= 2 else 0.0
        
        if current_velocity > 0 and acceleration > 0:
            trend = "صاعد متسارع"
            trend_score = 2
        elif current_velocity > 0:
            trend = "صاعد متباطئ"
            trend_score = 1
        elif current_velocity < 0 and acceleration < 0:
            trend = "هابط متسارع"
            trend_score = -2
        elif current_velocity < 0:
            trend = "هابط متباطئ"
            trend_score = -1
        else:
            trend = "محايد"
            trend_score = 0
        
        return {
            'velocity': current_velocity,
            'acceleration': acceleration,
            'trend': trend,
            'trend_score': trend_score
        }
    except Exception as e:
        logger.error(f"Differential error: {e}")
        return None

def calculate_stochastic_analysis(prices):
    try:
        if not prices or len(prices) < 10:
            return {}
        arr = np.array(prices, dtype=float)
        log_returns = np.diff(np.log(arr))
        daily_volatility = np.std(log_returns)
        drift = np.mean(log_returns)
        sharpe_ratio = (drift - 0.02/252) / daily_volatility if daily_volatility != 0 else 0
        return {'sharpe_ratio': sharpe_ratio, 'var_95': float(np.percentile(log_returns, 5))}
    except Exception as e:
        logger.error(f"Stochastic error: {e}")
        return {}

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
        return {
            'closes': cleaned,
            'highs': highs[:len(cleaned)],
            'lows': lows[:len(cleaned)],
            'volumes': volumes[:len(cleaned)]
        }
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return None

def calc_rsi(prices, period=14):
    try:
        if len(prices) < period + 1:
            return 50.0
        gains = [max(prices[i] - prices[i-1], 0) for i in range(-period, 0)]
        losses = [max(-(prices[i] - prices[i-1]), 0) for i in range(-period, 0)]
        avg_gain = sum(gains)/period
        avg_loss = sum(losses)/period
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    except:
        return 50.0

def calc_atr(highs, lows, closes, period=14):
    try:
        if len(closes) < period + 1:
            return closes[-1] * 0.02
        tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(-period, 0)]
        return sum(tr) / period
    except:
        return closes[-1] * 0.02

def get_recommendation(rsi, sharpe, smc_score, smc_zone, diff_trend_score):
    score = 0
    if rsi < 30:
        score += 3
    elif rsi > 70:
        score -= 3
    if sharpe > 1.0:
        score += 2
    elif sharpe < -0.5:
        score -= 2
    if smc_score:
        score += smc_score
    if smc_zone == 'LOWER_DISCOUNT':
        score += 2
    elif smc_zone == 'UPPER_PREMIUM':
        score -= 2
    score += diff_trend_score
    
    if score >= 4:
        return 'شراء قوي', score, 'عالية'
    elif score >= 1:
        return 'شراء حذر', score, 'متوسطة'
    elif score <= -4:
        return 'بيع قوي', score, 'عالية'
    elif score <= -1:
        return 'بيع حذر', score, 'متوسطة'
    else:
        return 'انتظار', score, 'منخفضة'

def unified_analysis(symbol, interval="4h"):
    try:
        data = fetch_data(symbol, interval)
        if not data:
            return None
        closes = data['closes']
        highs = data['highs']
        lows = data['lows']
        price = float(closes[-1])
        rsi = calc_rsi(closes)
        atr = calc_atr(highs, lows, closes)
        stochastic = calculate_stochastic_analysis(closes)
        diff_analysis = advanced_differential_analysis(closes)
        diff_trend_score = diff_analysis.get('trend_score', 0) if diff_analysis else 0
        
        smc_score = 0
        smc_zone = 'محايد'
        if get_smc_analysis:
            try:
                smc_data = get_smc_analysis(closes, highs, lows, data.get('volumes', []))
                smc_score = smc_data.get('score', 0)
                smc_zone = smc_data.get('premium_discount', {}).get('zone', 'محايد')
            except:
                pass
        
        action, score, conf = get_recommendation(rsi, stochastic.get('sharpe_ratio', 0), smc_score, smc_zone, diff_trend_score)
        
        if 'شراء' in action:
            sl = price - (atr * 0.8)
            tp1 = price + (atr * 1.5)
            tp2 = price + (atr * 2.5)
            tp3 = price + (atr * 3.5)
        elif 'بيع' in action:
            sl = price + (atr * 0.8)
            tp1 = price - (atr * 1.5)
            tp2 = price - (atr * 2.5)
            tp3 = price - (atr * 3.5)
        else:
            sl = price - (atr * 0.8)
            tp1 = price + (atr * 1.5)
            tp2 = price + (atr * 2.5)
            tp3 = price + (atr * 3.5)
        
        rr = abs(tp1 - price) / abs(price - sl) if abs(price - sl) > 0 else 0
        
        return {
            'symbol': symbol,
            'price': price,
            'interval': interval,
            'rsi': rsi,
            'atr': atr,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'tp3': tp3,
            'rr': rr,
            'action': action,
            'score': score,
            'confidence': conf,
            'stochastic': stochastic,
            'smc_zone': smc_zone,
            'differential': diff_analysis
        }
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return None

def ask_qwen_ai(data):
    if not OPENROUTER_API_KEY:
        return None
    try:
        diff_info = ""
        if data.get('differential'):
            diff = data['differential']
            diff_info = f"المشتقات: {diff['trend']}. "
        
        prompt = (
            f"أنت محلل تداول محترف. "
            f"الرمز: {data['symbol']}. "
            f"السعر: {data['price']}. "
            f"التوصية: {data['action']}. "
            f"RSI: {data['rsi']:.1f}. "
            f"Sharpe: {data['stochastic'].get('sharpe_ratio', 0):.2f}. "
            f"المنطقة: {data['smc_zone']}. "
            f"{diff_info}"
            f"أعطِ جملة واحدة فقط بالعربية تشرح سبب التوصية. "
            f"لا تستخدم ** أو # أو أي رموز."
        )
        
        res = requests.post(
            OPENROUTER_API_URL,
            json={
                "model": "qwen/qwen-2.5-72b-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 150
            },
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/tysear/trading-bot",
                "X-Title": "Quant Trading Bot"
            },
            timeout=30
        )
        
        if res.status_code == 200:
            result = res.json()
            if 'choices' in result and len(result['choices']) > 0:
                analysis = result['choices'][0]['message']['content'].strip()
                analysis = analysis.replace('**', '').replace('*', '').replace('#', '').replace('`', '')
                if len(analysis) > 200:
                    analysis = analysis[:197] + "..."
                return analysis
    except Exception as e:
        logger.error(f"Qwen AI error: {e}")
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
        
        await update.message.reply_text(f"جاري تحليل {symbol}...")
        
        data = unified_analysis(symbol, interval)
        if not data:
            await update.message.reply_text(f"فشل تحليل {symbol}. تأكد من صحة الرمز.")
            return
        
        ai_analysis = None
        if OPENROUTER_API_KEY:
            await update.message.reply_text("جاري تحليل الذكاء الاصطناعي...")
            ai_analysis = ask_qwen_ai(data)
        
        if generate_signal_image:
            try:
                image_bytes = generate_signal_image(data)
                await update.message.reply_photo(photo=BytesIO(image_bytes), caption=f"{symbol}")
            except Exception as e:
                logger.error(f"Image error: {e}")
        
        emoji = ""
        if 'شراء' in data['action']:
            emoji = ""
        elif 'بيع' in data['action']:
            emoji = ""
        else:
            emoji = ""
        
        dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
        
        diff_section = ""
        if data.get('differential'):
            diff = data['differential']
            diff_section = (
                f"\nالمشتقات التفاضلية:\n"
                f"- السرعة: {diff['velocity']:+.2f}\n"
                f"- التسارع: {diff['acceleration']:+.2f}\n"
                f"- الاتجاه: {diff['trend']}\n"
            )
        
        ai_section = ""
        if ai_analysis:
            ai_section = f"\nتحليل الذكاء الاصطناعي:\n{ai_analysis}\n"
        else:
            ai_section = f"\nRSI: {data['rsi']:.1f} | Sharpe: {data['stochastic'].get('sharpe_ratio', 0):.2f} | المنطقة: {data['smc_zone']}\n"
        
        msg = (
            f"{emoji} {data['symbol']} ({data['interval'].upper()})\n"
            f"السعر: {data['price']:,.2f}\n"
            f"التوصية: {data['action']} (Score: {data['score']})\n"
            f"العائد/المخاطرة: 1:{data['rr']:.2f}\n"
            f"\n"
            f"مستويات التداول:\n"
            f"SL: {data['sl']:,.2f}\n"
            f"TP1: {data['tp1']:,.2f}\n"
            f"TP2: {data['tp2']:,.2f}\n"
            f"TP3: {data['tp3']:,.2f}\n"
            f"{diff_section}"
            f"{ai_section}"
            f"\n"
            f"للتفاصيل: {dashboard_url}/dashboard\n"
            f"هذه ليست نصيحة مالية."
        )
        
        await update.message.reply_text(msg)
        
        if log_trade:
            try:
                log_trade(
                    symbol=data['symbol'],
                    action=data['action'],
                    price=data['price'],
                    score=data['score'],
                    tp1=data['tp1'],
                    tp2=data['tp2'],
                    tp3=data['tp3'],
                    sl=data['sl']
                )
                logger.info(f"Trade logged: {data['symbol']}")
            except Exception as e:
                logger.error(f"Log error: {e}")
        
    except Exception as e:
        logger.error(f"Command error: {e}")
        await update.message.reply_text(f"حدث خطأ: {str(e)}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
    msg = (
        f"بوت التداول الكمي المتقدم\n"
        f"معادلات تفاضلية + Stochastic Calculus + Smart Money + ذكاء اصطناعي\n"
        f"\n"
        f"الأوامر:\n"
        f"/smart <رمز> [تايم_فريم]\n"
        f"/stats\n"
        f"/dashboard\n"
        f"\n"
        f"{dashboard_url}/dashboard"
    )
    await update.message.reply_text(msg)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_performance:
        stats = get_performance()
        msg = (
            f"الإجمالي: {stats.get('total', 0)}\n"
            f"الرابحة: {stats.get('wins', 0)}\n"
            f"الخاسرة: {stats.get('losses', 0)}\n"
            f"النجاح: {stats.get('win_rate', 0)}%"
        )
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("إحصائيات غير متاحة")

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
    await update.message.reply_text(f"{dashboard_url}/dashboard")

def main():
    logger.info("Starting bot...")
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask Dashboard started")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("smart", smart_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    
    logger.info("Bot started successfully!")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
