import requests
import time
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

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code != 200: logger.error(f"Send failed: {resp.text}")
    except Exception as e: logger.error(f"Send error: {e}")

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
    logger.info("Starting Urgent News Monitor...")
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
                            send_message(f"🚨 <b>تنبيه عاجل:</b> {original}\n🔗 {item.get('url', '')}")
                            save_alerted_news(original)
        except Exception as e: logger.error(f"News monitor error: {e}")

def fetch_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty: return None
        closes, highs, lows, volumes = hist['Close'].tolist(), hist['High'].tolist(), hist['Low'].tolist(), hist['Volume'].tolist()
        cleaned = [{'close': c, 'high': h, 'low': l, 'volume': v} for c, h, l, v in zip(closes, highs, lows, volumes) 
                   if not np.isnan(c) and not np.isnan(h) and not np.isnan(l) and not np.isnan(v) and c > 0]
        if len(cleaned) < 50: return None
        return {'closes': [d['close'] for d in cleaned], 'highs': [d['high'] for d in cleaned], 
                'lows': [d['low'] for d in cleaned], 'volumes': [d['volume'] for d in cleaned]}
    except Exception as e:
        logger.error(f"Fetch error {symbol}: {e}")
        return None

# ══════════════════════════════════════════
# ORDER BOOK & DIFFERENTIAL ANALYSIS (مختصرة للوضوح)
# ══════════════════════════════════════════
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
    if bp > 65: sig, strn = 'ضغط شراء قوي 🟢', 'قوي'
    elif bp > 55: sig, strn = 'ضغط شراء معتدل 🟢', 'متوسط'
    elif sp > 65: sig, strn = 'ضغط بيع قوي 🔴', 'قوي'
    elif sp > 55: sig, strn = 'ضغط بيع معتدل 🔴', 'متوسط'
    else: sig, strn = 'توازن ⚪', 'ضعيف'
    spr_st = 'ضيق ✅' if ob['spread_percent'] < 0.1 else 'واسع ⚠️' if ob['spread_percent'] > 0.5 else 'عادي'
    return {'signal': sig, 'strength': strn, 'buy_pressure': bp, 'sell_pressure': sp, 'spread_status': spr_st, 'source': ob.get('source'), 'estimated': ob.get('estimated')}

def calc_damped_oscillator(closes, period=20):
    try:
        prices = np.array(closes[-period:], dtype=float)
        fv = np.mean(prices)
        y = prices - fv
        yp, ypp = np.gradient(y), np.gradient(np.gradient(y))
        omega_sq = max(0.01, abs(-ypp[-1] / y[-1])) if abs(y[-1]) > 1e-10 else 0.01
        gamma = abs(-ypp[-1] - omega_sq * y[-1]) / (2 * abs(yp[-1])) if abs(yp[-1]) > 1e-10 else 0.3
        damp = gamma / (2 * np.sqrt(omega_sq))
        phase = np.arctan2(yp[-1], y[-1])
        pos = 'قاع 📈' if np.pi/2 < phase < 3*np.pi/2 else 'قمة 📉'
        return {'regime': 'تذبذب نشط 🌀' if damp < 1 else 'تخميد عالي 🛑', 'position': pos, 'damping': damp, 'prediction': fv + y[-1] + yp[-1] + 0.5*ypp[-1]}
    except: return {'regime': 'N/A', 'position': 'N/A', 'damping': 0, 'prediction': closes[-1]}

def calc_diffusion_model(closes, volumes, period=20):
    try:
        prices, vols = np.array(closes[-period:], dtype=float), np.array(volumes[-period:], dtype=float)
        fd, sd = np.gradient(prices), np.gradient(np.gradient(prices))
        volat = np.std(fd) + 1e-10
        D = volat * np.log10(np.mean(vols) + 1e-10)
        diff_val = D * sd[-1]
        return {'signal': 1 if diff_val > 0 else -1, 'strength': min(1.0, abs(diff_val) / (volat * 10)), 'curvature': sd[-1], 'D': D}
    except: return {'signal': 0, 'strength': 0, 'curvature': 0, 'D': 0}

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

