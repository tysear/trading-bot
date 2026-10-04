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
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
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
def home():
    return "Bot is running!"

@app.route('/health')
def health():
    return {"status": "ok"}

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def fetch_crypto(symbol):
    try:
        logger.info(f"Fetching {symbol} from yfinance...")
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty:
            return None
        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        logger.info(f"Got {len(closes)} days for {symbol}")
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
        signals.append(f"RSI={rsi:.1f} oversold")
    elif rsi > 70:
        score -= 2
        signals.append(f"RSI={rsi:.1f} overbought")
    else:
        signals.append(f"RSI={rsi:.1f} neutral")
    if price > ema20 > ema50:
        score += 2
        signals.append("Price above EMA20 and EMA50")
    elif price < ema20 < ema50:
        score -= 2
        signals.append("Price below EMA20 and EMA50")
    else:
        signals.append("EMA neutral")
    if macd > 0:
        score += 1
        signals.append("MACD positive")
    else:
        score -= 1
        signals.append("MACD negative")
    if price <= bb_lower:
        score += 2
        signals.append("At lower BB")
    elif price >= bb_upper:
        score -= 2
        signals.append("At upper BB")
    if p1 > 0 and p2 > 0:
        score += 1
        signals.append("Upward momentum")
    elif p1 < 0 and p2 < 0:
        score -= 1
        signals.append("Downward momentum")
    if score >= 5:
        rec = "Strong Buy"
        direction = 'buy'
    elif score >= 3:
        rec = "Buy"
        direction = 'buy'
    elif score <= -5:
        rec = "Strong Sell"
        direction = 'sell'
    elif score <= -3:
        rec = "Sell"
        direction = 'sell'
    else:
        rec = "Wait"
        direction = None
    if direction == 'buy':
        sl = price - (atr * 1.5)
        tp1 = price + (atr * 2)
        tp2 = price + (atr * 3)
        tp3 = price + (atr * 4.5)
    elif direction == 'sell':
        sl = price + (atr * 1.5)
        tp1 = price - (atr * 2)
        tp2 = price - (atr * 3)
        tp3 = price - (atr * 4.5)
    else:
        sl = price - (atr * 1.5)
        tp1 = price + (atr * 2)
        tp2 = price + (atr * 3)
        tp3 = price + (atr * 4.5)
    risk = abs(price - sl)
    reward = abs(tp2 - price)
    rr_ratio = reward / risk if risk > 0 else 0
    return {
        'symbol': symbol, 'type': asset_type, 'price': price, 'rsi': rsi,
        'ema20': ema20, 'score': score, 'recommendation': rec,
        'direction': direction, 'signals': signals,
        'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'rr_ratio': rr_ratio
    }

