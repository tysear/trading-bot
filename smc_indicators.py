import numpy as np
from scipy.signal import argrelextrema

def find_pivots(highs, lows, order=3):
    """إيجاد القمم والقيعان"""
    highs_arr = np.array(highs)
    lows_arr = np.array(lows)
    
    peaks = argrelextrema(highs_arr, np.greater_equal, order=order)[0]
    troughs = argrelextrema(lows_arr, np.less_equal, order=order)[0]
    
    return peaks, troughs

def detect_bos(closes, highs, lows, period=50):
    """كشف كسر الهيكل (Break of Structure)"""
    try:
        if len(closes) < period:
            return {'type': 'NONE', 'level': 0, 'strength': 0}
        
        h = highs[-period:]
        l = lows[-period:]
        c = closes[-period:]
        
        peaks, troughs = find_pivots(h, l, order=3)
        
        if len(peaks) < 2 or len(troughs) < 2:
            return {'type': 'NONE', 'level': 0, 'strength': 0}
        
        last_peak = h[peaks[-1]]
        prev_peak = h[peaks[-2]]
        last_trough = l[troughs[-1]]
        prev_trough = l[troughs[-2]]
        current = c[-1]
        
        # BOS صعودي: كسر قمة سابقة
        if current > prev_peak and current > last_peak:
            strength = min(1.0, (current - prev_peak) / prev_peak * 100)
            return {'type': 'BOS_BULLISH', 'level': prev_peak, 'strength': strength}
        
        # BOS هبوطي: كسر قاع سابق
        if current < prev_trough and current < last_trough:
            strength = min(1.0, (prev_trough - current) / prev_trough * 100)
            return {'type': 'BOS_BEARISH', 'level': prev_trough, 'strength': strength}
        
        return {'type': 'NONE', 'level': 0, 'strength': 0}
    except:
        return {'type': 'NONE', 'level': 0, 'strength': 0}

def detect_choch(closes, highs, lows, period=50):
    """كشف تغير الشخصية (Change of Character)"""
    try:
        if len(closes) < period:
            return {'type': 'NONE', 'level': 0, 'confidence': 0}
        
        # حساب الاتجاه السابق (آخر 70% من الفترة)
        trend_period = int(period * 0.7)
        prev_trend_closes = closes[-period:-trend_period]
        recent_closes = closes[-trend_period:]
        
        prev_trend = 'UP' if recent_closes[0] < prev_trend_closes[-1] else 'DOWN'
        current = closes[-1]
        prev_high = max(highs[-period:-trend_period])
        prev_low = min(lows[-period:-trend_period])
        
        # CHoCH صعودي: السعر كان هابطاً وكسر آخر قمة
        if prev_trend == 'DOWN' and current > prev_high:
            confidence = min(1.0, (current - prev_high) / prev_high * 50)
            return {'type': 'CHoCH_BULLISH', 'level': prev_high, 'confidence': confidence}
        
        # CHoCH هبوطي: السعر كان صاعداً وكسر آخر قاع
        if prev_trend == 'UP' and current < prev_low:
            confidence = min(1.0, (prev_low - current) / prev_low * 50)
            return {'type': 'CHoCH_BEARISH', 'level': prev_low, 'confidence': confidence}
        
        return {'type': 'NONE', 'level': 0, 'confidence': 0}
    except:
        return {'type': 'NONE', 'level': 0, 'confidence': 0}