# ══════════════════════════════════════════
# المؤشرات الأساسية
# ══════════════════════════════════════════
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
        if len(closes) < 10: return {'win_rate': 0.5, 'kelly_safe': 0.10}
        wins = sum(1 for i in range(-min(50, len(closes)-5), -5) if closes[i+5] > closes[i])
        total = min(50, len(closes) - 5)
        win_rate = wins / total if total > 0 else 0.5
        edge = (win_rate * 2.0) - (1 - win_rate)
        kelly_full = edge / 2.0 if 2.0 > 0 else 0
        # ✅ إصلاح صارم: الحد الأقصى المطلق 25% (0.25)، والحد الأدنى 5%
        safe_kelly = max(0.05, min(kelly_full * 0.5, 0.25))
        return {'win_rate': win_rate, 'kelly_safe': safe_kelly}
    except: return {'win_rate': 0.5, 'kelly_safe': 0.10}

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
        
        strength = 'قوي جداً 🔥' if vgs > 0.15 else 'قوي 💪' if vgs > 0.08 else 'متوسط ⚪' if vgs > 0.03 else 'ضعيف ⚪'
        direction = 'صعودي 🟢' if ds > 0.02 else 'هبوطي 🔴' if ds < -0.02 else 'محايد ⚪'
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
        
        if div > 0.05 and flux > 0: sig, strn = 'تدفق دخول قوي 🟢', 'قوي' if div > 0.1 else 'متوسط'
        elif div < -0.05 and flux < 0: sig, strn = 'تدفق خروج قوي 🔴', 'قوي' if div < -0.1 else 'متوسط'
        elif curl > 0.3: sig, strn = 'دوامات وتقلبات ⚠️', 'عالي التقلب'
        else: sig, strn = 'محايد ⚪', 'ضعيف'
        return {'divergence': float(div), 'curl': float(curl), 'flux': float(flux), 'signal': sig, 'strength': strn}
    except: return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'خطأ', 'strength': 'غير محدد'}

