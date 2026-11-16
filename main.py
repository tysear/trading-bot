import asyncio
import json
import logging
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# إعداد السجل (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QuantBot")

app = FastAPI(title="Quant Trading Bot v3.6 - Unified Server")

# السماح بالاتصالات الخارجية للداشبورد
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
            # الحفاظ على الاتصال نشطاً
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
    while True:
        try:
            current_price = 64942.0
            quant_score = 86.18
            
            # يمكنك تفعيل إرسال التنبيه التجريبي أو ربطه بالمعادلات الحقيقية هنا
            # await send_quant_alert("BUY_REVERSAL", "تم رصد امتصاص سيولة وارتداد إيجابي قرب الدعم", quant_score, current_price)
            
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"خطأ في حلقة البوت: {e}")
            await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(trading_bot_engine())

@app.get("/")
def read_root():
    return {"status": "Quant Trading Bot v3.6 is running", "websocket_endpoint": "/ws/alerts"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
