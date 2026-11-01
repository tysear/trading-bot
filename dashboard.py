from flask import Blueprint, jsonify, render_template_string
from trade_logger import get_performance, get_recent_trades

dashboard_bp = Blueprint('dashboard', __name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, sans-serif;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 100%);
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { 
            text-align: center; 
            padding: 30px; 
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            margin-bottom: 30px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .header h1 { 
            font-size: 2.5em; 
            background: linear-gradient(45deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: rgba(255,255,255,0.05);
            padding: 25px;
            border-radius: 15px;
            border: 1px solid rgba(255,255,255,0.1);
            transition: transform 0.3s;
        }
        .stat-card:hover { transform: translateY(-5px); }
        .stat-label { color: #888; font-size: 0.9em; margin-bottom: 10px; }
        .stat-value { font-size: 2em; font-weight: bold; }
        .stat-value.green { color: #00ff88; }
        .stat-value.blue { color: #00d4ff; }
        .stat-value.orange { color: #ff9500; }
        .stat-value.purple { color: #7b2ff7; }
        .trades-section {
            background: rgba(255,255,255,0.05);
            padding: 25px;
            border-radius: 15px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .trades-section h2 { 
            margin-bottom: 20px; 
            color: #00d4ff;
        }
        .trade-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            margin-bottom: 10px;
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
            border-right: 4px solid #00d4ff;
        }
        .trade-item.won { border-right-color: #00ff88; }
        .trade-item.lost { border-right-color: #ff4444; }
        .trade-item.pending { border-right-color: #ff9500; }
        .trade-symbol { font-weight: bold; font-size: 1.1em; }
        .trade-action { 
            padding: 5px 12px; 
            border-radius: 5px; 
            font-size: 0.85em;
        }
        .action-buy { background: rgba(0,255,136,0.2); color: #00ff88; }
        .action-sell { background: rgba(255,68,68,0.2); color: #ff4444; }
        .action-wait { background: rgba(255,149,0,0.2); color: #ff9500; }
        .trade-price { color: #888; font-size: 0.9em; }
        .refresh-btn {
            background: linear-gradient(45deg, #00d4ff, #7b2ff7);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 1em;
            margin: 20px 0;
        }
        .refresh-btn:hover { opacity: 0.9; }
        .empty-state { text-align: center; padding: 40px; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🧮 Quant Trading Bot</h1>
            <p>لوحة التحكم الاحترافية - SMC Edition</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">إجمالي التوصيات</div>
                <div class="stat-value blue">{{ stats.total }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">معدل النجاح</div>
                <div class="stat-value green">{{ stats.win_rate }}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">الرابحة</div>
                <div class="stat-value green">{{ stats.wins }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">قيد الانتظار</div>
                <div class="stat-value orange">{{ stats.pending }}</div>
            </div>
        </div>
        
        <div class="trades-section">
            <h2>📋 آخر التوصيات</h2>
            {% if trades %}
                {% for trade in trades %}
                <div class="trade-item {{ trade.status }}">
                    <div>
                        <div class="trade-symbol">{{ trade.symbol }}</div>
                        <div class="trade-price">{{ trade.timestamp[:16] }}</div>
                    </div>
                    <div>
                        <span class="trade-action action-{{ 'buy' if 'شراء' in trade.action else 'sell' if 'بيع' in trade.action else 'wait' }}">
                            {{ trade.action }}
                        </span>
                    </div>
                    <div class="trade-price">${{ "%.2f"|format(trade.price) }}</div>
                    <div class="trade-price">Score: {{ trade.score }}</div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <p>لا توجد توصيات مسجلة بعد</p>
                    <p>استخدم /smart في Telegram لبدء التحليل</p>
                </div>
            {% endif %}
        </div>
        
        <div style="text-align: center;">
            <button class="refresh-btn" onclick="location.reload()">🔄 تحديث</button>
        </div>
    </div>
</body>
</html>
"""

@dashboard_bp.route('/dashboard')
def dashboard():
    stats = get_performance()
    trades = get_recent_trades(20)
    return render_template_string(DASHBOARD_HTML, stats=stats, trades=trades)

@dashboard_bp.route('/api/stats')
def api_stats():
    return jsonify(get_performance())

@dashboard_bp.route('/api/trades')
def api_trades():
    return jsonify(get_recent_trades(50))
