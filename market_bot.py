import requests
import time
import yfinance as yf
from datetime import datetime
import logging
import os
from flask import Flask
import threading
import numpy as np
import re

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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_API_URL = "https://finnhub.io/api/v1"

ALERTED_NEWS_FILE = 'alerted_news.txt'
URGENT_KEYWORDS = ['federal reserve', 'fed rate', 'inflation', 'cpi', 'jerome powell', 
                   'interest rate', 'recession', 'nonfarm payrolls', 'nfp', 'fomc', 'treasury']

CRYPTO_SYMBOLS = [
    'BTC-USD', 'ETH-USD', 'XRP-USD', 'BNB-USD', 'SOL-USD',
    'ADA-USD', 'DOGE-USD', 'DOT-USD', 'MATIC-USD', 'AVAX-USD',
    'LINK-USD', 'UNI-USD', 'ATOM-USD', 'LTC-USD', 'ETC-USD',
    'XLM-USD', 'ALGO-USD', 'VET-USD', 'FIL-USD', 'TRX-USD'
]

app = Flask(__name__)

@app.route('/')
def home(): return "Bot running!"
@app.route('/health')
def health(): return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def clean_markdown(text):
    if not text:
        return text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'```[a-z]*\n?', '', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text}
        logger.info(f"📤 إرسال رسالة إلى {CHAT_ID}")
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code != 200: 
            logger.error(f"❌ إرسال فشل: {resp.text}")
        else:
            logger.info("✅ تم إرسال الرسالة بنجاح")
    except Exception as e: 
        logger.error(f" خطأ في الإرسال: {e}")

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
        if cut_point == -1: cut_point = max_length
        parts.append(report[:cut_point])
        report = report[cut_point:]
    total = len(parts)
    for i, part in enumerate(parts, 1):
        suffix = f"\n\n⏳ يتبع... ({i}/{total})" if i < total else f"\n\n✅ انتهى التقرير ({i}/{total})"
        send_message(f"{part}{suffix}")
        time.sleep(0.5)

