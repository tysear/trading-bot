import requests
import time
import yfinance as yf
from datetime import datetime
import logging
import os
from flask import Flask
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

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
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        logger.error(f"Send error: {e}")

def simple_report():
    """تقرير بسيط جداً - يعمل 100%"""
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
                    report += f"✅ <b>{symbol}</b>: ${price:.2f}\n"
                else:
                    report += f"⚠️ <b>{symbol}</b>: لا بيانات\n"
                    
            except Exception as e:
                report += f"❌ <b>{symbol}</b>: خطأ\n"
        
        send_message(report)
        logger.info("Report sent!")
        
    except Exception as e:
        send_message(f"خطأ: {str(e)}")

def handle_command(cmd):
    cmd = cmd.strip().lower()
    
    if cmd == '/start':
        send_message("🤖 <b>بوت التداول</b>\n\n/test_report - تقرير\n/smart <رمز> - تحليل\n/help - مساعدة")
    
    elif cmd == '/test_report':
        send_message("⏳ جاري...")
        simple_report()
    
    elif cmd.startswith('/smart'):
        parts = cmd.split()
        if len(parts) < 2:
            send_message("استخدم: /smart <رمز>")
            return
        
        symbol = parts[1].upper()
        send_message(f"🔍 تحليل {symbol}...")
        
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")
            
            if hist.empty:
                send_message(f"لا بيانات لـ {symbol}")
                return
            
            price = hist['Close'].iloc[-1]
            high = hist['High'].max()
            low = hist['Low'].min()
            
            report = f" <b>{symbol}</b>\n\n"
            report += f"السعر: ${price:.2f}\n"
            report += f"أعلى: ${high:.2f}\n"
            report += f"أدنى: ${low:.2f}\n"
            
            send_message(report)
            
        except Exception as e:
            send_message(f"❌ خطأ: {str(e)}")
    
    elif cmd == '/help':
        send_message("<b>الأوامر:</b>\n/test_report\n/smart <رمز>\n/help")
    
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
    logger.info("Starting bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    listen()
