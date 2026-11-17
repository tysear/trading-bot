import os
import time
import requests
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# ==========================================
# إعدادات واجهة برمجة التطبيقات (API Settings)
# ==========================================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# ==========================================
# دالة تحليل الذكاء الاصطناعي (Qwen / Free Model)
# ==========================================
def get_ai_market_analysis():
    if not OPENROUTER_API_KEY:
        return "الذكاء الاصطناعي غير متاحة؛ يرجى التحقق من إضافة المفتاح وعمل Redeploy."
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://railway.app",
        "X-Title": "Trading Dashboard",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": OPENROUTER_MODEL if OPENROUTER_MODEL else "openrouter/free",
        "messages": [
            {
                "role": "system",
                "content": "أنت خبير في التداول الكمي وهندسة السوق (Smart Money). قدم تحليلاً مختصراً وقوياً للسيولة واتجاه الأسواق بناءً على المؤشرات الحالية."
            },
            {
                "role": "user",
                "content": "قم بتحليل وضع السوق الحالي (BTC, ETH, SOL, XRP, Gold) من حيث السيولة وصناع السوق وإعطاء إشارة واضحة."
            }
        ]
    }
    
    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        elif response.status_code == 402:
            return "خطأ من مزود الذكاء الاصطناعي (رمز 402) - تأكد من صلاحية المفتاح والرصيد أو استخدام openrouter/free."
        else:
            return f"تعذر جلب التحليل (رمز الاستجابة: {response.status_code})"
    except Exception as e:
        return f"حدث خطأ أثناء الاتصال بالذكاء الاصطناعي: {str(e)}"

# ==========================================
# مسارات السيرفر (Routes)
# ==========================================
@app.route("/")
def index():
    return "Trading Dashboard is Running Successfully!"

@app.route("/api/ai-analysis")
def ai_analysis_endpoint():
    analysis = get_ai_market_analysis()
    return jsonify({"analysis": analysis})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
