def ask_gemini(question, context=""):
    """إرسال سؤال إلى Groq AI (مجاني وسريع)"""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        return "⚠️ الذكاء الاصطناعي غير مفعّل. أضف GROQ_API_KEY في Railway."
    
    try:
        prompt = f"""أنت مساعد تداول ذكي محترف. أجب باللغة العربية بشكل واضح ومختصر.

{context}

سؤال المستخدم: {question}

️ مهم: اذكر دائماً أن هذا ليس نصيحة مالية، وأن التداول ينطوي على مخاطر."""
        
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1024
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()
        
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        else:
            error_msg = data.get('error', {}).get('message', 'غير معروف')
            return f"❌ خطأ: {error_msg}"
            
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return f" حدث خطأ: {str(e)}"
