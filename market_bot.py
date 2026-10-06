import requests
import time
import yfinance as yf
from datetime import datetime
import logging
import os
from flask import Flask
import threading
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=15)
    except Exception as e:
        logger.error(f"Send error: {e}")

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
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calc_ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calc_macd(prices):
    return calc_ema(prices, 12) - calc_ema(prices, 26)

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02
    true_ranges = []
    for i in range(-period, 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        true_ranges.append(tr)
    return sum(true_ranges) / period

def analyze_symbol(symbol):
    """تحليل شامل لكن مستقر"""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        
        if hist.empty:
            return None
        
        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        
        current_price = closes[-1]
        rsi = calc_rsi(closes)
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        macd = calc_macd(closes)
        atr = calc_atr(highs, lows, closes)
        
        high_3m = max(highs)
        low_3m = min(lows)
        
        return {
            'symbol': symbol,
            'price': current_price,
            'rsi': rsi,
            'ema20': ema20,
            'ema50': ema50,
            'macd': macd,
            'atr': atr,
            'high': high_3m,
            'low': low_3m
        }
        
    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {e}")
        return None

def smart_analysis(symbol):
    """تحليل شامل موحد"""
    data = analyze_symbol(symbol)
    
    if not data:
        send_message(f"❌ فشل التحليل لـ {symbol}")
        return
    
    rsi = data['rsi']
    ema20 = data['ema20']
    ema50 = data['ema50']
    macd = data['macd']
    atr = data['atr']
    price = data['price']
    
    # تحديد الاتجاه
    buy_signals = 0
    if price > ema20:
        buy_signals += 1
    if price > ema50:
        buy_signals += 1
    if macd > 0:
        buy_signals += 1
    if rsi < 70:
        buy_signals += 1
    
    if buy_signals >= 3:
        direction = "شراء 🟢"
        sl = price - (atr * 1.5)
        tp1 = price + (atr * 2)
        tp2 = price + (atr * 3)
    else:
        direction = "بيع 🔴"
        sl = price + (atr * 1.5)
        tp1 = price - (atr * 2)
        tp2 = price - (atr * 3)
    
    report = f"🎯 <b>التحليل الشامل لـ {symbol}</b>\n\n"
    report += f" <b>التحليل الفني:</b>\n"
    report += f"• السعر: ${price:,.2f}\n"
    report += f"• RSI: {rsi:.1f}\n"
    report += f"• EMA20: ${ema20:,.2f}\n"
    report += f"• EMA50: ${ema50:,.2f}\n"
    report += f"• MACD: {macd:.2f}\n"
    report += f"• ATR: ${atr:,.2f}\n"
    report += f"• أعلى 3 أشهر: ${data['high']:,.2f}\n"
    report += f"• أدنى 3 أشهر: ${data['low']:,.2f}\n\n"
    
    report += f"🎯 <b>القرار:</b> {direction}\n\n"
    report += f"💰 <b>مستويات التداول:</b>\n"
    report += f"• وقف الخسارة (SL): ${sl:,.2f}\n"
    report += f"• الهدف 1 (TP1): ${tp1:,.2f}\n"
    report += f"• الهدف 2 (TP2): ${tp2:,.2f}\n\n"
    
    report += f"⚠️ <i>هذا ليس نصيحة مالية</i>"
    
    send_message(report)

def simple_report():
    """تقرير السوق البسيط"""
    try:
        report = "📊 <b>تقرير السوق</b>\n\n"
        report += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        
        symbols = ['BTC-USD', 'ETH-USD', 'AAPL', 'TSLA', 'NVDA']
        
        for symbol in symbols:
            data = analyze_symbol(symbol)
            if data:
                rsi_emoji = "🟢" if data['rsi'] < 70 else "🔴"
                report += f"{rsi_emoji} <b>{symbol}</b>: ${data['price']:,.2f} | RSI: {data['rsi']:.1f}\n"
            else:
                report += f"⚠️ <b>{symbol}</b>: لا بيانات\n"
        
        send_message(report)
        
    except Exception as e:
        send_message(f"❌ خطأ: {str(e)}")

def handle_command(cmd):
    cmd = cmd.strip()
    cmd_lower = cmd.lower()
    
    if cmd_lower == '/start':
        send_message("🤖 <b>بوت التداول الذكي</b>\n\nالأوامر:\n/test_report - تقرير السوق\n/smart <رمز> - تحليل شامل\n/help - المساعدة")
    
    elif cmd_lower == '/test_report':
        send_message("⏳ جاري إعداد التقرير...")
        simple_report()
    
    elif cmd_lower.startswith('/smart'):
        parts = cmd.split()
        if len(parts) < 2:
            send_message("الاستخدام: /smart <رمز>\nمثال: /smart BTC-USD")
            return
        
        symbol = parts[1].upper()
        send_message(f" جاري التحليل الشامل لـ {symbol}...")
        smart_analysis(symbol)
    
    elif cmd_lower == '/help':
        send_message("<b>الأوامر:</b>\n/test_report - تقرير السوق\n/smart <رمز> - تحليل شامل\n/help - المساعدة")
    
    else:
        send_message("استخدم /help للأوامر")

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
    logger.info("Starting bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
