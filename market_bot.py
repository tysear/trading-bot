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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot running!"

@app.route('/health')
def health():
    return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Send failed: {resp.text}")
    except Exception as e:
        logger.error(f"Send error: {e}")

def send_long_report(report):
    max_length = 3800
    if len(report) <= max_length:
        send_message(report)
        return
    parts = []
    while len(report) > 0:
        if len(report) <= max_length:
            parts.append(report)
            break
        cut_point = report.rfind('\n', 0, max_length)
        if cut_point == -1:
            cut_point = max_length
        parts.append(report[:cut_point])
        report = report[cut_point:]
    total = len(parts)
    for i, part in enumerate(parts, 1):
        if i < total:
            send_message(f"{part}\n\n⏳ يتبع... ({i}/{total})")
        else:
            send_message(f"{part}\n\n✅ انتهى التقرير ({i}/{total})")
        time.sleep(0.5)

def fetch_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty:
            return None
        return {
            'closes': hist['Close'].tolist(),
            'highs': hist['High'].tolist(),
            'lows': hist['Low'].tolist(),
            'volumes': hist['Volume'].tolist()
        }
    except Exception as e:
        logger.error(f"Fetch error {symbol}: {e}")
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
        logger.error(f"News error: {e}")
        return ["تعذر جلب الأخبار."]