def unified_analysis(symbol, asset_type, balance=10000):
    try:
        if asset_type == 'crypto':
            data = fetch_crypto(symbol)
        else:
            data = fetch_stock(symbol)
        if not data or len(data['closes']) < 50:
            return None
        closes = np.array(data['closes'], dtype=float)
        highs = np.array(data['highs'], dtype=float)
        lows = np.array(data['lows'], dtype=float)
        current_price = float(closes[-1])
        rsi = calc_rsi(closes)
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        macd = calc_macd(closes)
        atr = calc_atr(highs, lows, closes)
        returns = np.diff(np.log(closes))
        lookback = min(50, len(closes) - 1)
        spread_lag = closes[-lookback-1:-1]
        spread_diff = np.diff(closes[-lookback-1:])
        if len(spread_lag) < 10:
            ou_theta = 0.01
            ou_mu = current_price
            ou_z_score = 0.0
            ou_half_life = 999.0
        else:
            slope, intercept, r_value, p_value, std_err = linregress(spread_lag, spread_diff)
            ou_theta = float(-slope) if slope < 0 else 0.01
            ou_mu = float(np.mean(closes[-lookback:]))
            ou_sigma = float(np.std(returns[-lookback:])) * np.sqrt(252)
            ou_half_life = float(np.log(2) / ou_theta) if ou_theta > 0 else 999.0
            ou_z_score = float((current_price - ou_mu) / (ou_sigma * current_price)) if ou_sigma > 0 else 0.0
        wins = 0
        total = 0
        lookback_kelly = min(50, len(closes) - 5)
        for i in range(-lookback_kelly, -5):
            if closes[i+5] > closes[i]:
                wins += 1
            total += 1
        win_rate = float(wins / total) if total > 0 else 0.5
        win_loss_ratio = 2.0 if current_price > ema20 else 1.5
        kelly_edge = float((win_rate * win_loss_ratio) - (1 - win_rate))
        kelly_full = float(kelly_edge / win_loss_ratio) if win_loss_ratio > 0 else 0.0
        kelly_half = min(float(kelly_full / 2), 0.25)
        kelly_quarter = float(kelly_full / 4)
        mu_return = float(np.mean(returns))
        sigma_return = float(np.std(returns))
        np.random.seed(42)
        simulations = 1000
        days = 30
        simulated = np.zeros((simulations, days + 1))
        simulated[:, 0] = current_price
        for t in range(1, days + 1):
            random_returns = np.random.normal(mu_return, sigma_return, simulations)
            simulated[:, t] = simulated[:, t-1] * np.exp(random_returns)
        final_prices = simulated[:, -1]
        prob_profit = float(np.sum(final_prices > current_price) / simulations * 100)
        prob_loss_10 = float(np.sum(final_prices < current_price * 0.9) / simulations * 100)
        prob_gain_10 = float(np.sum(final_prices > current_price * 1.1) / simulations * 100)
        percentile_5 = float(np.percentile(final_prices, 5))
        percentile_95 = float(np.percentile(final_prices, 95))
        vol_current = float(np.std(returns[-20:])) * np.sqrt(252) if len(returns) >= 20 else float(np.std(returns)) * np.sqrt(252)
        vol_long_term = float(np.std(returns)) * np.sqrt(252)
        risk_amount = balance * kelly_half
        sl_distance = atr * 1.5
        position_size = risk_amount / sl_distance if sl_distance > 0 else 0.0
        notional_value = position_size * current_price
        direction = 'buy' if current_price > ema20 and rsi < 70 else 'sell'
        if direction == 'buy':
            sl = current_price - (atr * 1.5)
            tp1 = current_price + (atr * 2)
            tp2 = current_price + (atr * 3)
            tp3 = current_price + (atr * 4.5)
        else:
            sl = current_price + (atr * 1.5)
            tp1 = current_price - (atr * 2)
            tp2 = current_price - (atr * 3)
            tp3 = current_price - (atr * 4.5)
        risk_reward = float(abs(tp2 - current_price) / abs(current_price - sl)) if abs(current_price - sl) > 0 else 0.0
        score = 0
        if rsi < 30: score += 3
        elif rsi < 40: score += 2
        elif rsi > 70: score -= 3
        elif rsi > 60: score -= 1
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
        if score >= 8:
            recommendation = "Strong Buy"
            confidence = "Very High"
        elif score >= 5:
            recommendation = "Buy"
            confidence = "High"
        elif score >= 2:
            recommendation = "Cautious Buy"
            confidence = "Medium"
        elif score <= -8:
            recommendation = "Strong Sell"
            confidence = "Very High"
        elif score <= -5:
            recommendation = "Sell"
            confidence = "High"
        elif score <= -2:
            recommendation = "Cautious Sell"
            confidence = "Medium"
        else:
            recommendation = "Wait"
            confidence = "Low"
        return {
            'symbol': symbol, 'type': asset_type, 'price': current_price,
            'rsi': rsi, 'ema20': ema20, 'ema50': ema50, 'macd': macd, 'atr': atr,
            'direction': direction, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
            'risk_reward': risk_reward, 'ou_theta': ou_theta, 'ou_mu': ou_mu,
            'ou_z_score': ou_z_score, 'ou_half_life': ou_half_life,
            'win_rate': win_rate, 'kelly_edge': kelly_edge, 'kelly_full': kelly_full,
            'kelly_half': kelly_half, 'kelly_quarter': kelly_quarter,
            'position_size': position_size, 'notional_value': notional_value,
            'risk_amount': risk_amount, 'prob_profit': prob_profit,
            'prob_loss_10': prob_loss_10, 'prob_gain_10': prob_gain_10,
            'percentile_5': percentile_5, 'percentile_95': percentile_95,
            'vol_current': vol_current, 'vol_long_term': vol_long_term,
            'score': score, 'recommendation': recommendation, 'confidence': confidence
        }
    except Exception as e:
        logger.error(f"Error in unified_analysis for {symbol}: {e}")
        return None

