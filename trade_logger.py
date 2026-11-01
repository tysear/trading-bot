import json
import os
from datetime import datetime

TRADE_LOG_FILE = 'trade_log.json'
PERFORMANCE_FILE = 'performance.json'

def log_trade(symbol, action, price, score, tp1, tp2, tp3, sl):
    """تسجيل التوصية في السجل"""
    trade = {
        'timestamp': datetime.now().isoformat(),
        'symbol': symbol,
        'action': action,
        'price': price,
        'score': score,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'sl': sl,
        'status': 'pending'
    }
    
    try:
        trades = load_trades()
        trades.append(trade)
        # الاحتفاظ بآخر 100 توصية فقط
        if len(trades) > 100:
            trades = trades[-100:]
        save_trades(trades)
        return True
    except Exception as e:
        print(f"❌ خطأ في تسجيل التوصية: {e}")
        return False

def load_trades():
    """تحميل السجل"""
    try:
        if os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return []

def save_trades(trades):
    """حفظ السجل"""
    try:
        with open(TRADE_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ خطأ في حفظ السجل: {e}")

def get_performance():
    """حساب إحصائيات الأداء"""
    trades = load_trades()
    if not trades:
        return {'total': 0, 'win_rate': 0, 'avg_profit': 0}
    
    completed = [t for t in trades if t.get('status') in ['won', 'lost']]
    total = len(completed)
    
    if total == 0:
        return {'total': len(trades), 'pending': len(trades), 'win_rate': 0}
    
    wins = len([t for t in completed if t.get('status') == 'won'])
    win_rate = (wins / total * 100) if total > 0 else 0
    
    return {
        'total': len(trades),
        'completed': total,
        'wins': wins,
        'losses': total - wins,
        'win_rate': round(win_rate, 1),
        'pending': len(trades) - total
    }

def get_recent_trades(limit=10):
    """آخر التوصيات"""
    trades = load_trades()
    return trades[-limit:] if trades else []

def update_trade_status(symbol, status):
    """تحديث حالة التوصية"""
    trades = load_trades()
    for trade in reversed(trades):
        if trade['symbol'] == symbol and trade['status'] == 'pending':
            trade['status'] = status
            trade['closed_at'] = datetime.now().isoformat()
            break
    save_trades(trades)