def calc_rsi(prices, period=14):
    try:
        if len(prices) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(-period, 0):
            change = prices[i] - prices[i-1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
    except:
        return 50.0

def calc_ema(prices, period):
    try:
        if len(prices) < period:
            return prices[-1]
        multiplier = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema
    except:
        return prices[-1]

def calc_macd(prices):
    try:
        return calc_ema(prices, 12) - calc_ema(prices, 26)
    except:
        return 0.0

def calc_atr(highs, lows, closes, period=14):
    try:
        if len(closes) < period + 1:
            return closes[-1] * 0.02
        true_ranges = []
        for i in range(-period, 0):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            true_ranges.append(tr)
        return sum(true_ranges) / period
    except:
        return closes[-1] * 0.02

def calc_ou_model(prices):
    try:
        closes = np.array(prices, dtype=float)
        returns = np.diff(np.log(closes))
        lookback = min(50, len(closes) - 1)
        recent = closes[-lookback:]
        mu = float(np.mean(recent))
        sigma = float(np.std(returns[-lookback:])) * np.sqrt(252)
        current = float(closes[-1])
        z_score = (current - mu) / (sigma * current) if sigma > 0 else 0.0
        return {'mu': mu, 'sigma': sigma, 'z_score': z_score}
    except:
        return {'mu': 0, 'sigma': 0, 'z_score': 0}

def calc_kelly(prices):
    try:
        closes = np.array(prices, dtype=float)
        wins = 0
        total = min(50, len(closes) - 5)
        for i in range(-total, -5):
            if closes[i+5] > closes[i]:
                wins += 1
        win_rate = wins / total if total > 0 else 0.5
        win_loss_ratio = 2.0
        edge = (win_rate * win_loss_ratio) - (1 - win_rate)
        kelly_full = edge / win_loss_ratio if win_loss_ratio > 0 else 0
        kelly_half = min(kelly_full / 2, 0.25)
        return {'win_rate': win_rate, 'kelly_half': max(kelly_half, 0)}
    except:
        return {'win_rate': 0.5, 'kelly_half': 0.1}

def monte_carlo(prices, days=30, simulations=500):
    try:
        closes = np.array(prices, dtype=float)
        returns = np.diff(np.log(closes))
        mu = float(np.mean(returns))
        sigma = float(np.std(returns))
        current = float(closes[-1])
        np.random.seed(42)
        final_prices = []
        for _ in range(simulations):
            price = current
            for _ in range(days):
                z = np.random.normal(0, 1)
                price = price * np.exp(mu + sigma * z)
            final_prices.append(price)
        final_prices = np.array(final_prices)
        prob_profit = float(np.sum(final_prices > current) / simulations * 100)
        prob_loss_10 = float(np.sum(final_prices < current * 0.9) / simulations * 100)
        p5 = float(np.percentile(final_prices, 5))
        p95 = float(np.percentile(final_prices, 95))
        return {
            'prob_profit': prob_profit,
            'prob_loss_10': prob_loss_10,
            'p5': p5,
            'p95': p95
        }
    except:
        return {'prob_profit': 50, 'prob_loss_10': 20, 'p5': 0, 'p95': 0}

def calc_vector_gradient_strength(closes, volumes):
    """
    حساب قوة الاتجاه المتجهية (VGS)
    باستخدام القيم العظمى والصغرى للمشتقات المتجهية
    ‖∇f‖ = ((∂f/∂P)² + (∂f/∂V)² + (∂f/∂R)² + (f/∂M)²)
    """
    try:
        if len(closes) < 20:
            return {
                'vgs': 0, 'direction': 'محايد', 'strength': 'ضعيف',
                'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0,
                'direction_score': 0
            }
        
        prices = np.array(closes, dtype=float)
        vols = np.array(volumes, dtype=float) if len(volumes) == len(closes) else np.ones(len(closes))
        
        # حساب المشتقات الجزئية (التغيرات المعيارية)
        # ∂f/P: تغير السعر المعياري
        recent_prices = prices[-10:]
        grad_P = (recent_prices[-1] - recent_prices[0]) / (recent_prices[0] + 1e-10)
        
        # ∂f/∂V: تغير الحجم المعياري
        recent_vols = vols[-10:]
        avg_vol = np.mean(vols[-20:]) + 1e-10
        grad_V = (np.mean(recent_vols[-5:]) - np.mean(recent_vols[:5])) / avg_vol
        
        # ∂f/∂R: تغير RSI المعياري
        rsi_values = []
        for i in range(14, len(prices) + 1):
            rsi_values.append(calc_rsi(prices[:i].tolist()))
        recent_rsi = rsi_values[-10:]
        grad_R = (recent_rsi[-1] - recent_rsi[0]) / 100.0
        
        # ∂f/∂M: تغير MACD المعياري
        macd_values = []
        for i in range(26, len(prices) + 1):
            macd_values.append(calc_ema(prices[:i].tolist(), 12) - calc_ema(prices[:i].tolist(), 26))
        if len(macd_values) >= 10:
            recent_macd = macd_values[-10:]
            avg_macd = np.mean(np.abs(macd_values[-20:])) + 1e-10
            grad_M = (np.mean(recent_macd[-5:]) - np.mean(recent_macd[:5])) / avg_macd
        else:
            grad_M = 0.0
        
        # الأوزان
        w1, w2, w3, w4 = 0.4, 0.2, 0.2, 0.2
        
        # حساب التدرج الموزون
        grad_P_w = w1 * grad_P
        grad_V_w = w2 * grad_V
        grad_R_w = w3 * grad_R
        grad_M_w = w4 * grad_M
        
        # معيار التدرج (القيمة العظمى للمشتقة الاتجاهية)
        vgs = np.sqrt(grad_P_w**2 + grad_V_w**2 + grad_R_w**2 + grad_M_w**2)
        
        # اتجاه التدرج (إشارة الشراء/البيع)
        direction_score = grad_P_w + grad_V_w + grad_R_w + grad_M_w
        
        # تحديد القوة
        if vgs > 0.15:
            strength = 'قوي جداً 🔥'
        elif vgs > 0.08:
            strength = 'قوي 💪'
        elif vgs > 0.03:
            strength = 'متوسط '
        else:
            strength = 'ضعيف 😴'
        
        # تحديد الاتجاه
        if direction_score > 0.02:
            direction = 'صعودي '
        elif direction_score < -0.02:
            direction = 'هبوطي '
        else:
            direction = 'محايد ⚪'
        
        return {
            'vgs': float(vgs),
            'direction': direction,
            'strength': strength,
            'grad_P': float(grad_P),
            'grad_V': float(grad_V),
            'grad_R': float(grad_R),
            'grad_M': float(grad_M),
            'direction_score': float(direction_score)
        }
    except Exception as e:
        logger.error(f"VGS error: {e}")
        return {
            'vgs': 0, 'direction': 'محايد', 'strength': 'خطأ',
            'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0,
            'direction_score': 0
        }

def unified_analysis(symbol):
    try:
        data = fetch_data(symbol)
        if not data or len(data['closes']) < 50:
            return None
        closes = data['closes']
        highs = data['highs']
        lows = data['lows']
        volumes = data.get('volumes', [])
        current_price = float(closes[-1])
        rsi = calc_rsi(closes)
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        macd = calc_macd(closes)
        atr = calc_atr(highs, lows, closes)
        ou = calc_ou_model(closes)
        kelly = calc_kelly(closes)
        mc = monte_carlo(closes)
        news = fetch_news(symbol)
        
        # ✅ حساب مؤشر VGS (القيم العظمى والصغرى للمشتقات المتجهية)
        vgs_data = calc_vector_gradient_strength(closes, volumes)
        
        vol_msg = "محايد"
        if len(volumes) >= 20:
            recent_vol = np.mean(volumes[-5:])
            avg_vol = np.mean(volumes[-20:])
            if recent_vol > avg_vol * 1.2:
                vol_msg = "مرتفع "
            elif recent_vol < avg_vol * 0.8:
                vol_msg = "منخفض 📉"
        
        buy_signals = 0
        if current_price > ema20: buy_signals += 1
        if current_price > ema50: buy_signals += 1
        if macd > 0: buy_signals += 1
        if rsi < 75: buy_signals += 1
        if mc['prob_profit'] > 55: buy_signals += 1
        if ou['z_score'] < 1.5: buy_signals += 1
        # ✅ إضافة إشارة VGS
        if vgs_data['direction'] == 'صعودي 🟢' and vgs_data['vgs'] > 0.05:
            buy_signals += 1
        
        direction = 'شراء' if buy_signals >= 4 else ('بيع' if buy_signals <= 2 else 'انتظار')
        
        if direction == 'شراء':
            sl = current_price - (atr * 1.5)
            tp1 = current_price + (atr * 2)
            tp2 = current_price + (atr * 3)
            tp3 = current_price + (atr * 4.5)
        elif direction == 'بيع':
            sl = current_price + (atr * 1.5)
            tp1 = current_price - (atr * 2)
            tp2 = current_price - (atr * 3)
            tp3 = current_price - (atr * 4.5)
        else:
            sl = current_price - (atr * 1.5)
            tp1 = current_price + (atr * 2)
            tp2 = current_price - (atr * 2)
            tp3 = current_price + (atr * 3)
        rr = abs(tp2 - current_price) / abs(current_price - sl) if abs(current_price - sl) > 0 else 0
        return {
            'symbol': symbol,
            'price': current_price,
            'rsi': rsi,
            'ema20': ema20,
            'ema50': ema50,
            'macd': macd,
            'atr': atr,
            'ou': ou,
            'kelly': kelly,
            'mc': mc,
            'vol_msg': vol_msg,
            'direction': direction,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'tp3': tp3,
            'rr': rr,
            'news': news,
            'buy_signals': buy_signals,
            'vgs': vgs_data
        }
    except Exception as e:
        logger.error(f"Analysis error {symbol}: {e}")
        return None

def ask_qwen(data):
    if not OPENROUTER_API_KEY:
        return None
    try:
        news_text = "\n".join(data.get('news', ['لا توجد أخبار']))
        vgs = data.get('vgs', {})
        prompt = f"""أنت محلل مالي خبير. حلل البيانات التالية بالعربية:

الرمز: {data['symbol']}
السعر: ${data['price']:,.2f}
RSI: {data['rsi']:.1f}
EMA20: ${data['ema20']:,.2f} | EMA50: ${data['ema50']:,.2f}
MACD: {data['macd']:.2f}
ATR: ${data['atr']:,.2f}
OU Z-Score: {data['ou']['z_score']:.2f}
Kelly: {data['kelly']['kelly_half']*100:.1f}%
احتمال الربح: {data['mc']['prob_profit']:.1f}%
حجم التداول: {data['vol_msg']}
إشارات الشراء: {data['buy_signals']}/7
معيار التدرج ‖∇f‖ (VGS): {vgs.get('vgs', 0):.3f}
قوة الاتجاه: {vgs.get('strength', 'محايد')}
اتجاه التدرج: {vgs.get('direction', 'محايد')}

الأخبار:
{news_text}

أعطِ:
1. القرار: (شراء قوي/شراء/انتظار/بيع/بيع قوي)
2. التفسير المختصر
3. المخاطر الرئيسية
4. نصيحة عملية

⚠️ ليس نصيحة مالية."""
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
            "max_tokens": 1000
        }
        res = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=30)
        if res.status_code == 200:
            result = res.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
        return None
    except Exception as e:
        logger.error(f"Qwen error: {e}")
        return None

