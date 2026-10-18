
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

def load_alerted_news():
    try:
        if os.path.exists(ALERTED_NEWS_FILE):
            with open(ALERTED_NEWS_FILE, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
    except:
        pass
    return set()

def save_alerted_news(headline):
    try:
        with open(ALERTED_NEWS_FILE, 'a', encoding='utf-8') as f:
            f.write(headline + '\n')
    except:
        pass

def monitor_urgent_news():
    logger.info("Starting Urgent News Monitor...")
    while True:
        try:
            time.sleep(1800)
            if not FINNHUB_API_KEY:
                continue
            url = f"{FINNHUB_API_URL}/news?category=general&token={FINNHUB_API_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                news_list = resp.json()[:10]
                alerted = load_alerted_news()
                for item in news_list:
                    headline = item.get('headline', '').lower()
                    source = item.get('source', '')
                    original_headline = item.get('headline', '')
                    url_news = item.get('url', '')
                    if any(keyword in headline for keyword in URGENT_KEYWORDS):
                        if original_headline not in alerted:
                            alert_msg = (
                                f"🚨 <b>تنبيه عاجل: خبر اقتصادي مؤثر!</b>\n\n"
                                f"📰 <b>{original_headline}</b>\n"
                                f"📡 المصدر: {source}\n"
                                f"🔗 <a href='{url_news}'>رابط الخبر</a>\n\n"
                                f"💡 <i>يُنصح بمراجعة السوق فوراً. يمكنك استخدام /smart لفحص الأصول.</i>"
                            )
                            send_message(alert_msg)
                            save_alerted_news(original_headline)
                            logger.info(f"Urgent news alerted: {original_headline}")
        except Exception as e:
            logger.error(f"News monitor error: {e}")

def fetch_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty:
            return None
        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        volumes = hist['Volume'].tolist()
        cleaned_data = []
        for i in range(len(closes)):
            if (not np.isnan(closes[i]) and not np.isnan(highs[i]) and 
                not np.isnan(lows[i]) and not np.isnan(volumes[i]) and
                closes[i] > 0 and highs[i] > 0 and lows[i] > 0):
                cleaned_data.append({
                    'close': closes[i], 'high': highs[i],
                    'low': lows[i], 'volume': volumes[i]
                })
        if len(cleaned_data) < 50:
            return None
        return {
            'closes': [d['close'] for d in cleaned_data],
            'highs': [d['high'] for d in cleaned_data],
            'lows': [d['low'] for d in cleaned_data],
            'volumes': [d['volume'] for d in cleaned_data]
        }
    except Exception as e:
        logger.error(f"Fetch error {symbol}: {e}")
        return None

# ══════════════════════════════════════════
# ORDER BOOK - مع Fallbacks متعددة
# ══════════════════════════════════════════
def fetch_order_book(symbol):
    """جلب Order Book مع 4 مصادر بديلة"""
    try:
        if symbol not in CRYPTO_SYMBOLS:
            logger.info(f"⏭️ Order Book غير متاح لـ {symbol} (كريبتو فقط)")
            return None
        
        binance_symbol = symbol.replace('-USD', 'USDT')
        coingecko_id = symbol.replace('-USD', '').lower()
        
        # المحاولة 1: Binance Global
        try:
            logger.info(f"🔵 Trying Binance Global for {binance_symbol}...")
            url = f"https://api.binance.com/api/v3/depth?symbol={binance_symbol}&limit=20"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('bids') and data.get('asks'):
                    logger.info(f"✅ Binance Global succeeded for {symbol}")
                    return parse_order_book_data(data, 'Binance Global')
        except Exception as e:
            logger.warning(f"️ Binance Global failed: {e}")
        
        # المحاولة 2: Binance US
        try:
            logger.info(f"🟢 Trying Binance US for {binance_symbol}...")
            url = f"https://api.binance.us/api/v3/depth?symbol={binance_symbol}&limit=20"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('bids') and data.get('asks'):
                    logger.info(f"✅ Binance US succeeded for {symbol}")
                    return parse_order_book_data(data, 'Binance US')
        except Exception as e:
            logger.warning(f"⚠️ Binance US failed: {e}")
        
        # المحاولة 3: CoinGecko
        try:
            logger.info(f"🟡 Trying CoinGecko for {coingecko_id}...")
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_id}&vs_currencies=usd&include_24hr_vol=true"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if coingecko_id in data:
                    coin_data = data[coingecko_id]
                    price = float(coin_data.get('usd', 0))
                    volume = float(coin_data.get('usd_24h_vol', 0))
                    if price > 0:
                        logger.info(f"✅ CoinGecko succeeded for {symbol}")
                        return create_estimated_order_book(price, price * 1.0001, volume, 'CoinGecko')
        except Exception as e:
            logger.warning(f"⚠️ CoinGecko failed: {e}")
        
        # المحاولة 4: Yahoo Finance
        try:
            logger.info(f"🔴 Trying Yahoo Finance fallback for {symbol}...")
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if not hist.empty:
                current_price = float(hist['Close'].iloc[-1])
                volume = float(hist['Volume'].iloc[-1])
                atr = float((hist['High'] - hist['Low']).mean())
                logger.info(f"✅ Yahoo Finance fallback for {symbol}")
                return create_estimated_order_book(current_price, current_price * 1.0001, volume, 'Yahoo Finance (estimated)', atr)
        except Exception as e:
            logger.warning(f"⚠️ Yahoo Finance fallback failed: {e}")
        
        logger.error(f"❌ All Order Book sources failed for {symbol}")
        return None
    except Exception as e:
        logger.error(f"❌ Order Book error for {symbol}: {e}")
        return None