def detect_order_blocks(closes, highs, lows, volumes, period=50):
    """كشف مناطق Order Blocks"""
    try:
        if len(closes) < period:
            return {'bullish': [], 'bearish': []}
        
        h = highs[-period:]
        l = lows[-period:]
        c = closes[-period:]
        v = volumes[-period:] if len(volumes) >= period else [1] * period
        
        peaks, troughs = find_pivots(h, l, order=2)
        
        bullish_obs = []
        bearish_obs = []
        
        # Order Block صعودي: آخر شمعة هبوطية قبل حركة صعودية قوية
        for i in range(1, len(c) - 5):
            if c[i] < c[i-1] and c[i+1] > c[i] and c[i+5] > c[i] * 1.02:
                if v[i] > np.mean(v) * 1.5:  # حجم عالي
                    bullish_obs.append({
                        'top': h[i],
                        'bottom': l[i],
                        'strength': v[i] / np.mean(v)
                    })
        
        # Order Block هبوطي: آخر شمعة صعودية قبل حركة هبوطية قوية
        for i in range(1, len(c) - 5):
            if c[i] > c[i-1] and c[i+1] < c[i] and c[i+5] < c[i] * 0.98:
                if v[i] > np.mean(v) * 1.5:
                    bearish_obs.append({
                        'top': h[i],
                        'bottom': l[i],
                        'strength': v[i] / np.mean(v)
                    })
        
        return {
            'bullish': bullish_obs[-3:],  # آخر 3 مناطق
            'bearish': bearish_obs[-3:]
        }
    except:
        return {'bullish': [], 'bearish': []}

def detect_fvg(closes, highs, lows, period=50):
    """كشف فجوات القيمة العادلة (Fair Value Gaps)"""
    try:
        if len(closes) < period:
            return {'bullish': [], 'bearish': []}
        
        h = highs[-period:]
        l = lows[-period:]
        
        bullish_fvgs = []
        bearish_fvgs = []
        
        for i in range(2, len(closes[-period:])):
            # FVG صعودي: فجوة بين شمعة i-2 و i
            if l[i] > h[i-2]:
                gap_size = (l[i] - h[i-2]) / h[i-2] * 100
                bullish_fvgs.append({
                    'top': l[i],
                    'bottom': h[i-2],
                    'size': gap_size
                })
            
            # FVG هبوطي
            if h[i] < l[i-2]:
                gap_size = (l[i-2] - h[i]) / l[i-2] * 100
                bearish_fvgs.append({
                    'top': l[i-2],
                    'bottom': h[i],
                    'size': gap_size
                })
        
        return {
            'bullish': bullish_fvgs[-3:],
            'bearish': bearish_fvgs[-3:]
        }
    except:
        return {'bullish': [], 'bearish': []}

def detect_liquidity_sweep(highs, lows, closes, period=50):
    """كشف سحب السيولة"""
    try:
        if len(closes) < period:
            return {'swept': False, 'type': 'NONE'}
        
        h = highs[-period:]
        l = lows[-period:]
        c = closes[-period:]
        
        peaks, troughs = find_pivots(h, l, order=3)
        
        if len(peaks) == 0 or len(troughs) == 0:
            return {'swept': False, 'type': 'NONE'}
        
        recent_high = h[peaks[-1]]
        recent_low = l[troughs[-1]]
        current = c[-1]
        prev_close = c[-2]
        
        # Sweep صعودي: السعر اخترق قاع ثم ارتد
        if current < recent_low and prev_close >= recent_low:
            return {'swept': True, 'type': 'BULLISH_SWEEP', 'level': recent_low}
        
        # Sweep هبوطي: السعر اخترق قمة ثم انخفض
        if current > recent_high and prev_close <= recent_high:
            return {'swept': True, 'type': 'BEARISH_SWEEP', 'level': recent_high}
        
        return {'swept': False, 'type': 'NONE'}
    except:
        return {'swept': False, 'type': 'NONE'}

def calculate_premium_discount(highs, lows, closes, period=50):
    """حساب مناطق القسط والخصم"""
    try:
        if len(closes) < period:
            return {'zone': 'EQUILIBRIUM', 'percentage': 50}
        
        h = max(highs[-period:])
        l = min(lows[-period:])
        current = closes[-1]
        
        range_size = h - l
        if range_size == 0:
            return {'zone': 'EQUILIBRIUM', 'percentage': 50}
        
        # 0% = القاع (Discount أقصى)، 100% = القمة (Premium أقصى)
        percentage = (current - l) / range_size * 100
        
        if percentage > 75:
            zone = 'PREMIUM'  # منطقة غالية - للبيع
        elif percentage > 50:
            zone = 'HIGHER_PREMIUM'
        elif percentage > 25:
            zone = 'LOWER_DISCOUNT'
        else:
            zone = 'DISCOUNT'  # منطقة رخيصة - للشراء
        
        return {'zone': zone, 'percentage': percentage, 'equilibrium': (h + l) / 2}
    except:
        return {'zone': 'EQUILIBRIUM', 'percentage': 50}