def send_unified_report(data, ai_analysis=None):
    news_text = "\n".join(data.get('news', ['لا توجد أخبار']))
    
    if data['direction'] == 'شراء':
        direction_display = 'شراء 🟢'
    elif data['direction'] == 'بيع':
        direction_display = 'بيع 🔴'
    else:
        direction_display = 'انتظار ⚪'
    
    vgs = data.get('vgs', {})
    
    part1 = f"""🎯 <b>التقرير الشامل الموحد لـ {data['symbol']}</b>
{'='*45}

💰 <b>السعر الحالي:</b> ${data['price']:,.2f}
📊 <b>الاتجاه:</b> {direction_display}

📈 <b>التحليل الفني:</b>
• RSI: {data['rsi']:.1f}
• MACD: {data['macd']:.2f}
• EMA20: ${data['ema20']:,.2f}
• EMA50: ${data['ema50']:,.2f}
• ATR: ${data['atr']:,.2f}
• حجم التداول: {data['vol_msg']}"""
    send_long_report(part1)
    
    part2 = f"""🧮 <b>نموذج OU (العودة للمتوسط):</b>
• Z-Score: {data['ou']['z_score']:.2f}

💰 <b>Kelly Criterion:</b>
• نسبة النجاح: {data['kelly']['win_rate']*100:.1f}%
• الحجم الآمن: {data['kelly']['kelly_half']*100:.1f}%

 <b>مونت كارلو (30 يوم):</b>
• احتمال الربح: {data['mc']['prob_profit']:.1f}%
• احتمال خسارة 10%+: {data['mc']['prob_loss_10']:.1f}%
• نطاق 90%: ${data['mc']['p5']:,.2f} - ${data['mc']['p95']:,.2f}"""
    send_long_report(part2)
    
    # ✅ إضافة قسم VGS
    part_vgs = f"""🎯 <b>قوة الاتجاه المتجهية (VGS):</b>
 المعادلة: ‖∇f‖ = √((∂f/∂P)² + (∂f/∂V)² + (∂f/∂R)² + (∂f/∂M)²)

• معيار التدرج ‖f‖: {vgs.get('vgs', 0):.3f}
• القوة: {vgs.get('strength', 'محايد')}
• اتجاه التدرج: {vgs.get('direction', 'محايد')}
• ∂f/∂P (السعر): {vgs.get('grad_P', 0):.3f}
• ∂f/V (الحجم): {vgs.get('grad_V', 0):.3f}
• f/∂R (RSI): {vgs.get('grad_R', 0):.3f}
• ∂f/∂M (MACD): {vgs.get('grad_M', 0):.3f}

💡 <i>كلما ارتفع معيار التدرج، زادت قوة الاتجاه. القيم الموجبة للمشتقات الجزئية تؤكد الاتجاه الصعودي.</i>"""
    send_long_report(part_vgs)
    
    part3 = f"""📰 <b>آخر الأخبار:</b>
{news_text}"""
    send_long_report(part3)
    
    if ai_analysis:
        part4 = f"""{'='*45}
🤖 <b>تحليل Qwen AI:</b>
{'='*45}
{ai_analysis}"""
        send_long_report(part4)
    
    part5 = f"""{'='*45}
💰 <b>مستويات التداول:</b>
• وقف الخسارة (SL): ${data['sl']:,.2f}
• الهدف 1 (TP1): ${data['tp1']:,.2f}
• الهدف 2 (TP2): ${data['tp2']:,.2f}
• الهدف 3 (TP3): ${data['tp3']:,.2f}
• المخاطرة/العائد: 1:{data['rr']:.2f}

⚠️ <i>هذا ليس نصيحة مالية. تداول بمسؤوليتك.</i>"""
    send_long_report(part5)