def load_alerted_news():
    try:
        if os.path.exists(ALERTED_NEWS_FILE):
            with open(ALERTED_NEWS_FILE, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
    except: pass
    return set()

def save_alerted_news(headline):
    try:
        with open(ALERTED_NEWS_FILE, 'a', encoding='utf-8') as f:
            f.write(headline + '\n')
    except: pass

def monitor_urgent_news():
    logger.info("🔍 بدء مراقبة الأخبار العاجلة...")
    while True:
        try:
            time.sleep(1800)
            if not FINNHUB_API_KEY: continue
            url = f"{FINNHUB_API_URL}/news?category=general&token={FINNHUB_API_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                news_list = resp.json()[:10]
                alerted = load_alerted_news()
                for item in news_list:
                    headline = item.get('headline', '').lower()
                    if any(keyword in headline for keyword in URGENT_KEYWORDS):
                        original = item.get('headline', '')
                        if original not in alerted:
                            send_message(f" تنبيه عاجل: {original}\n {item.get('url', '')}")
                            save_alerted_news(original)
        except Exception as e: logger.error(f"خطأ في مراقبة الأخبار: {e}")

def fetch_data(symbol, interval="1h"):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1mo", interval=interval)
        if hist.empty: return None
        closes, highs, lows, volumes = hist['Close'].tolist(), hist['High'].tolist(), hist['Low'].tolist(), hist['Volume'].tolist()
        cleaned = [{'close': c, 'high': h, 'low': l, 'volume': v} for c, h, l, v in zip(closes, highs, lows, volumes) 
                   if not np.isnan(c) and not np.isnan(h) and not np.isnan(l) and not np.isnan(v) and c > 0]
        if len(cleaned) < 50: return None
        return {'closes': [d['close'] for d in cleaned], 'highs': [d['high'] for d in cleaned], 
                'lows': [d['low'] for d in cleaned], 'volumes': [d['volume'] for d in cleaned], 'interval': interval}
    except Exception as e:
        logger.error(f"خطأ في جلب البيانات {symbol}: {e}")
        return None

def fetch_order_book(symbol):
    if symbol not in CRYPTO_SYMBOLS: return None
    binance_symbol = symbol.replace('-USD', 'USDT')
    try:
        resp = requests.get(f"https://api.binance.com/api/v3/depth?symbol={binance_symbol}&limit=20", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('bids') and data.get('asks'):
                bids, asks = data['bids'][:10], data['asks'][:10]
                tbv = sum(float(b[1]) for b in bids)
                tav = sum(float(a[1]) for a in asks)
                tv = tbv + tav
                if tv == 0: return None
                bp, sp = (tbv/tv)*100, (tav/tv)*100
                bb, ba = float(bids[0][0]), float(asks[0][0])
                spread = ba - bb
                sups = [{'price': float(b[0]), 'volume': float(b[1])} for b in bids if float(b[1]) > tbv * 0.15][:3]
                ress = [{'price': float(a[0]), 'volume': float(a[1])} for a in asks if float(a[1]) > tav * 0.15][:3]
                return {'best_bid': bb, 'best_ask': ba, 'spread': spread, 'spread_percent': (spread/ba)*100 if ba>0 else 0,
                        'buy_pressure': bp, 'sell_pressure': sp, 'support_levels': sups, 'resistance_levels': ress, 'source': 'Binance', 'estimated': False}
    except: pass
    return None

def analyze_order_book(ob):
    if not ob: return None
    bp, sp = ob['buy_pressure'], ob['sell_pressure']
    if bp > 65: sig, strn = 'ضغط شراء قوي', 'قوي'
    elif bp > 55: sig, strn = 'ضغط شراء معتدل', 'متوسط'
    elif sp > 65: sig, strn = 'ضغط بيع قوي', 'قوي'
    elif sp > 55: sig, strn = 'ضغط بيع معتدل', 'متوسط'
    else: sig, strn = 'توازن', 'ضعيف'
    return {'signal': sig, 'strength': strn, 'buy_pressure': bp, 'sell_pressure': sp, 'source': ob.get('source'), 'estimated': ob.get('estimated')}

def calc_damped_oscillator(closes, period=20):
    try:
        prices = np.array(closes[-period:], dtype=float)
        fv = np.mean(prices)
        y = prices - fv
        yp, ypp = np.gradient(y), np.gradient(np.gradient(y))
        omega_sq = max(0.01, abs(-ypp[-1] / y[-1])) if abs(y[-1]) > 1e-10 else 0.01
        gamma = abs(-ypp[-1] - omega_sq * y[-1]) / (2 * abs(yp[-1])) if abs(yp[-1]) > 1e-10 else 0.3
        damp = gamma / (2 * np.sqrt(omega_sq))
        phase = np.arctan(yp[-1] / (y[-1] + 1e-10))
        if y[-1] < 0: phase += np.pi
        pos = 'قاع' if np.pi/2 < phase < 3*np.pi/2 else 'قمة'
        return {'regime': 'تذبذب نشط' if damp < 1 else 'تخميد عالي', 'position': pos, 'damping': damp, 'prediction': fv + y[-1] + yp[-1] + 0.5*ypp[-1]}
    except: return {'regime': 'N/A', 'position': 'N/A', 'damping': 0, 'prediction': closes[-1]}

def calc_diffusion_model(closes, volumes, period=20):
    try:
        prices, vols = np.array(closes[-period:], dtype=float), np.array(volumes[-period:], dtype=float)
        fd, sd = np.gradient(prices), np.gradient(np.gradient(prices))
        volat = np.std(fd) + 1e-10
        D = volat * np.log10(np.mean(vols) + 1e-10)
        diff_val = D * sd[-1]
        normalized_signal = diff_val / (volat * 10 + 1e-10)
        return {'signal': 1 if diff_val > 0 else -1, 
                'strength': min(1.0, abs(diff_val) / (volat * 10)), 
                'curvature': sd[-1], 
                'D': D,
                'normalized_signal': float(np.clip(normalized_signal, -10, 10))}
    except: return {'signal': 0, 'strength': 0, 'curvature': 0, 'D': 0, 'normalized_signal': 0}

def calc_forced_motion(closes, volumes, period=20):
    try:
        prices = np.array(closes[-period:], dtype=float)
        y, yp, ypp = prices - np.mean(prices), np.gradient(prices - np.mean(prices)), np.gradient(np.gradient(prices - np.mean(prices)))
        k, c, m = 0.1, 0.3, 1.0
        int_f = -c * yp[-1] - k * y[-1]
        ext_f = m * ypp[-1] - int_f
        return {'internal_force': int_f, 'external_force': ext_f, 'news_dominance': abs(ext_f) > abs(int_f) * 1.5, 'prediction': np.mean(prices) + y[-1] + yp[-1] + 0.5 * ((ext_f + int_f)/m)}
    except: return {'internal_force': 0, 'external_force': 0, 'news_dominance': False, 'prediction': closes[-1]}

def advanced_differential_analysis(closes, volumes):
    try:
        osc, diff, forced = calc_damped_oscillator(closes), calc_diffusion_model(closes, volumes), calc_forced_motion(closes, volumes)
        signals = []
        if 'قاع' in osc['position']: signals.append(1)
        elif 'قمة' in osc['position']: signals.append(-1)
        if diff['strength'] > 0.3: signals.append(diff['signal'])
        if abs(forced['external_force']) > abs(forced['internal_force']) * 1.5: signals.append(1 if forced['external_force'] > 0 else -1)
        return {'oscillator': osc, 'diffusion': diff, 'forced': forced, 'final_signal': np.mean(signals) if signals else 0, 'confidence': len(signals) / 3.0}
    except: return {'oscillator': {}, 'diffusion': {}, 'forced': {}, 'final_signal': 0, 'confidence': 0}

def fetch_news(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news: return ["لا توجد أخبار حديثة."]
        return [f"• {item.get('title', '')} ({item.get('publisher', '')})" for item in news[:3]]
    except: return ["تعذر جلب الأخبار."]

def calc_rsi(prices, period=14):
    try:
        if len(prices) < period + 1: return 50.0
        gains = [max(prices[i] - prices[i-1], 0) for i in range(-period, 0)]
        losses = [max(-(prices[i] - prices[i-1]), 0) for i in range(-period, 0)]
        avg_gain, avg_loss = sum(gains)/period, sum(losses)/period
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    except: return 50.0

def calc_ema(prices, period):
    try:
        if len(prices) < period: return prices[-1]
        mult = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]: ema = (p - ema) * mult + ema
        return ema
    except: return prices[-1]

def calc_atr(highs, lows, closes, period=14):
    try:
        if len(closes) < period + 1: return closes[-1] * 0.02
        tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(-period, 0)]
        return sum(tr) / period
    except: return closes[-1] * 0.02

def calc_kelly(prices):
    try:
        closes = np.array(prices, dtype=float)
        closes = closes[~np.isnan(closes)]
        if len(closes) < 10: return {'win_rate': 0.5, 'kelly_safe': 0.05}
        wins = sum(1 for i in range(-min(50, len(closes)-5), -5) if closes[i+5] > closes[i])
        total = min(50, len(closes) - 5)
        win_rate = wins / total if total > 0 else 0.5
        edge = (win_rate * 2.0) - (1 - win_rate)
        kelly_full = edge / 2.0 if 2.0 > 0 else 0
        safe_kelly = max(0.02, min(kelly_full * 0.5, 0.15))
        return {'win_rate': win_rate, 'kelly_safe': safe_kelly}
    except: return {'win_rate': 0.5, 'kelly_safe': 0.05}

def monte_carlo(prices, days=30, simulations=500):
    try:
        closes = np.array(prices, dtype=float)
        closes = closes[~np.isnan(closes)]
        if len(closes) < 10: return {'prob_profit': 50, 'prob_loss_10': 20, 'p5': 0, 'p95': 0}
        rets = np.diff(np.log(closes))
        mu, sigma = float(np.mean(rets)), float(np.std(rets))
        current = float(closes[-1])
        np.random.seed(42)
        final_prices = [current * np.exp(np.sum(np.random.normal(mu, sigma, days))) for _ in range(simulations)]
        fp_arr = np.array(final_prices)
        return {'prob_profit': float(np.sum(fp_arr > current) / simulations * 100),
                'prob_loss_10': float(np.sum(fp_arr < current * 0.9) / simulations * 100),
                'p5': float(np.percentile(fp_arr, 5)), 'p95': float(np.percentile(fp_arr, 95))}
    except: return {'prob_profit': 50, 'prob_loss_10': 20, 'p5': 0, 'p95': 0}

def calc_vgs(closes, volumes):
    try:
        if len(closes) < 20: return {'vgs': 0, 'direction': 'محايد', 'strength': 'ضعيف', 'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0, 'direction_score': 0}
        prices, vols = np.array(closes, dtype=float), np.array(volumes, dtype=float) if len(volumes)==len(closes) else np.ones(len(closes))
        mask = ~(np.isnan(prices) | np.isnan(vols))
        prices, vols = prices[mask], vols[mask]
        if len(prices) < 20: return {'vgs': 0, 'direction': 'محايد', 'strength': 'ضعيف', 'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0, 'direction_score': 0}
        
        gp = (prices[-1] - prices[-10]) / (prices[-10] + 1e-10)
        gv = (np.mean(vols[-5:]) - np.mean(vols[-15:-10])) / (np.mean(vols[-20:]) + 1e-10)
        rsi_vals = [calc_rsi(prices[:i].tolist()) for i in range(14, len(prices)+1)]
        gr = (rsi_vals[-1] - rsi_vals[-10]) / 100.0 if len(rsi_vals)>=10 else 0
        macd_vals = [calc_ema(prices[:i].tolist(), 12) - calc_ema(prices[:i].tolist(), 26) for i in range(26, len(prices)+1)]
        gm = (np.mean(macd_vals[-5:]) - np.mean(macd_vals[-15:-10])) / (np.mean(np.abs(macd_vals[-20:])) + 1e-10) if len(macd_vals)>=10 else 0
        
        w1, w2, w3, w4 = 0.4, 0.2, 0.2, 0.2
        gpw, gvw, grw, gmw = w1*gp, w2*gv, w3*gr, w4*gm
        vgs = np.sqrt(gpw**2 + gvw**2 + grw**2 + gmw**2)
        ds = gpw + gvw + grw + gmw
        
        strength = 'قوي جداً' if vgs > 0.15 else 'قوي' if vgs > 0.08 else 'متوسط' if vgs > 0.03 else 'ضعيف'
        direction = 'صعودي' if ds > 0.02 else 'هبوطي' if ds < -0.02 else 'محايد'
        return {'vgs': float(vgs), 'direction': direction, 'strength': strength, 'grad_P': float(gpw), 'grad_V': float(gvw), 'grad_R': float(grw), 'grad_M': float(gmw), 'direction_score': float(ds)}
    except: return {'vgs': 0, 'direction': 'محايد', 'strength': 'خطأ', 'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0, 'direction_score': 0}

def calc_vfm(closes, volumes):
    try:
        if len(closes) < 20: return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'محايد', 'strength': 'ضعيف'}
        prices, vols = np.array(closes, dtype=float), np.array(volumes, dtype=float) if len(volumes)==len(closes) else np.ones(len(closes))
        mask = ~(np.isnan(prices) | np.isnan(vols))
        prices, vols = prices[mask], vols[mask]
        if len(prices) < 20: return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'محايد', 'strength': 'ضعيف'}
        
        pc = np.diff(prices) / (prices[:-1] + 1e-10)
        vc = np.diff(vols) / (vols[:-1] + 1e-10)
        mom = np.gradient(prices)
        
        divs, curls, fluxs = [], [], []
        for i in range(-10, -1):
            if i-1 >= -len(pc):
                dp = (pc[i] - pc[i-1]) / (abs(pc[i-1]) + 1e-10)
                dv = (vc[i] - vc[i-1]) / (abs(vc[i-1]) + 1e-10)
                dm = (mom[i] - mom[i-1]) / (abs(mom[i-1]) + 1e-10)
                divs.append((dp + dv + dm) / 3)
                curls.append(np.sqrt(abs(dm-dv)**2 + abs(dm-dp)**2 + abs(dv-dp)**2))
                fluxs.append(pc[i] * vols[i])
                
        div = np.mean(divs) if divs else 0.0
        curl = np.mean(curls) if curls else 0.0
        flux = np.mean(fluxs) if fluxs else 0.0
        
        avg_vol = np.mean(vols)
        avg_price_change = np.mean(np.abs(pc))
        normalized_flux = flux / (avg_vol * avg_price_change + 1e-10)
        price_volatility = np.std(pc) + 1e-10
        normalized_div = div / price_volatility
        
        if normalized_div > 0.5 and normalized_flux > 0: 
            sig, strn = 'تدفق دخول قوي', 'قوي' if normalized_div > 1.0 else 'متوسط'
        elif normalized_div < -0.5 and normalized_flux < 0: 
            sig, strn = 'تدفق خروج قوي', 'قوي' if normalized_div < -1.0 else 'متوسط'
        elif curl > 0.3: 
            sig, strn = 'دوامات وتقلبات', 'عالي التقلب'
        else: 
            sig, strn = 'محايد', 'ضعيف'
        
        return {
            'divergence': float(np.clip(normalized_div, -10, 10)),
            'curl': float(curl),
            'flux': float(np.clip(normalized_flux, -10, 10)),
            'signal': sig,
            'strength': strn
        }
    except: return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'خطأ', 'strength': 'غير محدد'}

def get_action_recommendation(data):
    try:
        vgs = data.get('vgs', {})
        vfm = data.get('vfm', {})
        rsi = data.get('rsi', 50)
        kelly = data.get('kelly', {}).get('kelly_safe', 0.05)
        mc_prob = data.get('mc', {}).get('prob_profit', 50)
        ob_an = data.get('ob_analysis')
        diff_an = data.get('diff_analysis', {})
        diff_sig = diff_an.get('final_signal', 0)
        diff_conf = diff_an.get('confidence', 0)
        osc = diff_an.get('oscillator', {})
        diff_model = diff_an.get('diffusion', {})
        
        score = 0
        math_signals = []
        osc_position = osc.get('position', '')
        
        if diff_conf >= 0.66:
            if diff_sig > 0.5:
                score += 6
                math_signals.append(f"التفاضلي صعودي قوي ({diff_sig:.2f})")
            elif diff_sig < -0.5:
                score -= 6
                math_signals.append(f"التفاضلي هبوطي قوي ({diff_sig:.2f})")
        elif diff_conf >= 0.33:
            if diff_sig > 0.3:
                score += 3
                math_signals.append(f"التفاضلي صعودي ({diff_sig:.2f})")
            elif diff_sig < -0.3:
                score -= 3
                math_signals.append(f"التفاضلي هبوطي ({diff_sig:.2f})")
        
        if 'قمة' in osc_position:
            score -= 3
            math_signals.append(f"المذبذب عند قمة (خصم -3)")
        elif 'قاع' in osc_position:
            score += 3
            math_signals.append(f"المذبذب عند قاع (إضافة +3)")
        
        if ob_an:
            ob_signal = ob_an.get('signal', '')
            if 'شراء قوي' in ob_signal:
                score += 4
                math_signals.append(f"Order Book: ضغط شراء قوي ({ob_an['buy_pressure']:.1f}%)")
            elif 'شراء معتدل' in ob_signal:
                score += 2
            elif 'بيع قوي' in ob_signal:
                score -= 4
                math_signals.append(f"Order Book: ضغط بيع قوي ({ob_an['sell_pressure']:.1f}%)")
            elif 'بيع معتدل' in ob_signal:
                score -= 2
        
        vgs_dir = vgs.get('direction', 'محايد')
        vgs_val = vgs.get('vgs', 0)
        if vgs_dir == 'صعودي' and vgs_val > 0.08:
            score += 3
            math_signals.append(f"VGS صعودي قوي ({vgs_val:.3f})")
        elif vgs_dir == 'صعودي':
            score += 1.5
        elif vgs_dir == 'هبوطي' and vgs_val > 0.08:
            score -= 3
            math_signals.append(f"VGS هبوطي قوي ({vgs_val:.3f})")
        elif vgs_dir == 'هبوطي':
            score -= 1.5
        
        vfm_div = vfm.get('divergence', 0)
        vfm_flux = vfm.get('flux', 0)
        if vfm_div > 0.5 and vfm_flux > 0:
            score += 2
            math_signals.append(f"VFM: تدفق دخول ({vfm_div:.2f})")
        elif vfm_div < -0.5 and vfm_flux < 0:
            score -= 2
            math_signals.append(f"VFM: تدفق خروج ({vfm_div:.2f})")
        
        if rsi > 80:
            score -= 1
            math_signals.append(f"RSI تشبع شديد ({rsi:.1f})")
        elif rsi < 20:
            score += 1
            math_signals.append(f"RSI تشبع بيعي ({rsi:.1f})")
        
        macd = data.get('macd', 0)
        if macd > 0 and score > 0:
            score += 0.5
        elif macd < 0 and score < 0:
            score -= 0.5
        
        if mc_prob > 70 and score > 0:
            score += 1
        elif mc_prob < 30 and score < 0:
            score -= 1
        
        veto_warning = ""
        reversal_signals = 0
        if 'قاع' in osc_position: reversal_signals += 1
        if diff_model.get('signal', 0) > 0 and diff_model.get('strength', 0) > 0.3: reversal_signals += 1
        if diff_sig > 0.5 and diff_conf > 0.5: reversal_signals += 1
        
        if reversal_signals >= 2 and score < -3:
            score = 0
            veto_warning = f"فيتو انعكاسي: {reversal_signals}/3 نماذج تشير لقاع! تم إلغاء Short."
        elif 'قمة' in osc_position and score > 3:
            score = 0
            veto_warning = f"فيتو انعكاسي: المذبذب عند قمة! تم إلغاء Long."
        
        if score >= 8: action, emoji, conf = 'افتح شراء (Long) قوي جداً', '', 'عالية جداً'
        elif score >= 5: action, emoji, conf = 'افتح شراء (Long) قوي', '', 'عالية'
        elif score >= 2: action, emoji, conf = 'دخول حذر (Long)', '', 'متوسطة'
        elif score >= -1: action, emoji, conf = 'انتظار', '', 'منخفضة'
        elif score >= -2: action, emoji, conf = 'دخول حذر (Short)', '', 'متوسطة'
        elif score >= -5: action, emoji, conf = 'افتح بيع (Short) قوي', '', 'عالية'
        else: action, emoji, conf = 'افتح بيع (Short) قوي جداً', '', 'عالية جداً'
        
        desc_parts = [f"النقاط: {score}"]
        if math_signals:
            desc_parts.append(f" رياضي: {' | '.join(math_signals[:3])}")
        if veto_warning:
            desc_parts.append(veto_warning)
        desc_parts.append(f"(إشارات الانعكاس: {reversal_signals}/3)")
        desc = ". ".join(desc_parts)
        
        return {'action': action, 'emoji': emoji, 'trade_type': 'LONG' if score > 1 else ('SHORT' if score < -1 else 'WAIT'), 
                'description': desc, 'confidence': conf, 'score': score,
                'math_signals': math_signals, 'reversal_signals': reversal_signals}
    except Exception as e:
        logger.error(f"خطأ في التوصية: {e}")
        return {'action': 'انتظار', 'emoji': '', 'trade_type': 'WAIT', 'description': 'تعذر حساب التوصية', 'confidence': 'غير محدد', 'score': 0, 'math_signals': [], 'reversal_signals': 0}

def unified_analysis(symbol, interval="1h"):
    try:
        data = fetch_data(symbol, interval)
        if not data or len(data['closes']) < 50: return None
        closes, highs, lows, volumes = data['closes'], data['highs'], data['lows'], data.get('volumes', [])
        current_price = float(closes[-1])
        if np.isnan(current_price) or current_price <= 0: return None
        
        rsi = calc_rsi(closes)
        ema20, ema50 = calc_ema(closes, 20), calc_ema(closes, 50)
        macd = calc_ema(closes, 12) - calc_ema(closes, 26)
        atr = calc_atr(highs, lows, closes)
        kelly = calc_kelly(closes)
        mc = monte_carlo(closes)
        news = fetch_news(symbol)
        vgs_data = calc_vgs(closes, volumes)
        vfm_data = calc_vfm(closes, volumes)
        ob = fetch_order_book(symbol)
        ob_an = analyze_order_book(ob) if ob else None
        diff_an = advanced_differential_analysis(closes, volumes)
        
        vol_msg = "مرتفع" if len(volumes)>=20 and np.mean(volumes[-5:]) > np.mean(volumes[-20:])*1.2 else "منخفض" if len(volumes)>=20 and np.mean(volumes[-5:]) < np.mean(volumes[-20:])*0.8 else "محايد"
        
        direction = 'شراء' if current_price > ema20 and macd > 0 else ('بيع' if current_price < ema20 and macd < 0 else 'انتظار')
        
        rec = get_action_recommendation({'vgs': vgs_data, 'vfm': vfm_data, 'rsi': rsi, 'kelly': kelly, 'mc': mc, 
                                         'direction': direction, 'macd': macd, 'ob_analysis': ob_an, 'diff_analysis': diff_an})
        
        if rec['trade_type'] == 'LONG':
            sl, tp1, tp2, tp3 = current_price - (atr * 0.8), current_price + (atr * 1.2), current_price + (atr * 2.0), current_price + (atr * 3.0)
        elif rec['trade_type'] == 'SHORT':
            sl, tp1, tp2, tp3 = current_price + (atr * 0.8), current_price - (atr * 1.2), current_price - (atr * 2.0), current_price - (atr * 3.0)
        else:
            sl, tp1, tp2, tp3 = current_price - (atr * 0.8), current_price + (atr * 1.2), current_price + (atr * 2.0), current_price + (atr * 3.0)
        
        rr = abs(tp1 - current_price) / abs(current_price - sl) if abs(current_price - sl) > 0 else 0
        
        return {'symbol': symbol, 'price': current_price, 'rsi': rsi, 'ema20': ema20, 'ema50': ema50, 'macd': macd, 'atr': atr, 'interval': interval,
                'kelly': kelly, 'mc': mc, 'vol_msg': vol_msg, 'direction': direction, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'rr': rr,
                'news': news, 'vgs': vgs_data, 'vfm': vfm_data, 'recommendation': rec,
                'order_book': ob, 'ob_analysis': ob_an, 'diff_analysis': diff_an}
    except Exception as e:
        logger.error(f"خطأ في التحليل {symbol}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# 🤖 Qwen AI (التحليل الرئيسي)
# ═══════════════════════════════════════════════════════════════
def ask_qwen(data):
    if not OPENROUTER_API_KEY: 
        return None
    try:
        rec = data.get('recommendation', {})
        math_sigs = rec.get('math_signals', [])
        math_text = " | ".join(math_sigs[:3]) if math_sigs else "لا توجد"
        prompt = f"""أنت محلل كمي (Quant Analyst). رمز: {data['symbol']} ({data.get('interval', '1h')}).
السعر: ${data['price']:,.2f}
الإشارات الرياضية: {math_text}
الإشارة التفاضلية: {data['diff_analysis'].get('final_signal',0):.2f} (ثقة: {data['diff_analysis'].get('confidence',0)*100:.0f}%)
RSI: {data['rsi']:.1f} | Kelly: {data['kelly'].get('kelly_safe',0)*100:.1f}%

أعطِ تحليلاً كمياً مختصراً بالعربية في 3 نقاط فقط (بدون رموز Markdown):
1. القرار بناءً على الرياضيات
2. تفسير فيزيائي لحركة السعر
3. المخاطر
ليس نصيحة مالية. لا تستخدم # أو * أو ** في ردك."""
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/tysear/trading-bot",
            "X-Title": "Quant Trading Bot"
        }
        
        payload = {
            "model": "qwen/qwen-2.5-72b-instruct",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 500
        }
        
        logger.info("🤖 جاري إرسال طلب إلى Qwen AI...")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                res = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=60)
                
                if res.status_code == 200:
                    result = res.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        analysis = result['choices'][0]['message']['content']
                        cleaned = clean_markdown(analysis)
                        logger.info(f"✅ تم استلام تحليل Qwen AI ({len(cleaned)} حرف)")
                        return cleaned
                elif res.status_code == 429:
                    time.sleep(5)
                else:
                    logger.error(f" خطأ HTTP {res.status_code}")
                    
            except requests.exceptions.Timeout:
                time.sleep(3)
            except Exception as e:
                logger.error(f"❌ خطأ: {str(e)[:200]}")
                time.sleep(3)
        
        return None
    except Exception as e:
        logger.error(f"❌ خطأ عام في ask_qwen: {str(e)}")
        return None