def format_unified_analysis(result):
    msg = f"""
Analysis for {result['symbol']}
{'='*40}

Current Price: ${result['price']:,.2f}

Technical Analysis:
- RSI: {result['rsi']:.1f}
- EMA20: ${result['ema20']:,.2f}
- EMA50: ${result['ema50']:,.2f}
- MACD: {result['macd']:.4f}
- ATR: ${result['atr']:.2f}

Ornstein-Uhlenbeck Model:
- Long-term mean: ${result['ou_mu']:,.2f}
- Mean reversion speed (theta): {result['ou_theta']:.4f}
- Half-life: {result['ou_half_life']:.1f} days
- Z-Score: {result['ou_z_score']:.2f}

Kelly Criterion:
- Win rate: {result['win_rate']*100:.1f}%
- Edge: {result['kelly_edge']*100:.2f}%
- Safe Kelly (Half): {result['kelly_half']*100:.2f}%

Monte Carlo (30 days):
- Profit probability: {result['prob_profit']:.1f}%
- 10%+ loss probability: {result['prob_loss_10']:.1f}%
- 90% confidence range: ${result['percentile_5']:,.2f} - ${result['percentile_95']:,.2f}

Heston Volatility:
- Current vol: {result['vol_current']*100:.2f}%
- Long-term vol: {result['vol_long_term']*100:.2f}%

{'='*40}
{result['recommendation']}
Confidence: {result['confidence']} | Score: {result['score']}

Trade Details (Half Kelly):
- Direction: {result['direction'].upper()}
- Position size: {result['position_size']:.4f} units
- Value: ${result['notional_value']:,.2f}
- Risk amount: ${result['risk_amount']:,.2f}

Risk Management:
- SL: ${result['sl']:,.2f}
- TP1: ${result['tp1']:,.2f}
- TP2: ${result['tp2']:,.2f}
- TP3: ${result['tp3']:,.2f}
- Risk:Reward = 1:{result['risk_reward']:.2f}

Not financial advice.
"""
    return msg

def send_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Error sending: {e}")
        return False

def format_signal(r):
    msg = f"<b>{r['symbol']}</b> ({r['type']})\n"
    msg += f"Price: ${r['price']:.2f}\n"
    msg += f"RSI: {r['rsi']:.1f}\n"
    msg += f"Signal: {r['recommendation']}\n"
    msg += f"Score: {r['score']}\n\n"
    if r['direction']:
        msg += f"SL: ${r['sl']:.2f}\n"
        msg += f"TP1: ${r['tp1']:.2f}\n"
        msg += f"TP2: ${r['tp2']:.2f}\n"
        msg += f"TP3: ${r['tp3']:.2f}\n"
        msg += f"R:R = 1:{r['rr_ratio']:.1f}\n\n"
    msg += "━━━━━━━━━━━━\n\n"
    return msg

def analyze_all():
    message = f"<b>Market Report</b>\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
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
        message += "<b>Buy Opportunities:</b>\n\n"
        for r in buys[:5]: message += format_signal(r)
    sells = [r for r in all_results if r['score'] <= -3]
    if sells:
        message += "\n<b>Sell Opportunities:</b>\n\n"
        for r in sells[:5]: message += format_signal(r)
    waits = [r for r in all_results if -3 < r['score'] < 3]
    if waits:
        message += "\n<b>Wait:</b>\n\n"
        for r in waits[:7]:
            message += f"• {r['symbol']} - ${r['price']:.2f} | RSI: {r['rsi']:.1f}\n\n"
    send_message(message)
    logger.info("Report sent!")

def ask_groq(question, context=""):
    if not GROQ_API_KEY:
        return "Groq not configured"
    try:
        prompt = f"Answer in Arabic clearly.\n\n{context}\n\nQuestion: {question}\n\nNote: This is not financial advice."
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 1024}
        response = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=30)
        data = response.json()
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        return f"Error: {data.get('error', {}).get('message', 'Unknown')}"
    except Exception as e:
        return f"Error: {str(e)}"