# ══════════════════════════════════════════
# ✅ نظام التوصية المطور (مع كشف التناقض وحماية RSI)
# ══════════════════════════════════════════
def get_action_recommendation(data):
    try:
        vgs, vfm = data.get('vgs', {}), data.get('vfm', {})
        rsi = data.get('rsi', 50)
        kelly = data.get('kelly', {}).get('kelly_safe', 0.1)
        mc_prob = data.get('mc', {}).get('prob_profit', 50)
        direction = data.get('direction', 'انتظار')
        buy_signals = data.get('buy_signals', 0)
        ob_an = data.get('ob_analysis')
        diff_sig = data.get('diff_signal', 0)
        
        score = 0
        if direction == 'شراء': score += 5
        elif direction == 'بيع': score -= 5
        
        if vgs.get('direction') == 'صعودي 🟢' and vgs.get('vgs', 0) > 0.08: score += 2
        elif vgs.get('direction') == 'صعودي 🟢': score += 1
        elif vgs.get('direction') == 'هبوطي 🔴' and vgs.get('vgs', 0) > 0.08: score -= 2
        elif vgs.get('direction') == 'هبوطي 🔴': score -= 1
        
        if vfm.get('divergence', 0) > 0.05 and vfm.get('flux', 0) > 0: score += 1
        elif vfm.get('divergence', 0) < -0.05 and vfm.get('flux', 0) < 0: score -= 1
        
        if ob_an:
            if 'شراء قوي' in ob_an.get('signal', ''): score += 1
            elif 'بيع قوي' in ob_an.get('signal', ''): score -= 1
            
        if rsi < 30: score += 1
        elif rsi < 40: score += 0.5
        elif rsi > 70: score -= 1
        elif rsi > 60: score -= 0.5
        
        if mc_prob > 65: score += 1
        elif mc_prob < 35: score -= 1
        
        if buy_signals >= 6: score += 1
        elif buy_signals <= 2: score -= 1

        # ✅ 1. نظام كشف التناقض (Contradiction System)
        contradiction_warning = ""
        if diff_sig < -0.2 and score >= 3:
            score -= 3
            contradiction_warning = "⚠️ تناقض خطير: المؤشرات الأساسية إيجابية لكن التحليل التفاضلي هبوطي بقوة!"
        elif diff_sig > 0.2 and score <= -3:
            score += 3
            contradiction_warning = "⚠️ تناقض خطير: المؤشرات الأساسية سلبية لكن التحليل التفاضلي صعودي بقوة!"

        # ✅ 2. حماية RSI المتطرف (Extreme RSI Override)
        rsi_warning = ""
        if rsi < 25 and score < 0: # كان سيتجه للشورت
            score = 0 # إلغاء التوصية
            rsi_warning = "🚫 تم إلغاء توصية الشورت: RSI < 25 (تشبع بيعي شديد، ارتداد وشيك محتمل!)"
        elif rsi > 75 and score > 0: # كان سيتجه للونغ
            score = 0 # إلغاء التوصية
            rsi_warning = "🚫 تم إلغاء توصية اللونغ: RSI > 75 (تشبع شرائي شديد، انعكاس هبوطي محتمل!)"

        # تحديد التوصية النهائية
        if score >= 6: action, emoji, conf = 'افتح شراء (Long) قوي 🟢', '🟢', 'عالية جداً'
        elif score >= 3: action, emoji, conf = 'افتح شراء (Long) 🟢', '🟢', 'عالية'
        elif score >= 1: action, emoji, conf = 'دخول حذر (Long) 🟡', '🟡', 'متوسطة'
        elif score >= -1: action, emoji, conf = 'انتظار ⚪', '⚪', 'منخفضة'
        elif score >= -3: action, emoji, conf = 'دخول حذر (Short) 🟠', '🟠', 'متوسطة'
        elif score >= -6: action, emoji, conf = 'افتح بيع (Short) 🟠', '🟠', 'عالية'
        else: action, emoji, conf = 'افتح بيع (Short) قوي 🔴', '🔴', 'عالية جداً'

        desc = f"نقاط التقييم: {score}. "
        if contradiction_warning: desc += contradiction_warning + " "
        if rsi_warning: desc += rsi_warning
        
        return {'action': action, 'emoji': emoji, 'trade_type': 'LONG' if score > 1 else ('SHORT' if score < -1 else 'WAIT'), 
                'description': desc, 'confidence': conf, 'score': score}
    except Exception as e:
        logger.error(f"Recommendation error: {e}")
        return {'action': 'انتظار ⚪', 'emoji': '⚪', 'trade_type': 'WAIT', 'description': 'تعذر حساب التوصية', 'confidence': 'غير محدد', 'score': 0}

def unified_analysis(symbol):
    try:
        data = fetch_data(symbol)
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
        
        vol_msg = "مرتفع 📈" if len(volumes)>=20 and np.mean(volumes[-5:]) > np.mean(volumes[-20:])*1.2 else "منخفض 📉" if len(volumes)>=20 and np.mean(volumes[-5:]) < np.mean(volumes[-20:])*0.8 else "محايد ⚪"
        
        buy_signals = sum([current_price > ema20, current_price > ema50, macd > 0, rsi < 75, mc['prob_profit'] > 55, vgs_data['direction']=='صعودي 🟢' and vgs_data['vgs']>0.05, vfm_data['divergence']>0.05 and vfm_data['flux']>0])
        direction = 'شراء' if buy_signals >= 4 else ('بيع' if buy_signals <= 2 else 'انتظار')
        
        rec = get_action_recommendation({'vgs': vgs_data, 'vfm': vfm_data, 'rsi': rsi, 'kelly': kelly, 'mc': mc, 
                                         'direction': direction, 'buy_signals': buy_signals, 'ob_analysis': ob_an, 'diff_signal': diff_an['final_signal']})
        
        if direction == 'شراء': sl, tp1, tp2, tp3 = current_price - atr, current_price + atr*1.5, current_price + atr*2.5, current_price + atr*4.0
        elif direction == 'بيع': sl, tp1, tp2, tp3 = current_price + atr, current_price - atr*1.5, current_price - atr*2.5, current_price - atr*4.0
        else:
            if vgs_data['direction'] == 'هبوطي 🔴': sl, tp1, tp2, tp3 = current_price + atr, current_price - atr*1.5, current_price - atr*2.5, current_price - atr*4.0
            else: sl, tp1, tp2, tp3 = current_price - atr, current_price + atr*1.5, current_price + atr*2.5, current_price + atr*4.0
            
        rr = abs(tp1 - current_price) / abs(current_price - sl) if abs(current_price - sl) > 0 else 0
        
        return {'symbol': symbol, 'price': current_price, 'rsi': rsi, 'ema20': ema20, 'ema50': ema50, 'macd': macd, 'atr': atr,
                'kelly': kelly, 'mc': mc, 'vol_msg': vol_msg, 'direction': direction, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'rr': rr,
                'news': news, 'buy_signals': buy_signals, 'vgs': vgs_data, 'vfm': vfm_data, 'recommendation': rec,
                'order_book': ob, 'ob_analysis': ob_an, 'diff_analysis': diff_an}
    except Exception as e:
        logger.error(f"Analysis error {symbol}: {e}")
        return None