# ═══════════════════════════════════════════════════════════════
# 🤖 Gemini AI (المساعد - رأي ثاني)
# ═══════════════════════════════════════════════════════════════
def ask_gemini(data):
    """تحليل Gemini AI كمساعد لـ Qwen"""
    if not GEMINI_API_KEY:
        logger.warning("⚠️ GEMINI_API_KEY غير موجود - تم تعطيل Gemini")
        return None
    try:
        rec = data.get('recommendation', {})
        math_sigs = rec.get('math_signals', [])
        math_text = " | ".join(math_sigs[:3]) if math_sigs else "لا توجد"
        
        prompt = f"""أنت محلل تداول مساعد (Co-Analyst). قدم رأياً ثانياً مستقلاً عن:

الرمز: {data['symbol']} ({data.get('interval', '1h')})
السعر: ${data['price']:,.2f}
RSI: {data['rsi']:.1f}
الإشارات الرياضية: {math_text}
الإشارة التفاضلية: {data['diff_analysis'].get('final_signal',0):.2f}
Kelly: {data['kelly'].get('kelly_safe',0)*100:.1f}%

أعطِ رأيك المختصر بالعربية (بدون Markdown):
- هل تتفق مع التحليل الكمي؟ لماذا؟
- ما الذي قد يفوته التحليل الرئيسي؟
- نصيحة عملية واحدة

لا تستخدم # أو * أو **. ليس نصيحة مالية."""
        
        url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 500
            }
        }
        
        logger.info(" جاري إرسال طلب إلى Gemini AI...")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                res = requests.post(url, json=payload, timeout=30)
                
                if res.status_code == 200:
                    result = res.json()
                    if 'candidates' in result and len(result['candidates']) > 0:
                        analysis = result['candidates'][0]['content']['parts'][0]['text']
                        cleaned = clean_markdown(analysis)
                        logger.info(f"✅ تم استلام تحليل Gemini AI ({len(cleaned)} حرف)")
                        return cleaned
                    else:
                        logger.warning(f"⚠️ الرد لا يحتوي على candidates")
                elif res.status_code == 429:
                    logger.warning(f"⚠️ Rate limit - محاولة {attempt + 1}/{max_retries}")
                    time.sleep(5)
                else:
                    logger.error(f"❌ خطأ HTTP {res.status_code}: {res.text[:200]}")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"️ Timeout - محاولة {attempt + 1}/{max_retries}")
                time.sleep(3)
            except Exception as e:
                logger.error(f" خطأ في المحاولة {attempt + 1}: {str(e)[:200]}")
                time.sleep(3)
        
        logger.error("❌ فشلت جميع محاولات Gemini AI")
        return None
    except Exception as e:
        logger.error(f" خطأ عام في ask_gemini: {str(e)}")
        return None