def parse_order_book_data(data, source):
    """تحليل بيانات Order Book من Binance"""
    try:
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        if not bids or not asks:
            return None
        
        total_bid_volume = sum(float(bid[1]) for bid in bids[:10])
        total_ask_volume = sum(float(ask[1]) for ask in asks[:10])
        total_volume = total_bid_volume + total_ask_volume
        
        if total_volume == 0:
            return None
        
        buy_pressure = (total_bid_volume / total_volume) * 100
        sell_pressure = (total_ask_volume / total_volume) * 100
        
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        spread = best_ask - best_bid
        spread_percent = (spread / best_ask * 100) if best_ask > 0 else 0
        
        support_levels = []
        resistance_levels = []
        for bid in bids[:10]:
            price = float(bid[0])
            volume = float(bid[1])
            if volume > total_bid_volume * 0.15:
                support_levels.append({'price': price, 'volume': volume})
        for ask in asks[:10]:
            price = float(ask[0])
            volume = float(ask[1])
            if volume > total_ask_volume * 0.15:
                resistance_levels.append({'price': price, 'volume': volume})
        
        return {
            'best_bid': best_bid, 'best_ask': best_ask,
            'spread': spread, 'spread_percent': spread_percent,
            'buy_pressure': buy_pressure, 'sell_pressure': sell_pressure,
            'total_bid_volume': total_bid_volume, 'total_ask_volume': total_ask_volume,
            'support_levels': support_levels[:3], 'resistance_levels': resistance_levels[:3],
            'source': source, 'estimated': False
        }
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None

def create_estimated_order_book(bid, ask, volume, source, atr=None):
    """إنشاء Order Book تقديري"""
    try:
        if atr is None:
            atr = bid * 0.02
        spread = ask - bid
        spread_percent = (spread / ask * 100) if ask > 0 else 0
        buy_pressure = 50 + (volume / 1000000000 * 5)
        buy_pressure = max(40, min(60, buy_pressure))
        sell_pressure = 100 - buy_pressure
        
        support_levels = [
            {'price': round(bid - atr, 4), 'volume': volume * 0.3},
            {'price': round(bid - atr * 2, 4), 'volume': volume * 0.5},
        ]
        resistance_levels = [
            {'price': round(ask + atr, 4), 'volume': volume * 0.3},
            {'price': round(ask + atr * 2, 4), 'volume': volume * 0.5},
        ]
        
        return {
            'best_bid': bid, 'best_ask': ask,
            'spread': spread, 'spread_percent': spread_percent,
            'buy_pressure': buy_pressure, 'sell_pressure': sell_pressure,
            'total_bid_volume': volume * 0.5, 'total_ask_volume': volume * 0.5,
            'support_levels': support_levels, 'resistance_levels': resistance_levels,
            'source': source, 'estimated': True
        }
    except Exception as e:
        logger.error(f"Estimated OB error: {e}")
        return None

def analyze_order_book(ob_data):
    """تحليل Order Book"""
    if not ob_data:
        return None
    try:
        buy_pressure = ob_data['buy_pressure']
        sell_pressure = ob_data['sell_pressure']
        if buy_pressure > 65:
            signal = 'ضغط شراء قوي '
            strength = 'قوي'
        elif buy_pressure > 55:
            signal = 'ضغط شراء معتدل 🟢'
            strength = 'متوسط'
        elif sell_pressure > 65:
            signal = 'ضغط بيع قوي 🔴'
            strength = 'قوي'
        elif sell_pressure > 55:
            signal = 'ضغط بيع معتدل 🔴'
            strength = 'متوسط'
        else:
            signal = 'توازن ⚪'
            strength = 'ضعيف'
        spread_status = 'ضيق ✅' if ob_data['spread_percent'] < 0.1 else 'عادي' if ob_data['spread_percent'] < 0.5 else 'واسع ⚠️'
        return {
            'signal': signal, 'strength': strength,
            'buy_pressure': buy_pressure, 'sell_pressure': sell_pressure,
            'spread_status': spread_status,
            'support_count': len(ob_data['support_levels']),
            'resistance_count': len(ob_data['resistance_levels']),
            'source': ob_data.get('source', 'غير معروف'),
            'estimated': ob_data.get('estimated', False)
        }
    except Exception as e:
        logger.error(f"Order Book analysis error: {e}")
        return None