def get_smc_analysis(closes, highs, lows, volumes):
    """تحليل SMC شامل"""
    bos = detect_bos(closes, highs, lows)
    choch = detect_choch(closes, highs, lows)
    obs = detect_order_blocks(closes, highs, lows, volumes)
    fvgs = detect_fvg(closes, highs, lows)
    liquidity = detect_liquidity_sweep(highs, lows, closes)
    pd = calculate_premium_discount(highs, lows, closes)
    
    # حساب نقاط SMC
    score = 0
    signals = []
    
    # BOS
    if bos['type'] == 'BOS_BULLISH':
        score += 3
        signals.append(f"BOS صعودي عند {bos['level']:.3f} (قوة {bos['strength']*100:.0f}%)")
    elif bos['type'] == 'BOS_BEARISH':
        score -= 3
        signals.append(f"BOS هبوطي عند {bos['level']:.3f} (قوة {bos['strength']*100:.0f}%)")
    
    # CHoCH
    if choch['type'] == 'CHoCH_BULLISH':
        score += 4
        signals.append(f"CHoCH صعودي عند {choch['level']:.3f} (ثقة {choch['confidence']*100:.0f}%)")
    elif choch['type'] == 'CHoCH_BEARISH':
        score -= 4
        signals.append(f"CHoCH هبوطي عند {choch['level']:.3f} (ثقة {choch['confidence']*100:.0f}%)")
    
    # Order Blocks
    current_price = closes[-1]
    near_bullish_ob = any(ob['bottom'] <= current_price <= ob['top'] * 1.01 for ob in obs['bullish'])
    near_bearish_ob = any(ob['bottom'] <= current_price <= ob['top'] * 1.01 for ob in obs['bearish'])
    
    if near_bullish_ob:
        score += 2
        signals.append("السعر عند Order Block صعودي")
    elif near_bearish_ob:
        score -= 2
        signals.append("السعر عند Order Block هبوطي")
    
    # FVG
    near_bullish_fvg = any(fvg['bottom'] <= current_price <= fvg['top'] for fvg in fvgs['bullish'])
    near_bearish_fvg = any(fvg['bottom'] <= current_price <= fvg['top'] for fvg in fvgs['bearish'])
    
    if near_bullish_fvg:
        score += 1
        signals.append("السعر يملأ FVG صعودي")
    elif near_bearish_fvg:
        score -= 1
        signals.append("السعر يملأ FVG هبوطي")
    
    # Liquidity Sweep
    if liquidity['swept']:
        if liquidity['type'] == 'BULLISH_SWEEP':
            score += 3
            signals.append(f"سحب سيولة صعودي عند {liquidity['level']:.3f}")
        elif liquidity['type'] == 'BEARISH_SWEEP':
            score -= 3
            signals.append(f"سحب سيولة هبوطي عند {liquidity['level']:.3f}")
    
    # Premium/Discount
    if pd['zone'] == 'DISCOUNT':
        score += 2
        signals.append(f"منطقة خصم ({pd['percentage']:.0f}%) - مثالية للشراء")
    elif pd['zone'] == 'PREMIUM':
        score -= 2
        signals.append(f"منطقة قسط ({pd['percentage']:.0f}%) - مثالية للبيع")
    
    return {
        'score': score,
        'signals': signals,
        'bos': bos,
        'choch': choch,
        'order_blocks': obs,
        'fvgs': fvgs,
        'liquidity': liquidity,
        'premium_discount': pd
    }