def send_unified_report(data, qwen_analysis=None, gemini_analysis=None):
    rec = data.get('recommendation', {})
    vgs, vfm = data.get('vgs', {}), data.get('vfm', {})
    ob, ob_an = data.get('order_book'), data.get('ob_analysis')
    da = data.get('diff_analysis', {})
    kelly = data.get('kelly', {})
    mc = data.get('mc', {})
    interval = data.get('interval', '1h').upper()
    math_sigs = rec.get('math_signals', [])
    
    dir_display = 'شراء' if rec.get('trade_type')=='LONG' else 'بيع' if rec.get('trade_type')=='SHORT' else 'انتظار'
    
    part1 = f"""التقرير الكمي ({interval}) لـ {data['symbol']}
{'='*45}
نظام القرار الرياضي البحت

السعر الحالي: ${data['price']:,.2f}
التوصية الكمية: {dir_display}

مستويات التداول:
SL: ${data['sl']:,.2f}
TP1: ${data['tp1']:,.2f}
TP2: ${data['tp2']:,.2f}
TP3: ${data['tp3']:,.2f}
R:R = 1:{data['rr']:.2f}

الإشارات الرياضية المحركة للقرار:
"""
    if math_sigs:
        for sig in math_sigs[:4]:
            part1 += f"- {sig}\n"
    else:
        part1 += "- لا توجد إشارات رياضية قوية حالياً\n"
    
    part1 += f"""
المؤشرات التقليدية (فلاتر سلامة فقط):
- RSI: {data['rsi']:.1f}
- MACD: {data['macd']:.2f}
- ATR: ${data['atr']:,.2f}

Kelly الآمن: {kelly.get('kelly_safe', 0)*100:.1f}%
مونت كارلو: ربح {mc.get('prob_profit',0):.1f}%"""
    send_long_report(part1)
    
    osc = da.get('oscillator', {})
    diff = da.get('diffusion', {})
    forced = da.get('forced', {})
    
    part2 = f"""{'='*45}
المعادلات التفاضلية (80% من القرار):
{'='*45}

1. المذبذب التوافقي:
- الموقع: {osc.get('position','N/A')}
- التخميد: {osc.get('damping',0):.2f}
- التنبؤ: ${osc.get('prediction',0):,.2f}

2. نموذج الانتشار:
- الاتجاه: {'صعودي' if diff.get('signal',0)>0 else 'هبوطي' if diff.get('signal',0)<0 else 'محايد'}
- القوة: {diff.get('strength',0)*100:.1f}%

3. الحركة القسرية:
- تسيطر الأخبار؟: {'نعم' if forced.get('news_dominance',False) else 'لا'}

الإشارة النهائية: {da.get('final_signal',0):.2f} (ثقة: {da.get('confidence',0)*100:.0f}%)"""
    send_long_report(part2)
    
    part3 = f"""VGS (قوة التدرج): {vgs.get('vgs',0):.3f} - {vgs.get('direction','')}
VFM (التدفق): تباعد {vfm.get('divergence',0):.2f} | تدفق {vfm.get('flux',0):.2f}"""
    send_long_report(part3)
    
    if ob and ob_an:
        part4 = f"""{'='*45}
Order Book ({ob_an.get('source','Binance')}):
Bid: ${ob['best_bid']:,.4f} | Ask: ${ob['best_ask']:,.4f}
شراء: {ob_an['buy_pressure']:.1f}% | بيع: {ob_an['sell_pressure']:.1f}%
{ob_an['signal']}"""
        send_long_report(part4)
    
    news_text = "\n\n".join(data.get('news', ['لا توجد أخبار']))
    part5 = f"""الأخبار:
{news_text}"""
    send_long_report(part5)
    
    # ✅ تحليل Qwen AI (الرئيسي)
    if qwen_analysis:
        part6 = f"""{'='*45}
🤖 تحليل Qwen AI (الرئيسي):
{'='*45}
{qwen_analysis}"""
        send_long_report(part6)
    
    # ✅ تحليل Gemini AI (المساعد)
    if gemini_analysis:
        part6b = f"""{'='*45}
🤖 تحليل Gemini AI (المساعد - رأي ثاني):
{'='*45}
{gemini_analysis}"""
        send_long_report(part6b)
    
    part7 = f"""{'='*45}
التوصية النهائية:
{'='*45}

{rec.get('action','انتظار')}

{rec.get('description','')}
الثقة: {rec.get('confidence','غير محدد')}
النقاط: {rec.get('score',0)}

تذكير:
SL: ${data['sl']:,.2f}
TP1: ${data['tp1']:,.2f}

هذا البوت كمي (Quant): 80% من القرار مبني على المعادلات التفاضلية وOrder Book. المؤشرات التقليدية مجرد فلاتر سلامة. ليست نصيحة مالية."""
    send_long_report(part7)

