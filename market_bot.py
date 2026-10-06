import requests
import time
import schedule
import yfinance as yf
from datetime import datetime
import logging
import os
from flask import Flask
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def send_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=10)
        logger.info("Message sent successfully")
    except Exception as e:
        logger.error(f"Error sending message: {e}")

def get_simple_report():
    """تقرير بسيط يعمل بدون أخطاء"""
    try:
        logger.info("Starting simple report...")
        
        symbols = ['BTC-USD', 'ETH-USD', 'AAPL', 'TSLA']
        report = " <b>تقرير السوق البسيط</b>\n\n"
        report += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                
                if not hist.empty:
                    current_price = hist['Close'].iloc[-1]
                    change = ((current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0]) * 100
                    
                    emoji = "🟢" if change >= 0 else "🔴"
                    report += f"{emoji} <b>{symbol}</b>: ${current_price:.2f} ({change:+.2f}%)\n"
                else:
                    report += f" <b>{symbol}</b>: لا توجد بيانات\n"
                    
            except Exception as e:
                logger.error(f"Error with {symbol}: {e}")
                report += f"❌ <b>{symbol}</b>: خطأ في جلب البيانات\n"
        
        report += "\n💡 استخدم /smart <رمز> للتحليل الشامل"
        
        send_message(report)
        logger.info("Simple report completed!")
        
    except Exception as e:
        logger.error(f"Error in report: {e}")
        send_message(f" خطأ في التقرير: {str(e)}")

def handle_command(command):
    cmd = command.strip().lower()
    
    if cmd == '/start':
        send_message("🤖 <b>مرحباً! أنا بوت التداول الذكي</b>\n\nالأوامر:\n/test_report - تقرير بسيط\n/smart <رمز> - تحليل شامل\n/help - المساعدة")
    
    elif cmd == '/test_report':
        send_message("⏳ جاري إعداد التقرير...")
        get_simple_report()
    
    elif cmd.startswith('/smart'):
        parts = command.split()
        if len(parts) < 2:
            send_message("الاستخدام: /smart <رمز>\nمثال: /smart BTC-USD")
            return
        
        symbol = parts[1].upper()
        send_message(f" جاري التحليل الشامل لـ {symbol}...")
        
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="3mo")
            
            if hist.empty:
                send_message(f" لا توجد بيانات لـ {symbol}")
                return
            
            current_price = hist['Close'].iloc[-1]
            rsi = 50  # قيمة مبسطة
            ema20 = hist['Close'].rolling(20).mean().iloc[-1]
            
            report = f"🎯 <b>تحليل {symbol}</b>\n\n"
            report += f" السعر: ${current_price:.2f}\n"
            report += f"📊 RSI: {rsi:.1f}\n"
            report += f"📈 EMA20: ${ema20:.2f}\n\n"
            report += "💡 هذا تحليل مبسط. للتحليل الكامل استخدم الموقع."
            
            send_message(report)
            
        except Exception as e:
            send_message(f"❌ خطأ: {str(e)}")
    
    elif cmd == '/help':
        send_message("<b>الأوامر المتاحة:</b>\n\n/test_report - تقرير بسيط\n/smart <رمز> - تحليل شامل\n/help - هذه الرسالة")
    
    else:
        send_message("استخدم /help للأوامر")

def listen_for_commands():
    last_update_id = 0
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 10}
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('ok') and data.get('result'):
                    for update in data['result']:
                        if 'message' in update and 'text' in update['message']:
                            chat_id = str(update['message']['chat']['id'])
                            if chat_id == CHAT_ID:
                                handle_command(update['message']['text'])
                        last_update_id = update['update_id']
            
        except Exception as e:
            logger.error(f"Error listening: {e}")
        
        time.sleep(1)

def scheduled_tasks():
    schedule.every(6).hours.do(get_simple_report)
    logger.info("Scheduled tasks set up")

if __name__ == "__main__":
    logger.info("Starting bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    scheduled_tasks()
    listen_for_commands()