def ask_qwen(question, context=""):
    if not OPENROUTER_API_KEY:
        return "OpenRouter not configured"
    try:
        prompt = f"Answer in Arabic in detail.\n\n{context}\n\nQuestion: {question}\n\nNote: This is not financial advice."
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://github.com/tysear/trading-bot", "X-Title": "Trading Bot"}
        payload = {"model": "qwen/qwen-2.5-72b-instruct", "messages": [{"role": "user", "content": prompt}], "temperature": 0.4, "max_tokens": 2048}
        response = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=60)
        data = response.json()
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        return f"Error: {data.get('error', {}).get('message', 'Unknown')}"
    except Exception as e:
        return f"Error: {str(e)}"

def ask_ai(question, context="", model_preference="auto"):
    if model_preference == "auto":
        model = "groq" if len(question) < 100 else "qwen"
    else:
        model = model_preference
    if model == "qwen":
        try:
            answer = ask_qwen(question, context)
            if not answer.startswith("Error"):
                return answer
        except: pass
    try:
        answer = ask_groq(question, context)
        if not answer.startswith("Error"):
            return answer
    except: pass
    return "Sorry, could not get answer."

def handle_command(command):
    cmd = command.strip().lower()
    if cmd == '/start':
        send_message("""<b>Smart Trading Bot</b>

Commands:
/report - Full report
/crypto - Crypto only
/stocks - Stocks only
/metals - Metals
/forex - Forex
/urgent - Urgent alerts

AI:
/ask <question> - Auto AI
/qwen <question> - Qwen 2.5 72B
/ai <model> <question>

Advanced:
/smart <symbol> - Full analysis

/help - Help""")
    elif cmd == '/report':
        send_message("Generating report...")
        analyze_all()
    elif cmd == '/crypto':
        send_message("Analyzing crypto...")
        message = "<b>Crypto Report</b>\n\n"
        for symbol in CRYPTO_LIST:
            result = analyze_asset(symbol, 'crypto')
            if result: message += format_signal(result)
        send_message(message)
    elif cmd == '/stocks':
        send_message("Analyzing stocks...")
        message = "<b>Stocks Report</b>\n\n"
        for symbol in STOCKS_LIST:
            result = analyze_asset(symbol, 'stock')
            if result: message += format_signal(result)
        send_message(message)
    elif cmd == '/metals':
        send_message("Analyzing metals...")
        message = "<b>Metals Report</b>\n\n"
        for symbol in METALS_LIST:
            result = analyze_asset(symbol, 'metal')
            if result: message += format_signal(result)
        send_message(message)
    elif cmd == '/forex':
        send_message("Analyzing forex...")
        message = "<b>Forex Report</b>\n\n"
        for symbol in FOREX_LIST:
            result = analyze_asset(symbol, 'forex')
            if result: message += format_signal(result)
        send_message(message)
    elif cmd == '/urgent':
        send_message("Checking alerts...")
        for symbol in CRYPTO_LIST[:3]:
            result = analyze_asset(symbol, 'crypto')
            if result and result['rsi'] < 25:
                send_message(f"<b>{result['symbol']}</b>\nRSI = {result['rsi']:.1f}\nStrong buy signal!")
            elif result and result['rsi'] > 75:
                send_message(f"<b>{result['symbol']}</b>\nRSI = {result['rsi']:.1f}\nStrong sell signal!")
        send_message("Check complete!")
    elif cmd.startswith('/ask '):
        question = command[5:].strip()
        if not question:
            send_message("Please write a question after /ask")
            return
        send_message("Thinking...")
        answer = ask_ai(question)
        send_message(f"<b>Answer:</b>\n\n{answer}")
    elif cmd.startswith('/qwen '):
        question = command[6:].strip()
        if not question:
            send_message("Please write a question after /qwen")
            return
        send_message("Thinking with Qwen...")
        answer = ask_qwen(question)
        send_message(f"<b>Qwen 2.5 72B:</b>\n\n{answer}")
    elif cmd.startswith('/ai '):
        parts = command.split(maxsplit=2)
        if len(parts) < 3:
            send_message("Usage: /ai groq|qwen <question>")
            return
        model = parts[1].lower()
        question = parts[2]
        send_message(f"Thinking with {model.upper()}...")
        answer = ask_ai(question, model_preference=model)
        send_message(f"<b>{model.upper()}:</b>\n\n{answer}")
    elif cmd.startswith('/smart'):
        parts = command.split()
        if len(parts) < 2:
            send_message("""<b>Smart Analysis:</b>

/smart <symbol>

Examples:
/smart BTC-USD
/smart AAPL
/smart GC=F""")
            return
        symbol = parts[1].upper()
        if symbol in CRYPTO_LIST:
            asset_type = 'crypto'
        elif symbol in STOCKS_LIST:
            asset_type = 'stock'
        elif symbol in METALS_LIST:
            asset_type = 'metal'
        elif symbol in FOREX_LIST:
            asset_type = 'forex'
        else:
            asset_type = 'stock'
        send_message(f"Analyzing {symbol}...")
        result = unified_analysis(symbol, asset_type)
        if not result:
            send_message(f"Failed to analyze {symbol}\n\nPossible reasons:\n- Not enough data\n- API connection error\n- Invalid symbol\n\nTry: /smart BTC-USD or /smart AAPL")
            return
        msg = format_unified_analysis(result)
        send_message(msg)
        send_message("Getting AI analysis...")
        context = f"""Data for {result['symbol']}:
- Price: ${result['price']:,.2f}
- RSI: {result['rsi']:.1f}
- OU Z-Score: {result['ou_z_score']:.2f}
- Kelly Half: {result['kelly_half']*100:.2f}%
- Profit probability: {result['prob_profit']:.1f}%
- Volatility: {result['vol_current']*100:.2f}%
- Recommendation: {result['recommendation']}
- Score: {result['score']}"""
        question = f"Based on these equations:\n1. Is this trade recommended?\n2. What are the main risks?\n3. What is your final advice?"
        ai_answer = ask_qwen(question, context)
        send_message(f"<b>Qwen Analysis:</b>\n\n{ai_answer}")
    elif cmd == '/help':
        send_message("""<b>Bot Guide:</b>

Reports: /report, /crypto, /stocks, /metals, /forex
AI: /ask, /qwen, /ai
Advanced: /smart <symbol>
Alerts: /urgent

Trading involves risk.""")
    else:
        if OPENROUTER_API_KEY or GROQ_API_KEY:
            send_message("Thinking...")
            answer = ask_ai(command)
            send_message(f"{answer}")
        else:
            send_message("Commands: /start, /help")

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
                    if chat_id == CHAT_ID:
                        if text.startswith('/'):
                            handle_command(text)
                        else:
                            if OPENROUTER_API_KEY or GROQ_API_KEY:
                                send_message("Thinking...")
                                answer = ask_ai(text)
                                send_message(f"{answer}")
                            else:
                                send_message("Use /help")
                        logger.info(f"Processed: {text[:50]}")
                LAST_UPDATE_ID = max(LAST_UPDATE_ID, update_id)
    except Exception as e:
        logger.error(f"Error listening: {e}")

def scheduled_tasks():
    schedule.every(6).hours.do(analyze_all)
    schedule.every().day.at("09:00").do(analyze_all)
    schedule.every().day.at("15:00").do(analyze_all)
    schedule.every().day.at("21:00").do(analyze_all)
    schedule.every(1).hours.do(listen_for_commands)
    schedule.every(10).seconds.do(listen_for_commands)
    logger.info("Tasks scheduled!")

if __name__ == "__main__":
    print("=" * 60)
    print("Smart Trading Bot - Qwen + Groq")
    print("=" * 60)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Groq: {'OK' if GROQ_API_KEY else 'NOT SET'}")
    print(f"OpenRouter: {'OK' if OPENROUTER_API_KEY else 'NOT SET'}")
    print("Bot ready!")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server running")
    scheduled_tasks()
    logger.info("Running initial report...")
    analyze_all()
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Error: {e}")
        send_message(f"Error: {str(e)}")