def smart_analysis(symbol, interval="1h"):
    send_message(f"جاري التحليل الكمي لـ {symbol} ({interval.upper()})...")
    data = unified_analysis(symbol, interval)
    if not data:
        send_message(f"فشل تحليل {symbol}.")
        return
    
    qwen_ai = None
    gemini_ai = None
    
    # ✅ استدعاء Qwen AI
    if OPENROUTER_API_KEY:
        send_message("🤖 جاري التحليل بواسطة Qwen AI...")
        qwen_ai = ask_qwen(data)
    
    # ✅ استدعاء Gemini AI (المساعد)
    if GEMINI_API_KEY:
        send_message(" جاري التحليل المساعد بواسطة Gemini AI...")
        gemini_ai = ask_gemini(data)
    
    send_unified_report(data, qwen_ai, gemini_ai)

def test_order_book():
    send_message("جاري اختبار Order Book...")
    for sym in ['BTC-USD', 'ETH-USD', 'XRP-USD']:
        try:
            ob = fetch_order_book(sym)
            if ob:
                ob_an = analyze_order_book(ob)
                est = "تقديري" if ob.get('estimated') else "حقيقي"
                msg = f"{sym} ({est})\n"
                msg += f"   Bid: ${ob['best_bid']:,.2f} | Ask: ${ob['best_ask']:,.2f}\n"
                msg += f"   Spread: {ob['spread_percent']:.3f}%\n"
                msg += f"   {ob_an['signal'] if ob_an else 'N/A'}"
                send_message(msg)
            else: send_message(f"{sym}: فشل")
        except Exception as e: send_message(f"{sym}: {e}")
        time.sleep(1)

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start':
        send_message("""بوت التداول الكمي (Quant Bot)

الفلسفة: المعادلات الرياضية + Order Book = 80% من القرار
مدعوم بـ Qwen AI + Gemini AI (رأيان مستقلان)

الأوامر:
/smart <رمز> [تايم_فريم]
أمثلة:
/smart BTC-USD 1h
/smart XRP-USD 4h
/smart ETH-USD 15m
/test_ob - اختبار Order Book
/help""")
    elif cmd == '/test_ob':
        test_order_book()
    elif cmd.startswith('/smart'):
        parts = command.split(' ')
        if len(parts) < 2:
            send_message("الاستخدام: /smart <رمز> [تايم_فريم]\nمثال: /smart BTC-USD 1h")
            return
        symbol = parts[1].strip().upper().replace(' ', '')
        interval = parts[2].strip().lower() if len(parts) > 2 else "1h"
        valid_intervals = ['1m', '2m', '5m', '15m', '30m', '60m', '1h', '4h', '90m', '1d', '5d', '1wk', '1mo']
        if interval not in valid_intervals:
            send_message("تايم فريم غير مدعوم. استخدم: 15m, 1h, 4h, 1d")
            return
        smart_analysis(symbol, interval)
    elif cmd == '/help':
        send_message("الأوامر:\n/smart <رمز> [15m, 1h, 4h, 1d]\n/test_ob\n/help")