def ask_qwen(data):
    if not OPENROUTER_API_KEY: return None
    try:
        ob_an = data.get('ob_analysis', {})
        da = data.get('diff_analysis', {})
        prompt = f"""أنت محلل مالي خبير. رمز: {data['symbol']}, السعر: ${data['price']:,.2f}, RSI: {data['rsi']:.1f}, 
VGS: {data['vgs'].get('vgs',0):.3f} ({data['vgs'].get('direction','')}), VFM: {data['vfm'].get('signal','')}, 
Kelly الآمن: {data['kelly'].get('kelly_safe',0)*100:.1f}%, احتمال الربح: {data['mc'].get('prob_profit',0):.1f}%, 
الإشارة التفاضلية: {da.get('final_signal',0):.2f} (ثقة: {da.get('confidence',0)*100:.0f}%).
أعطِ: 1. القرار 2. التفسير المختصر 3. المخاطر 4. نصيحة عملية. ⚠️ ليس نصيحة مالية."""
        res = requests.post(OPENROUTER_API_URL, json={"model": "qwen/qwen-2.5-72b-instruct", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}, 
                            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, timeout=45)
        if res.status_code == 200:
            result = res.json()
            if 'choices' in result and len(result['choices']) > 0: return result['choices'][0]['message']['content']
        return None
    except: return None

