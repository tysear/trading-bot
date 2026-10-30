from PIL import Image, ImageDraw, ImageFont
import io
import os

def get_font(size):
    """تحميل خط - يحاول عدة مسارات"""
    font_paths = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except:
                continue
    return ImageFont.load_default()

def generate_signal_image(data):
    """إنشاء صورة احترافية للإشارة"""
    rec = data.get('recommendation', {})
    trade_type = rec.get('trade_type', 'WAIT')
    score = rec.get('score', 0)
    
    # تحديد الألوان حسب التوصية
    if trade_type == 'LONG':
        bg_color = (10, 40, 20)
        border_color = (0, 200, 100)
        text_color = (0, 255, 120)
        signal_text = "STRONG BUY"
        arabic_text = "إشارة شراء قاطعة"
        icon = "BUY"
    elif trade_type == 'SHORT':
        bg_color = (40, 10, 10)
        border_color = (200, 50, 50)
        text_color = (255, 80, 80)
        signal_text = "STRONG SELL"
        arabic_text = "إشارة بيع قاطعة"
        icon = "SELL"
    else:
        bg_color = (40, 35, 10)
        border_color = (200, 180, 50)
        text_color = (255, 220, 80)
        signal_text = "WAIT"
        arabic_text = "انتظار"
        icon = "WAIT"
    
    # إنشاء الصورة
    width, height = 800, 900
    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    
    # الحدود
    draw.rectangle([10, 10, width-10, height-10], outline=border_color, width=4)
    
    # الخطوط
    font_title = get_font(42)
    font_subtitle = get_font(28)
    font_label = get_font(22)
    font_value = get_font(32)
    font_small = get_font(18)
    
    # العنوان
    y = 40
    draw.text((40, y), f"[{icon}] {signal_text}", fill=text_color, font=font_title)
    y += 60
    draw.text((40, y), arabic_text, fill=text_color, font=font_subtitle)
    
    # نسبة الثقة
    y += 50
    confidence = rec.get('confidence', 'غير محدد')
    draw.text((40, y), f"Confidence: {confidence}", fill=(255, 255, 255), font=font_label)
    
    # خط فاصل
    y += 40
    draw.line([(40, y), (width-40, y)], fill=border_color, width=2)
    
    # معلومات الرمز والسعر
    y += 30
    symbol = data.get('symbol', 'UNKNOWN')
    price = data.get('price', 0)
    draw.text((40, y), f"Symbol: {symbol}", fill=(200, 200, 200), font=font_label)
    y += 35
    draw.text((40, y), f"Current Price: ${price:.2f}", fill=(255, 255, 255), font=font_value)
    
    # خط فاصل
    y += 50
    draw.line([(40, y), (width-40, y)], fill=border_color, width=2)
    
    # المستويات
    y += 30
    levels = [
        ("Entry Price:", f"${price:.2f}", (255, 220, 100)),
        ("Stop Loss (SL):", f"${data.get('sl', 0):.2f}", (255, 100, 100)),
        ("Target 1 (TP1):", f"${data.get('tp1', 0):.2f}", (100, 255, 150)),
        ("Target 2 (TP2):", f"${data.get('tp2', 0):.2f}", (100, 255, 150)),
        ("Target 3 (TP3):", f"${data.get('tp3', 0):.2f}", (100, 255, 150)),
    ]
    
    for label, value, color in levels:
        draw.text((40, y), label, fill=(200, 200, 200), font=font_label)
        y += 30
        draw.text((40, y), value, fill=color, font=font_value)
        y += 45
    
    # خط فاصل
    draw.line([(40, y), (width-40, y)], fill=border_color, width=2)
    
    # R:R
    y += 20
    rr = data.get('rr', 0)
    draw.text((40, y), "Risk/Reward (R:R)", fill=(200, 200, 200), font=font_label)
    y += 30
    draw.text((40, y), f"1 : {rr:.2f}", fill=(255, 220, 100), font=font_value)
    
    # النقاط
    y += 50
    draw.text((40, y), f"Score: {score}", fill=(255, 255, 255), font=font_label)
    
    # التوقيع
    y = height - 60
    draw.text((40, y), "Quant Trading Bot - SMC System", fill=(150, 150, 150), font=font_small)
    
    # حفظ الصورة
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG', quality=95)
    img_byte_arr.seek(0)
    
    return img_byte_arr