def fetch_finnhub_news(symbol):
    if not FINNHUB_API_KEY:
        return None
    try:
        general_news_url = f"{FINNHUB_API_URL}/news?category=general&token={FINNHUB_API_KEY}"
        general_resp = requests.get(general_news_url, timeout=10)
        finnhub_symbol = symbol
        if symbol.endswith('-USD'):
            finnhub_symbol = 'BINANCE:' + symbol.replace('-USD', '')
        company_news_url = f"{FINNHUB_API_URL}/company-news?symbol={finnhub_symbol}&from={int(time.time())-86400}&to={int(time.time())}&token={FINNHUB_API_KEY}"
        company_resp = requests.get(company_news_url, timeout=10)
        news_list = []
        if general_resp.status_code == 200:
            general_news = general_resp.json()[:5]
            for item in general_news:
                headline = item.get('headline', '')
                source = item.get('source', '')
                if headline and any(keyword in headline.lower() for keyword in 
                    ['fed', 'federal', 'trump', 'biden', 'treasury', 'bank', 'inflation', 'interest rate', 'crypto', 'bitcoin', 'sec', 'gold']):
                    news_list.append(f" <b>{headline}</b>\n   <i>المصدر: {source}</i>")
        if company_resp.status_code == 200:
            company_news = company_resp.json()[:3]
            for item in company_news:
                headline = item.get('headline', '')
                source = item.get('source', '')
                if headline:
                    news_list.append(f"📊 <b>{headline}</b>\n   <i>المصدر: {source}</i>")
        if len(news_list) == 0:
            return ["لا توجد أخبار مهمة حالياً."]
        return news_list[:8]
    except Exception as e:
        logger.error(f"Finnhub news error: {e}")
        return None

def fetch_news(symbol):
    finnhub_news = fetch_finnhub_news(symbol)
    if finnhub_news:
        return finnhub_news
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news or len(news) == 0:
            return ["لا توجد أخبار حديثة."]
        return [f"• {item.get('title', 'بدون عنوان')} ({item.get('publisher', 'مجهول')})" for item in news[:3]]
    except:
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
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
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
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            true_ranges.append(tr)
        return sum(true_ranges) / period
    except:
        return closes[-1] * 0.02

def calc_ou_model(prices):
    try:
        closes = np.array(prices, dtype=float)
        closes = closes[~np.isnan(closes)]
        if len(closes) < 10:
            return {'mu': 0, 'sigma': 0, 'z_score': 0}
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
        closes = closes[~np.isnan(closes)]
        if len(closes) < 10:
            return {'win_rate': 0.5, 'kelly_half': 0.1}
        wins = 0
        total = min(50, len(closes) - 5)
        for i in range(-total, -5):
            if closes[i+5] > closes[i]:
                wins += 1
        win_rate = wins / total if total > 0 else 0.5
        win_loss_ratio = 2.0
        edge = (win_rate * win_loss_ratio) - (1 - win_rate)
        kelly_full = edge / win_loss_ratio if win_loss_ratio > 0 else 0
        return {'win_rate': win_rate, 'kelly_half': max(min(kelly_full / 2, 0.25), 0)}
    except:
        return {'win_rate': 0.5, 'kelly_half': 0.1}

def monte_carlo(prices, days=30, simulations=500):
    try:
        closes = np.array(prices, dtype=float)
        closes = closes[~np.isnan(closes)]
        if len(closes) < 10:
            return {'prob_profit': 50, 'prob_loss_10': 20, 'p5': 0, 'p95': 0}
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
        return {
            'prob_profit': float(np.sum(final_prices > current) / simulations * 100),
            'prob_loss_10': float(np.sum(final_prices < current * 0.9) / simulations * 100),
            'p5': float(np.percentile(final_prices, 5)),
            'p95': float(np.percentile(final_prices, 95))
        }
    except:
        return {'prob_profit': 50, 'prob_loss_10': 20, 'p5': 0, 'p95': 0}

