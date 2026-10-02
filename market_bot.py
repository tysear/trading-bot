def ask_qwen(question, context=""):
    """الرد عبر Qwen 2.5 72B (نموذج قوي جداً من Alibaba)"""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        return "❌ مفتاح OpenRouter غير موجود"
    
    try:
        prompt = f"""أنت محلل مالي خبير متخصص في الأسواق المالية والتداول الكمي.
أجب باللغة العربية بشكل مفصل ودقيق.

{context}

سؤال المستخدم: {question}

قواعد الإجابة:
1. اعتمد على البيانات والأرقام.
2. استخدم المعادلات الرياضية عند الحاجة.
3. اذكر المخاطر بوضوح.
4. اختم بتحذير أن هذا ليس نصيحة مالية."""
        
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/tysear/trading-bot",
            "X-Title": "Trading Bot - Qwen"
        }
        payload = {
            "model": "qwen/qwen-2.5-72b-instruct",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 4096
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        data = response.json()
        
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        else:
            error_msg = data.get('error', {}).get('message', 'غير معروف')
            return f"❌ خطأ Qwen: {error_msg}"
    except Exception as e:
        logger.error(f"Qwen error: {e}")
        return f"❌ حدث خطأ في Qwen: {str(e)}"

def ask_ai(question, context="", model_preference="auto"):
    """إرسال سؤال إلى الذكاء الاصطناعي مع دعم 4 نماذج"""
    
    if model_preference == "auto":
        if len(question) < 100:
            model = "groq"  # سريع للأسئلة البسيطة
        else:
            model = "claude"  # دقيق للأسئلة المعقدة
    else:
        model = model_preference
    
    # Claude (الأذكى)
    if model == "claude":
        try:
            answer = ask_claude(question, context)
            if not answer.startswith("❌"):
                return answer
        except Exception as e:
            logger.error(f"Claude failed: {e}")
    
    # Qwen (قوي جداً ومتوازن)
    if model == "qwen":
        try:
            answer = ask_qwen(question, context)
            if not answer.startswith("❌"):
                return answer
        except Exception as e:
            logger.error(f"Qwen failed: {e}")
    
    # Groq (الأسرع)
    if model in ["groq", "auto"]:
        try:
            answer = ask_groq(question, context)
            if not answer.startswith("❌"):
                return answer
        except Exception as e:
            logger.error(f"Groq failed: {e}")
    
    # OpenAI (احتياطي)
    if model in ["openai", "auto"]:
        try:
            answer = ask_openai(question, context)
            if not answer.startswith("❌"):
                return answer
        except Exception as e:
            logger.error(f"OpenAI failed: {e}")
    
    return "❌ عذراً، لم أتمكن من الحصول على إجابة."