def smart_analysis(symbol):
    send_message(f"🔍 جاري التحليل الشامل لـ {symbol}...")
    data = unified_analysis(symbol)
    if not data:
        send_message(f"❌ فشل تحليل {symbol}. تأكد من الرمز.")
        return
    send_message(" جاري دمج التحليل مع الذكاء الاصطناعي...")
    ai = ask_qwen(data)
    send_unified_report(data, ai)

def simple_report():
    try:
        report = "📊 <b>تقرير السوق</b>\n\n"
        report += f" {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        symbols = ['BTC-USD', 'ETH-USD', 'AAPL']
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    report += f"✅ <b>{symbol}</b>: ${price:.2f}\n"
                else:
                    report += f"⚠️ <b>{symbol}</b>: لا بيانات\n"
            except Exception as e:
                report += f"❌ <b>{symbol}</b>: خطأ\n"
        send_message(report)
    except Exception as e:
        send_message(f"خطأ: {str(e)}")

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start':
        send_message("🤖 <b>بوت التداول الذكي الشامل</b>\n\nالأوامر:\n/smart <رمز> - تحليل شامل موحد\n/test_report - تقرير بسيط\n/help - المساعدة")
    elif cmd == '/test_report':
        send_message("⏳ جاري...")
        simple_report()
    elif cmd.startswith('/smart'):
        parts = command.split()
        if len(parts) < 2:
            send_message("الاستخدام: /smart <رمز>\nمثال: /smart BTC-USD")
            return
        symbol = parts[1].upper()
        smart_analysis(symbol)
    elif cmd == '/help':
        send_message("<b>الأوامر:</b>\n/smart <رمز> - تحليل شامل\n/test_report - تقرير بسيط\n/help - مساعدة")
    else:
        send_message("استخدم /help")

def listen():
    last_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_id + 1, "timeout": 10}
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('ok') and data.get('result'):
                    for update in data['result']:
                        if 'message' in update and 'text' in update['message']:
                            chat_id = str(update['message']['chat']['id'])
                            if chat_id == CHAT_ID:
                                handle_command(update['message']['text'])
                        last_id = update['update_id']
        except Exception as e:
            logger.error(f"Listen error: {e}")
        time.sleep(1)

if __name__ == "__main__":
    logger.info("Starting Unified Trading Bot with VGS...")
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