def calc_vector_gradient_strength(closes, volumes):
    try:
        if len(closes) < 20:
            return {'vgs': 0, 'direction': 'محايد', 'strength': 'ضعيف',
                    'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0, 'direction_score': 0}
        prices = np.array(closes, dtype=float)
        vols = np.array(volumes, dtype=float) if len(volumes) == len(closes) else np.ones(len(closes))
        valid_mask = ~(np.isnan(prices) | np.isnan(vols))
        prices = prices[valid_mask]
        vols = vols[valid_mask]
        if len(prices) < 20:
            return {'vgs': 0, 'direction': 'محايد', 'strength': 'ضعيف',
                    'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0, 'direction_score': 0}
        recent_prices = prices[-10:]
        grad_P = (recent_prices[-1] - recent_prices[0]) / (recent_prices[0] + 1e-10)
        recent_vols = vols[-10:]
        avg_vol = np.mean(vols[-20:]) + 1e-10
        grad_V = (np.mean(recent_vols[-5:]) - np.mean(recent_vols[:5])) / avg_vol
        rsi_values = [calc_rsi(prices[:i].tolist()) for i in range(14, len(prices) + 1)]
        recent_rsi = rsi_values[-10:]
        grad_R = (recent_rsi[-1] - recent_rsi[0]) / 100.0
        macd_values = [calc_ema(prices[:i].tolist(), 12) - calc_ema(prices[:i].tolist(), 26) for i in range(26, len(prices) + 1)]
        if len(macd_values) >= 10:
            recent_macd = macd_values[-10:]
            avg_macd = np.mean(np.abs(macd_values[-20:])) + 1e-10
            grad_M = (np.mean(recent_macd[-5:]) - np.mean(recent_macd[:5])) / avg_macd
        else:
            grad_M = 0.0
        w1, w2, w3, w4 = 0.4, 0.2, 0.2, 0.2
        grad_P_w, grad_V_w, grad_R_w, grad_M_w = w1*grad_P, w2*grad_V, w3*grad_R, w4*grad_M
        vgs = np.sqrt(grad_P_w**2 + grad_V_w**2 + grad_R_w**2 + grad_M_w**2)
        direction_score = grad_P_w + grad_V_w + grad_R_w + grad_M_w
        if vgs > 0.15: strength = 'قوي جداً 🔥'
        elif vgs > 0.08: strength = 'قوي 💪'
        elif vgs > 0.03: strength = 'متوسط ⚪'
        else: strength = 'ضعيف ⚪'
        if direction_score > 0.02: direction = 'صعودي 🟢'
        elif direction_score < -0.02: direction = 'هبوطي 🔴'
        else: direction = 'محايد ⚪'
        return {'vgs': float(vgs), 'direction': direction, 'strength': strength,
                'grad_P': float(grad_P), 'grad_V': float(grad_V),
                'grad_R': float(grad_R), 'grad_M': float(grad_M), 'direction_score': float(direction_score)}
    except Exception as e:
        logger.error(f"VGS error: {e}")
        return {'vgs': 0, 'direction': 'محايد', 'strength': 'خطأ',
                'grad_P': 0, 'grad_V': 0, 'grad_R': 0, 'grad_M': 0, 'direction_score': 0}

def calc_vector_flow_model(closes, volumes):
    try:
        if len(closes) < 20:
            return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'محايد', 'strength': 'ضعيف'}
        prices = np.array(closes, dtype=float)
        vols = np.array(volumes, dtype=float) if len(volumes) == len(closes) else np.ones(len(closes))
        valid_mask = ~(np.isnan(prices) | np.isnan(vols))
        prices = prices[valid_mask]
        vols = vols[valid_mask]
        if len(prices) < 20:
            return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'محايد', 'strength': 'ضعيف'}
        price_changes = np.diff(prices) / (prices[:-1] + 1e-10)
        vol_changes = np.diff(vols) / (vols[:-1] + 1e-10)
        momentum = np.gradient(prices)
        divergence_values = []
        for i in range(-10, -1):
            if i-1 >= -len(price_changes):
                div_p = (price_changes[i] - price_changes[i-1]) / (abs(price_changes[i-1]) + 1e-10)
                div_v = (vol_changes[i] - vol_changes[i-1]) / (abs(vol_changes[i-1]) + 1e-10)
                div_m = (momentum[i] - momentum[i-1]) / (abs(momentum[i-1]) + 1e-10)
                divergence_values.append((div_p + div_v + div_m) / 3)
        divergence = np.mean(divergence_values) if divergence_values else 0.0
        curl_values = []
        for i in range(-10, -1):
            if i-1 >= -len(price_changes):
                div_p = (price_changes[i] - price_changes[i-1]) / (abs(price_changes[i-1]) + 1e-10)
                div_v = (vol_changes[i] - vol_changes[i-1]) / (abs(vol_changes[i-1]) + 1e-10)
                div_m = (momentum[i] - momentum[i-1]) / (abs(momentum[i-1]) + 1e-10)
                curl_i = abs(div_m - div_v)
                curl_j = abs(div_m - div_p)
                curl_k = abs(div_v - div_p)
                curl_values.append(np.sqrt(curl_i**2 + curl_j**2 + curl_k**2))
        curl = np.mean(curl_values) if curl_values else 0.0
        flux_values = []
        for i in range(-10, 0):
            flow = price_changes[i] * vols[i]
            flux_values.append(flow)
        flux = np.mean(flux_values) if flux_values else 0.0
        if divergence > 0.05 and flux > 0:
            signal = 'تدفق دخول قوي 🟢'
            strength = 'قوي' if divergence > 0.1 else 'متوسط'
        elif divergence < -0.05 and flux < 0:
            signal = 'تدفق خروج قوي 🔴'
            strength = 'قوي' if divergence < -0.1 else 'متوسط'
        elif curl > 0.3:
            signal = 'دوامات وتقلبات ️'
            strength = 'عالي التقلب'
        else:
            signal = 'محايد ⚪'
            strength = 'ضعيف'
        return {'divergence': float(divergence), 'curl': float(curl), 'flux': float(flux), 'signal': signal, 'strength': strength}
    except Exception as e:
        logger.error(f"VFM error: {e}")
        return {'divergence': 0, 'curl': 0, 'flux': 0, 'signal': 'خطأ', 'strength': 'غير محدد'}

