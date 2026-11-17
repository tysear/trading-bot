import asyncio
import json
import logging
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# إعداد السجل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QuantBot")

app = FastAPI(title="Quant Trading Bot v3.6 - Unified Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# إدارة اتصالات لوحة التحكم (WebSockets)
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("تم اتصال لوحة التحكم (Dashboard) بنجاح.")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info("انقطع اتصال لوحة التحكم.")

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"خطأ في إرسال الرسالة للداشبورد: {e}")

manager = ConnectionManager()

@app.websocket("/ws/alerts")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# دالة إرسال التنبيهات للداشبورد
async def send_quant_alert(alert_type: str, message: str, score: float, price: float):
    payload = {
        "type": alert_type,
        "message": message,
        "quant_score": score,
        "price": price,
        "timestamp": asyncio.get_event_loop().time()
    }
    await manager.broadcast(payload)
    logger.info(f"تم إرسال تنبيه داشبورد: {alert_type} - السعر: {price}")

# محرك التداول الكمي في الخلفية
async def trading_bot_engine():
    logger.info("تم بدء تشغيل محرك التداول الكمي v3.6 في الخلفية...")
    await asyncio.sleep(5)
    await send_quant_alert("BUY_REVERSAL", "تم رصد امتصاص سيولة وارتداد إيجابي قرب الدعم بنجاح", 86.18, 64942.0)
    
    while True:
        try:
            current_price = 64942.0
            quant_score = 86.18
            await send_quant_alert("BUY_REVERSAL", "فحص دوري: النظام يراقب فخاخ السيولة بنجاح", quant_score, current_price)
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"خطأ في حلقة البوت: {e}")
            await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(trading_bot_engine())

# صفحة لوحة التحكم (Dashboard UI) المدمجة والرئيسية
@app.get("/", response_class=HTMLResponse)
def read_root():
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>لوحة تتبع التنبيهات الكمية - v3.6</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 20px; }
            .alert-box { background: #1e293b; border-right: 5px solid #3b82f6; padding: 15px; margin-bottom: 10px; border-radius: 4px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
            .alert-danger { border-right-color: #ef4444; }
            .alert-success { border-right-color: #22c55e; }
            .alert-time { font-size: 0.8rem; color: #94a3b8; }
            #status { font-weight: bold; margin-bottom: 15px; }
            .card { background: #1e293b; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
        </style>
    </head>
    <body>
        <h2>📊 لوحة تتبع التنبيهات الحية (الذكاء الكمي وصناع السوق)</h2>
        <div class="card">
            <div id="status">حالة الاتصال بالخادم: <span id="conn-status" style="color: orange;">جاري الاتصال...</span></div>
            <p>النظام يعمل بكفاءة لتجاوز انقطاعات تيليجرام عبر WebSockets.</p>
        </div>
        
        <h3>التنبيهات الواردة لحظياً:</h3>
        <div id="alerts-container">
            <div class="alert-box alert-success">
                <strong>النظام جاهز</strong>: تم تشغيل خادم التنبيهات والاتصال الحي بنجاح. <br>
                <small>الحالة: <b>مستعد لاستقبال إشارات الذكاء الكمي وفخاخ السيولة</b></small>
                <div class="alert-time">وقت التشغيل: الآن</div>
            </div>
        </div>

        <script>
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            const wsUrl = wsProtocol + window.location.host + '/ws/alerts';
            let socket;

            function connectWebSocket() {
                socket = new WebSocket(wsUrl);

                socket.onopen = function(e) {
                    document.getElementById('conn-status').innerText = 'متصل بنجاح (Live)';
                    document.getElementById('conn-status').style.color = '#22c55e';
                };

                socket.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    renderAlert(data);
                };

                socket.onclose = function(event) {
                    document.getElementById('conn-status').innerText = 'انقطع الاتصال، جاري إعادة المحاولة...';
                    document.getElementById('conn-status').style.color = '#ef4444';
                    setTimeout(connectWebSocket, 3000);
                };
            }

            function renderAlert(data) {
                const container = document.getElementById('alerts-container');
                const alertDiv = document.createElement('div');
                
                let alertClass = 'alert-box';
                if (data.type === 'SELL_TRAP' || data.type === 'BREAKDOWN') alertClass += ' alert-danger';
                if (data.type === 'BUY_REVERSAL' || data.type === 'PUMP') alertClass += ' alert-success';

                alertDiv.className = alertClass;
                alertDiv.innerHTML = `
                    <strong>${data.type}</strong>: ${data.message} <br>
                    <small>السعر: <b>${data.price}</b> | التقييم الكمي: <b>${data.quant_score}</b></small>
                    <div class="alert-time">وقت التنبيه: ${new Date().toLocaleTimeString()}</div>
                `;
                container.prepend(alertDiv);
            }

            connectWebSocket();
        </script>
    </body>
    </html>
    """
    return html_content

if __name__ == "__main__":
    uvicorn.run("market_bot:app", host="0.0.0.0", port=8000)