def listen():
    last_id = 0
    logger.info("🤖 البوت يبدأ الاستماع للرسائل الآن...")
    logger.info(f" CHAT_ID المسجل: {CHAT_ID}")
    logger.info(f"🔑 TELEGRAM_TOKEN موجود: {'نعم' if TELEGRAM_TOKEN else 'لا'}")
    logger.info(f"🤖 OPENROUTER_API_KEY (Qwen) موجود: {'نعم' if OPENROUTER_API_KEY else 'لا'}")
    logger.info(f"🤖 GEMINI_API_KEY (Gemini) موجود: {'نعم' if GEMINI_API_KEY else 'لا'}")
    logger.info(f"📰 FINNHUB_API_KEY موجود: {'نعم' if FINNHUB_API_KEY else 'لا'}")
    
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
                        expected_chat_id = str(CHAT_ID).strip()
                        
                        logger.info(f" رسالة وردت من Chat ID: {incoming_chat_id} | النص: {update['message']['text']}")
                        
                        if incoming_chat_id == expected_chat_id:
                            logger.info("✅ تم مطابقة الـ ID بنجاح، جاري تنفيذ الأمر...")
                            handle_command(update['message']['text'])
                        else:
                            logger.warning(f"⚠️ تم تجاهل الرسالة. المتوقع: {expected_chat_id} | الوارد: {incoming_chat_id}")
                            
                    last_id = update['update_id']
            else:
                logger.error(f"❌ خطأ في getUpdates: {resp.status_code} - {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ خطأ في الاستماع: {e}")
        time.sleep(1)

if __name__ == "__main__":
    logger.info(" Starting QUANT Trading Bot - Math-Driven 80% Decision System...")
    logger.info(f"📋 CHAT_ID: {CHAT_ID}")
    logger.info(f"🔑 TELEGRAM_TOKEN موجود: {'نعم' if TELEGRAM_TOKEN else 'لا'}")
    logger.info(f"🤖 OPENROUTER_API_KEY (Qwen) موجود: {'نعم' if OPENROUTER_API_KEY else 'لا'}")
    logger.info(f"🤖 GEMINI_API_KEY (Gemini) موجود: {'نعم' if GEMINI_API_KEY else 'لا'}")
    logger.info(f"📰 FINNHUB_API_KEY موجود: {'نعم' if FINNHUB_API_KEY else 'لا'}")
    
    threading.Thread(target=monitor_urgent_news, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