def get_action_recommendation(data):
    try:
        vgs = data.get('vgs', {})
        vgs_value = vgs.get('vgs', 0)
        vgs_direction = vgs.get('direction', 'محايد')
        vfm = data.get('vfm', {})
        vfm_div = vfm.get('divergence', 0)
        vfm_flux = vfm.get('flux', 0)
        rsi = data.get('rsi', 50)
        kelly = data.get('kelly', {}).get('kelly_half', 0.1)
        mc_prob = data.get('mc', {}).get('prob_profit', 50)
        direction = data.get('direction', 'انتظار')
        buy_signals = data.get('buy_signals', 0)
        score = 0
        if direction == 'شراء': score += 5
        elif direction == 'بيع': score -= 5
        if vgs_direction == 'صعودي 🟢' and vgs_value > 0.08: score += 2
        elif vgs_direction == 'صعودي 🟢': score += 1
        elif vgs_direction == 'هبوطي 🔴' and vgs_value > 0.08: score -= 2
        elif vgs_direction == 'هبوطي 🔴': score -= 1
        if vfm_div > 0.05 and vfm_flux > 0: score += 1
        elif vfm_div < -0.05 and vfm_flux < 0: score -= 1
        ob_analysis = data.get('ob_analysis')
        if ob_analysis:
            ob_signal = ob_analysis.get('signal', '')
            if 'شراء قوي' in ob_signal: score += 1
            elif 'بيع قوي' in ob_signal: score -= 1
        if rsi < 30: score += 1
        elif rsi < 40: score += 0.5
        elif rsi > 70: score -= 1
        elif rsi > 60: score -= 0.5
        if kelly > 0.15: score += 1 if score > 0 else -1
        elif kelly < 0.05: score -= 0.5 if score > 0 else 0.5
        if mc_prob > 65: score += 1
        elif mc_prob < 35: score -= 1
        if buy_signals >= 6: score += 1
        elif buy_signals <= 2: score -= 1
        if score >= 6:
            return {'action': 'افتح شراء (Long) قوي ', 'emoji': '🟢', 'trade_type': 'LONG', 'description': 'زخم صعودي قوي مع تأكيد من جميع المؤشرات.', 'confidence': 'عالية جداً', 'score': score}
        elif score >= 3:
            return {'action': 'افتح شراء (Long) ', 'emoji': '🟢', 'trade_type': 'LONG', 'description': 'مؤشرات إيجابية.', 'confidence': 'عالية', 'score': score}
        elif score >= 1:
            return {'action': 'دخول حذر (Long) 🟡', 'emoji': '', 'trade_type': 'LONG', 'description': 'إشارات إيجابية ضعيفة.', 'confidence': 'متوسطة', 'score': score}
        elif score >= -1:
            return {'action': 'انتظار ⚪', 'emoji': '', 'trade_type': 'WAIT', 'description': 'السوق متذبذب.', 'confidence': 'منخفضة', 'score': score}
        elif score >= -3:
            return {'action': 'دخول حذر (Short) 🟠', 'emoji': '🟠', 'trade_type': 'SHORT', 'description': 'إشارات سلبية ضعيفة.', 'confidence': 'متوسطة', 'score': score}
        elif score >= -6:
            return {'action': 'افتح بيع (Short) 🟠', 'emoji': '🟠', 'trade_type': 'SHORT', 'description': 'زخم هبوطي واضح.', 'confidence': 'عالية', 'score': score}
        else:
            return {'action': 'افتح بيع (Short) قوي 🔴', 'emoji': '🔴', 'trade_type': 'SHORT', 'description': 'زخم هبوطي قوي جداً.', 'confidence': 'عالية جداً', 'score': score}
    except Exception as e:
        logger.error(f"Recommendation error: {e}")
        return {'action': 'انتظار ⚪', 'emoji': '⚪', 'trade_type': 'WAIT', 'description': 'تعذر حساب التوصية', 'confidence': 'غير محدد', 'score': 0}

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
        if np.isnan(current_price) or current_price <= 0:
            return None
        rsi = calc_rsi(closes)
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        macd = calc_macd(closes)
        atr = calc_atr(highs, lows, closes)
        ou = calc_ou_model(closes)
        kelly = calc_kelly(closes)
        mc = monte_carlo(closes)
        news = fetch_news(symbol)
        vgs_data = calc_vector_gradient_strength(closes, volumes)
        vfm_data = calc_vector_flow_model(closes, volumes)
        order_book = fetch_order_book(symbol)
        ob_analysis = analyze_order_book(order_book) if order_book else None
        vol_msg = "محايد"
        if len(volumes) >= 20:
            recent_vol = np.mean(volumes[-5:])
            avg_vol = np.mean(volumes[-20:])
            if recent_vol > avg_vol * 1.2: vol_msg = "مرتفع 📈"
            elif recent_vol < avg_vol * 0.8: vol_msg = "منخفض 📉"
        buy_signals = 0
        if current_price > ema20: buy_signals += 1
        if current_price > ema50: buy_signals += 1
        if macd > 0: buy_signals += 1
        if rsi < 75: buy_signals += 1
        if mc['prob_profit'] > 55: buy_signals += 1
        if ou['z_score'] < 1.5: buy_signals += 1
        if vgs_data['direction'] == 'صعودي 🟢' and vgs_data['vgs'] > 0.05: buy_signals += 1
        if vfm_data['divergence'] > 0.05 and vfm_data['flux'] > 0: buy_signals += 1
        direction = 'شراء' if buy_signals >= 4 else ('بيع' if buy_signals <= 2 else 'انتظار')
        recommendation = get_action_recommendation({
            'vgs': vgs_data, 'vfm': vfm_data, 'rsi': rsi, 'kelly': kelly, 'mc': mc,
            'direction': direction, 'buy_signals': buy_signals, 'ob_analysis': ob_analysis
        })
        trade_type = recommendation.get('trade_type', 'WAIT')
        if direction == 'شراء':
            sl = current_price - (atr * 1.0)
            tp1 = current_price + (atr * 1.5)
            tp2 = current_price + (atr * 2.5)
            tp3 = current_price + (atr * 4.0)
        elif direction == 'بيع':
            sl = current_price + (atr * 1.0)
            tp1 = current_price - (atr * 1.5)
            tp2 = current_price - (atr * 2.5)
            tp3 = current_price - (atr * 4.0)
        else:
            if vgs_data['direction'] == 'هبوطي 🔴':
                sl = current_price + (atr * 1.0)
                tp1 = current_price - (atr * 1.5)
                tp2 = current_price - (atr * 2.5)
                tp3 = current_price - (atr * 4.0)
            else:
                sl = current_price - (atr * 1.0)
                tp1 = current_price + (atr * 1.5)
                tp2 = current_price + (atr * 2.5)
                tp3 = current_price + (atr * 4.0)
        rr = abs(tp1 - current_price) / abs(current_price - sl) if abs(current_price - sl) > 0 else 0
        return {
            'symbol': symbol, 'price': current_price, 'rsi': rsi,
            'ema20': ema20, 'ema50': ema50, 'macd': macd, 'atr': atr,
            'ou': ou, 'kelly': kelly, 'mc': mc, 'vol_msg': vol_msg,
            'direction': direction, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
            'rr': rr, 'news': news, 'buy_signals': buy_signals,
            'vgs': vgs_data, 'vfm': vfm_data, 'recommendation': recommendation, 
            'trade_type': trade_type, 'order_book': order_book, 'ob_analysis': ob_analysis
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
        vfm = data.get('vfm', {})
        rec = data.get('recommendation', {})
        ob_an = data.get('ob_analysis', {})
        ob_text = ""
        if ob_an:
            ob_text = f"\nضغط الشراء: {ob_an.get('buy_pressure', 0):.1f}%\nضغط البيع: {ob_an.get('sell_pressure', 0):.1f}%\nإشارة Order Book: {ob_an.get('signal', 'لا بيانات')}\nالمصدر: {ob_an.get('source', 'غير معروف')}"
        prompt = f"""أنت محلل مالي خبير. حلل البيانات التالية بالعربية:

الرمز: {data['symbol']}
السعر: ${data['price']:,.2f}
RSI: {data['rsi']:.1f}
EMA20: ${data['ema20']:,.2f} | EMA50: ${data['ema50']:,.2f}
MACD: {data['macd']:.2f}
ATR: ${data['atr']:,.2f}
Z-Score: {data['ou']['z_score']:.2f}
Kelly: {data['kelly']['kelly_half']*100:.1f}%
احتمال الربح: {data['mc']['prob_profit']:.1f}%
حجم التداول: {data['vol_msg']}
VGS: {vgs.get('vgs', 0):.3f} - {vgs.get('direction', 'محايد')}
VFM: {vfm.get('signal', 'محايد')}
{ob_text}
التوصية: {rec.get('action', 'انتظار')} (نقاط: {rec.get('score', 0)})

الأخبار: {news_text}

أعطِ:
1. القرار
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
            "temperature": 0.3, "max_tokens": 1000
        }
        res = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=45)
        if res.status_code == 200:
            result = res.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
        return None
    except Exception as e:
        logger.error(f"Qwen error: {e}")
        return None

def send_unified_report(data, ai_analysis=None):
    news_text = "\n\n".join(data.get('news', ['لا توجد أخبار']))
    if data['direction'] == 'شراء': direction_display = 'شراء 🟢'
    elif data['direction'] == 'بيع': direction_display = 'بيع 🔴'
    else: direction_display = 'انتظار ⚪'
    vgs = data.get('vgs', {})
    vfm = data.get('vfm', {})
    rec = data.get('recommendation', {})
    ob = data.get('order_book')
    ob_an = data.get('ob_analysis')
    
    part1 = f"""🎯 <b>التقرير الشامل الموحد لـ {data['symbol']}</b>
{'='*45}

💰 <b>السعر الحالي:</b> ${data['price']:,.2f}
 <b>الاتجاه:</b> {direction_display}

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

🎲 <b>مونت كارلو (30 يوم):</b>
• احتمال الربح: {data['mc']['prob_profit']:.1f}%
• احتمال خسارة 10%+: {data['mc']['prob_loss_10']:.1f}%
• نطاق 90%: ${data['mc']['p5']:,.2f} - ${data['mc']['p95']:,.2f}"""
    send_long_report(part2)
    
    part_vgs = f"""🎯 <b>قوة الاتجاه المتجهية (VGS):</b>
📐 المعادلة: ‖f‖ = √((∂f/∂P)² + (∂f/V)² + (∂f/∂R)² + (∂f/∂M)²)

• معيار التدرج ‖∇f‖: {vgs.get('vgs', 0):.3f}
• القوة: {vgs.get('strength', 'محايد')}
• اتجاه التدرج: {vgs.get('direction', 'محايد')}
• ∂f/∂P (السعر): {vgs.get('grad_P', 0):.3f}
• ∂f/∂V (الحجم): {vgs.get('grad_V', 0):.3f}
• ∂f/∂R (RSI): {vgs.get('grad_R', 0):.3f}
• ∂f/∂M (MACD): {vgs.get('grad_M', 0):.3f}

💡 <i>كلما ارتفع معيار التدرج، زادت قوة الاتجاه.</i>"""
    send_long_report(part_vgs)
    
    part_vfm = f"""🌊 <b>نموذج التدفق المتجهي (VFM):</b>
📐 المعادلات: ∇·F (التباعد)، |∇×F| (الالتواء)، Φ (التدفق)

• التباعد (∇·F): {vfm.get('divergence', 0):.3f}
• الالتواء (|∇×F|): {vfm.get('curl', 0):.3f}
• التدفق (Φ): {vfm.get('flux', 0):.2f}
• الإشارة: {vfm.get('signal', 'محايد')}
• القوة: {vfm.get('strength', 'ضعيف')}

💡 <i>التباعد الإيجابي = أموال تدخل الأصل.</i>"""
    send_long_report(part_vfm)
    
    if ob and ob_an:
        est_mark = " ️ (تقديري)" if ob_an.get('estimated') else ""
        ob_text = f"""{'='*45}
 <b>كتاب الأوامر (Order Book){est_mark} - {ob_an.get('source', 'Binance')}:</b>
{'='*45}

💰 <b>أفضل الأسعار:</b>
• أعلى سعر شراء (Best Bid): ${ob['best_bid']:,.4f}
• أقل سعر بيع (Best Ask): ${ob['best_ask']:,.4f}
• السبريد: ${ob['spread']:,.4f} ({ob['spread_percent']:.3f}%) - {ob_an['spread_status']}

⚖️ <b>توازن القوى:</b>
• ضغط الشراء: {ob_an['buy_pressure']:.1f}% {'🟢' if ob_an['buy_pressure'] > 55 else ''}
• ضغط البيع: {ob_an['sell_pressure']:.1f}% {'🔴' if ob_an['sell_pressure'] > 55 else ''}
• الإشارة: {ob_an['signal']}
• القوة: {ob_an['strength']}

📈 <b>مستويات السيولة الكبيرة:</b>
"""
        if ob['support_levels']:
            ob_text += "🟢 <b>دعم قوي (تجمعات شراء):</b>\n"
            for i, sup in enumerate(ob['support_levels'], 1):
                ob_text += f"  {i}. ${sup['price']:,.4f} (حجم: {sup['volume']:,.2f})\n"
        else:
            ob_text += "⚪ لا توجد تجمعات شراء كبيرة\n"
        if ob['resistance_levels']:
            ob_text += "\n🔴 <b>مقاومة قوية (تجمعات بيع):</b>\n"
            for i, res in enumerate(ob['resistance_levels'], 1):
                ob_text += f"  {i}. ${res['price']:,.4f} (حجم: {res['volume']:,.2f})\n"
        else:
            ob_text += "\n⚪ لا توجد تجمعات بيع كبيرة\n"
        ob_text += f"""
💡 <i>ضغط الشراء > 60% = أموال تدخل (إيجابي)
ضغط البيع > 60% = أموال تخرج (سلبي)</i>"""
        send_long_report(ob_text)
    
    part_news = f""" <b>آخر الأخبار:</b>
{news_text}"""
    send_long_report(part_news)
    
    if ai_analysis:
        part_ai = f"""{'='*45}
🤖 <b>تحليل Qwen AI (خبير مالي):</b>
{'='*45}
{ai_analysis}"""
        send_long_report(part_ai)
    
    part_levels = f"""{'='*45}
💰 <b>مستويات التداول ({rec.get('action', 'انتظار')}):</b>
• وقف الخسارة (SL): ${data['sl']:,.2f}
• الهدف 1 (TP1): ${data['tp1']:,.2f}
• الهدف 2 (TP2): ${data['tp2']:,.2f}
• الهدف 3 (TP3): ${data['tp3']:,.2f}
• المخاطرة/العائد: 1:{data['rr']:.2f}"""
    send_long_report(part_levels)
    
    part_rec = f"""{'='*45}
🎯 <b>توصية الإجراء النهائية:</b>
{'='*45}

{rec.get('emoji', '')} <b>{rec.get('action', 'انتظار')}</b>

📋 <b>الوصف:</b> {rec.get('description', '')}
📊 <b>مستوى الثقة:</b> {rec.get('confidence', 'غير محدد')}
🔢 <b>نقاط التقييم:</b> {rec.get('score', 0)}

💡 <i>هذه التوصية مبنية على دمج جميع المؤشرات. ليست نصيحة مالية.</i>"""
    send_long_report(part_rec)

def smart_analysis(symbol):
    send_message(f"🔍 جاري التحليل الشامل لـ {symbol}...")
    data = unified_analysis(symbol)
    if not data:
        send_message(f"❌ فشل تحليل {symbol}. تأكد من الرمز أو حاول لاحقاً.")
        return
    if OPENROUTER_API_KEY:
        send_message("🤖 جاري تحليل Qwen AI...")
        ai = ask_qwen(data)
        if not ai:
            send_message("⚠️ فشل تحليل Qwen AI")
    else:
        send_message("⚠️ مفتاح Qwen غير موجود")
        ai = None
    send_unified_report(data, ai)

def test_order_book():
    """اختبار Order Book مباشرة"""
    send_message("🔍 <b>جاري اختبار Order Book...</b>")
    test_symbols = ['BTC-USD', 'ETH-USD', 'XRP-USD']
    for symbol in test_symbols:
        try:
            ob = fetch_order_book(symbol)
            if ob:
                ob_an = analyze_order_book(ob)
                est_mark = "⚠️ تقديري" if ob.get('estimated') else "✅ حقيقي"
                msg = f"✅ <b>{symbol}</b> ({est_mark})\n"
                msg += f"   📡 المصدر: {ob.get('source', 'غير معروف')}\n"
                msg += f"   💰 Bid: ${ob['best_bid']:,.2f}\n"
                msg += f"    Ask: ${ob['best_ask']:,.2f}\n"
                msg += f"    Spread: {ob['spread_percent']:.3f}%\n"
                msg += f"   🟢 ضغط الشراء: {ob['buy_pressure']:.1f}%\n"
                msg += f"   🔴 ضغط البيع: {ob['sell_pressure']:.1f}%\n"
                if ob_an:
                    msg += f"   🎯 الإشارة: {ob_an['signal']}\n"
                send_message(msg)
            else:
                send_message(f"❌ <b>{symbol}</b>: فشل في جلب Order Book")
        except Exception as e:
            send_message(f"❌ <b>{symbol}</b>: خطأ - {e}")
        time.sleep(1)

def simple_report():
    try:
        report = "📊 <b>تقرير السوق</b>\n\n"
        report += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        symbols = ['BTC-USD', 'ETH-USD', 'AAPL']
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    if not np.isnan(price) and price > 0:
                        report += f"✅ <b>{symbol}</b>: ${price:.2f}\n"
                    else:
                        report += f"⚠️ <b>{symbol}</b>: بيانات غير متاحة\n"
                else:
                    report += f"⚠️ <b>{symbol}</b>: لا بيانات\n"
            except:
                report += f"❌ <b>{symbol}</b>: خطأ\n"
        send_message(report)
    except Exception as e:
        send_message(f"خطأ: {str(e)}")

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start':
        send_message("🤖 <b>بوت التداول الذكي الشامل</b>\n\nالأوامر:\n/smart <رمز> - تحليل شامل\n/test_ob - اختبار Order Book\n/test_report - تقرير بسيط\n/help - المساعدة")
    elif cmd == '/test_ob':
        send_message(" جاري اختبار Order Book...")
        test_order_book()
    elif cmd == '/test_report':
        send_message("⏳ جاري...")
        simple_report()
    elif cmd.startswith('/smart'):
        parts = command.split(' ', 1)
        if len(parts) < 2:
            send_message("الاستخدام: /smart <رمز>\nمثال: /smart BTC-USD\nمثال: /smart XAUUSD")
            return
        symbol = parts[1].strip().upper()
        symbol = symbol.replace(' ', '')
        smart_analysis(symbol)
    elif cmd == '/help':
        send_message("<b>الأوامر:</b>\n/smart <رمز> - تحليل شامل\n/test_ob - اختبار Order Book\n/test_report - تقرير بسيط\n/help - مساعدة")
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
    logger.info("Starting Unified Trading Bot with Safe Order Book + Fallbacks...")
    logger.info(f"✅ Supported crypto for Order Book: {len(CRYPTO_SYMBOLS)} symbols")
    threading.Thread(target=monitor_urgent_news, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