def send_unified_report(data, ai_analysis=None):
    rec = data.get('recommendation', {})
    vgs, vfm = data.get('vgs', {}), data.get('vfm', {})
    ob, ob_an = data.get('order_book'), data.get('ob_analysis')
    da = data.get('diff_analysis', {})
    kelly = data.get('kelly', {})
    
    part1 = f"""🎯 <b>التقرير الشامل الموحد لـ {data['symbol']}</b>
{'='*45}
💰 <b>السعر:</b> ${data['price']:,.2f} | <b>الاتجاه:</b> {'شراء 🟢' if data['direction']=='شراء' else 'بيع 🔴' if data['direction']=='بيع' else 'انتظار ⚪'}
📈 RSI: {data['rsi']:.1f} | MACD: {data['macd']:.2f} | ATR: ${data['atr']:,.2f}
💰 <b>Kelly الآمن:</b> {kelly.get('kelly_safe', 0)*100:.1f}% (حد أقصى 25%)
🎲 <b>مونت كارلو:</b> ربح {data['mc'].get('prob_profit',0):.1f}% | خسارة 10%+ {data['mc'].get('prob_loss_10',0):.1f}%"""
    send_long_report(part1)
    
    part2 = f"""🎯 <b>قوة الاتجاه (VGS):</b> ‖∇f‖ = {vgs.get('vgs',0):.3f} ({vgs.get('strength','')}) - {vgs.get('direction','')}
🌊 <b>التدفق (VFM):</b> تباعد: {vfm.get('divergence',0):.3f} | تدفق: {vfm.get('flux',0):.2f} - {vfm.get('signal','')}"""
    send_long_report(part2)
    
    osc, diff, forced = da.get('oscillator', {}), da.get('diffusion', {}), da.get('forced', {})
    part3 = f"""🔬 <b>التحليل التفاضلي المتقدم:</b>
🌀 المذبذب: {osc.get('position','N/A')} | تخميد: {osc.get('damping',0):.2f}
🌊 الانتشار: {'صعودي 🟢' if diff.get('signal',0)>0 else 'هبوطي 🔴'} (قوة: {diff.get('strength',0)*100:.1f}%)
⚡ الحركة القسرية: الأخبار تسيطر؟ {'نعم ⚠️' if forced.get('news_dominance',False) else 'لا ✅'}
🎯 الإشارة النهائية: {da.get('final_signal',0):.2f} (ثقة: {da.get('confidence',0)*100:.0f}%)"""
    send_long_report(part3)
    
    if ob and ob_an:
        part4 = f"""📊 <b>Order Book ({ob_an.get('source','Binance')}):</b>
💰 Bid: ${ob['best_bid']:,.4f} | Ask: ${ob['best_ask']:,.4f} | Spread: {ob['spread_percent']:.3f}%
⚖️ ضغط الشراء: {ob_an['buy_pressure']:.1f}% | ضغط البيع: {ob_an['sell_pressure']:.1f}%
🎯 الإشارة: {ob_an['signal']}"""
        send_long_report(part4)
        
    if ai_analysis:
        send_long_report(f"🤖 <b>تحليل Qwen AI:</b>\n{ai_analysis}")
        
    part5 = f"""{'='*45}
💰 <b>مستويات التداول:</b>
• SL: ${data['sl']:,.2f} | TP1: ${data['tp1']:,.2f} | TP2: ${data['tp2']:,.2f} | TP3: ${data['tp3']:,.2f}
• المخاطرة/العائد: 1:{data['rr']:.2f}

🎯 <b>التوصية النهائية:</b>
{rec.get('emoji','⚪')} <b>{rec.get('action','انتظار')}</b>
📋 {rec.get('description','')}
📊 الثقة: {rec.get('confidence','غير محدد')}

💡 <i>هذه التوصية مبنية على دمج جميع المؤشرات مع أنظمة الحماية من التناقض والتشبع. ليست نصيحة مالية.</i>"""
    send_long_report(part5)

def smart_analysis(symbol):
    send_message(f"🔍 جاري التحليل الشامل لـ {symbol}...")
    data = unified_analysis(symbol)
    if not data:
        send_message(f"❌ فشل تحليل {symbol}.")
        return
    if OPENROUTER_API_KEY:
        send_message("🤖 جاري تحليل Qwen AI...")
        ai = ask_qwen(data)
    else: ai = None
    send_unified_report(data, ai)

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start': send_message("🤖 <b>بوت التداول الذكي الآمن</b>\n/smart <رمز> - تحليل شامل\n/test_ob - اختبار Order Book\n/help - المساعدة")
    elif cmd == '/test_ob':
        send_message("🔍 جاري اختبار Order Book...")
        for sym in ['BTC-USD', 'ETH-USD']:
            ob = fetch_order_book(sym)
            if ob: send_message(f"✅ {sym}: Bid ${ob['best_bid']:,.2f} | Ask ${ob['best_ask']:,.2f} | ضغط شراء {ob['buy_pressure']:.1f}%")
            else: send_message(f"❌ {sym}: فشل")
    elif cmd.startswith('/smart'):
        parts = command.split(' ', 1)
        if len(parts) < 2: return send_message("الاستخدام: /smart <رمز>\nمثال: /smart BTC-USD")
        smart_analysis(parts[1].strip().upper().replace(' ', ''))
    elif cmd == '/help': send_message("<b>الأوامر:</b>\n/smart <رمز>\n/test_ob\n/help")

def listen():
    last_id = 0
    while True:
        try:
            resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset": last_id + 1, "timeout": 10}, timeout=15)
            if resp.status_code == 200 and resp.json().get('ok'):
                for update in resp.json()['result']:
                    if 'message' in update and 'text' in update['message'] and str(update['message']['chat']['id']) == CHAT_ID:
                        handle_command(update['message']['text'])
                    last_id = update['update_id']
        except Exception as e: logger.error(f"Listen error: {e}")
        time.sleep(1)

if __name__ == "__main__":
    logger.info("Starting Secure Unified Trading Bot with Contradiction Detection & RSI Protection...")
    threading.Thread(target=monitor_urgent_news, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
