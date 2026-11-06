import numpy as np
import pandas as pd
import logging
import os
import requests
import threading
import time
from io import BytesIO
from flask import Flask, jsonify, render_template_string, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from signal_image import generate_signal_image
except ImportError:
    generate_signal_image = None

try:
    from smc_indicators import get_smc_analysis
except ImportError:
    get_smc_analysis = None

try:
    from trade_logger import log_trade, get_performance
except ImportError:
    log_trade = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")

CRYPTO_SYMBOLS = ['BTC-USD', 'ETH-USD', 'XRP-USD', 'BNB-USD', 'SOL-USD', 'ADA-USD', 'DOGE-USD']

_data_cache = {}
CACHE_DURATION = 3600

def get_cached_data(key):
    if key in _data_cache:
        data, timestamp = _data_cache[key]
        if time.time() - timestamp < CACHE_DURATION:
            return data
    return None

def set_cached_data(key, data):
    _data_cache[key] = (data, time.time())

def fetch_from_binance(symbol, interval="4h"):
    try:
        binance_symbol = symbol.replace('-USD', 'USDT')
        interval_map = {'15m': '15m', '1h': '1h', '4h': '4h', '1d': '1d'}
        binance_interval = interval_map.get(interval, '4h')
        
        url = f"https://api.binance.com/api/v3/klines"
        params = {"symbol": binance_symbol, "interval": binance_interval, "limit": 200}
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        if not data or len(data) < 20:
            return None
        
        closes = [float(k[4]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        volumes = [float(k[5]) for k in data]
        
        return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}
    except Exception as e:
        logger.error(f"Binance error for {symbol}: {e}")
        return None

def fetch_from_twelvedata(symbol, interval="4h"):
    try:
        if not TWELVE_DATA_API_KEY:
            return None
        
        td_symbol = symbol.replace('-USD', '/USD')
        interval_map = {'15m': '15min', '1h': '1h', '4h': '4h', '1d': '1day'}
        td_interval = interval_map.get(interval, '4h')
        
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": td_symbol,
            "interval": td_interval,
            "outputsize": 200,
            "apikey": TWELVE_DATA_API_KEY,
            "format": "JSON"
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        if "status" in data and data["status"] == "error":
            return None
        
        values = data.get("values", [])
        if not values or len(values) < 20:
            return None
        
        values.reverse()
        
        closes, highs, lows, volumes = [], [], [], []
        for v in values:
            try:
                closes.append(float(v["close"]))
                highs.append(float(v["high"]))
                lows.append(float(v["low"]))
                volumes.append(float(v.get("volume", 0)))
            except:
                continue
        
        if len(closes) < 20:
            return None
        
        return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}
    except Exception as e:
        logger.error(f"Twelve Data error for {symbol}: {e}")
        return None

def fetch_from_finnhub(symbol, interval="1d"):
    try:
        if not FINNHUB_API_KEY:
            return None
        
        resolution_map = {'1d': 'D', '1wk': 'W', '1mo': 'M'}
        resolution = resolution_map.get(interval, 'D')
        
        end_time = int(time.time())
        start_time = end_time - (365 * 24 * 3600)
        
        url = f"https://finnhub.io/api/v1/stock/candle"
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": start_time,
            "to": end_time,
            "token": FINNHUB_API_KEY
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        if data.get("s") != "ok":
            return None
        
        closes = data.get("c", [])
        highs = data.get("h", [])
        lows = data.get("l", [])
        volumes = data.get("v", [])
        
        if len(closes) < 20:
            return None
        
        return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}
    except Exception as e:
        logger.error(f"Finnhub error for {symbol}: {e}")
        return None

def fetch_from_yfinance(symbol, interval="4h"):
    try:
        if yf is None:
            return None
        
        period = "7d" if interval in ['1m', '5m'] else "1mo" if interval in ['15m', '30m'] else "6mo" if interval == '4h' else "1y"
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        
        if hist.empty or len(hist) < 20:
            return None
        
        closes = hist['Close'].tolist()
        highs = hist['High'].tolist()
        lows = hist['Low'].tolist()
        volumes = hist['Volume'].tolist()
        
        return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}
    except Exception as e:
        logger.error(f"yfinance error for {symbol}: {e}")
        return None

def fetch_data(symbol, interval="4h"):
    cache_key = f"{symbol}_{interval}"
    cached = get_cached_data(cache_key)
    if cached:
        logger.info(f"✅ Using cached data for {symbol}")
        return cached
    
    data = None
    
    if symbol in CRYPTO_SYMBOLS:
        logger.info(f"🔄 Trying Binance for {symbol}")
        data = fetch_from_binance(symbol, interval)
    
    if data is None and TWELVE_DATA_API_KEY:
        logger.info(f"🔄 Trying Twelve Data for {symbol}")
        data = fetch_from_twelvedata(symbol, interval)
    
    if data is None and FINNHUB_API_KEY and symbol not in CRYPTO_SYMBOLS:
        logger.info(f"🔄 Trying Finnhub for {symbol}")
        data = fetch_from_finnhub(symbol, interval)
    
    if data is None:
        logger.info(f"🔄 Trying yfinance fallback for {symbol}")
        data = fetch_from_yfinance(symbol, interval)
    
    if data:
        set_cached_data(cache_key, data)
        logger.info(f"✅ Data fetched for {symbol} ({len(data['closes'])} candles)")
    else:
        logger.error(f"❌ ALL sources failed for {symbol}")
    
    return data

def advanced_differential_analysis(closes):
    try:
        if not closes or len(closes) < 10:
            return None
        prices = np.array(closes[-20:], dtype=float)
        velocity = np.diff(prices)
        current_velocity = float(velocity[-1])
        acceleration = float(np.diff(velocity)[-1]) if len(velocity) >= 2 else 0.0
        
        if current_velocity > 0 and acceleration > 0:
            trend, trend_score = "صاعد متسارع", 2
        elif current_velocity > 0:
            trend, trend_score = "صاعد متباطئ", 1
        elif current_velocity < 0 and acceleration < 0:
            trend, trend_score = "هابط متسارع", -2
        elif current_velocity < 0:
            trend, trend_score = "هابط متباطئ", -1
        else:
            trend, trend_score = "محايد", 0
        
        return {'velocity': current_velocity, 'acceleration': acceleration, 'trend': trend, 'trend_score': trend_score}
    except Exception as e:
        logger.error(f"Differential error: {e}")
        return None

def calculate_stochastic_analysis(prices):
    try:
        if not prices or len(prices) < 10:
            return {'sharpe_ratio': 0, 'var_95': 0}
        arr = np.array(prices, dtype=float)
        log_returns = np.diff(np.log(arr))
        daily_volatility = np.std(log_returns)
        drift = np.mean(log_returns)
        sharpe_ratio = (drift - 0.02/252) / daily_volatility if daily_volatility > 0 else 0
        return {'sharpe_ratio': float(sharpe_ratio), 'var_95': float(np.percentile(log_returns, 5))}
    except Exception as e:
        logger.error(f"Stochastic error: {e}")
        return {'sharpe_ratio': 0, 'var_95': 0}

def calc_rsi(prices, period=14):
    try:
        if len(prices) < period + 1:
            return 50.0
        gains = [max(prices[i] - prices[i-1], 0) for i in range(-period, 0)]
        losses = [max(-(prices[i] - prices[i-1]), 0) for i in range(-period, 0)]
        avg_gain, avg_loss = sum(gains)/period, sum(losses)/period
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    except:
        return 50.0

def calc_atr(highs, lows, closes, period=14):
    try:
        if len(closes) < period + 1:
            return closes[-1] * 0.02
        tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(-period, 0)]
        return sum(tr) / period
    except:
        return closes[-1] * 0.02

def get_recommendation(rsi, sharpe, smc_score, smc_zone, diff_trend_score):
    score = 0
    if rsi < 30: score += 3
    elif rsi > 70: score -= 3
    if sharpe > 1.0: score += 2
    elif sharpe < -0.5: score -= 2
    if smc_score: score += smc_score
    if smc_zone == 'LOWER_DISCOUNT': score += 2
    elif smc_zone == 'UPPER_PREMIUM': score -= 2
    score += diff_trend_score
    
    if score >= 4: return 'شراء قوي', score, 'عالية'
    elif score >= 1: return 'شراء حذر', score, 'متوسطة'
    elif score <= -4: return 'بيع قوي', score, 'عالية'
    elif score <= -1: return 'بيع حذر', score, 'متوسطة'
    else: return 'انتظار', score, 'منخفضة'

def calculate_correlation_matrix():
    try:
        cache_key = 'correlation_matrix'
        cached = get_cached_data(cache_key)
        if cached:
            return cached
        
        symbols = ['BTC-USD', 'ETH-USD', 'GC=F', 'CL=F', '^GSPC']
        data = {}
        
        for symbol in symbols:
            try:
                logger.info(f"🔄 Fetching {symbol} for correlation...")
                result = fetch_data(symbol, '1d')
                if result and len(result['closes']) >= 20:
                    data[symbol] = result['closes']
                    logger.info(f"✅ Got {symbol} ({len(result['closes'])} candles)")
                else:
                    logger.warning(f"⚠️ Failed for {symbol}")
            except Exception as e:
                logger.error(f"❌ Error for {symbol}: {e}")
        
        if len(data) < 2:
            logger.warning(f"Only {len(data)} assets available")
            return None
        
        df = pd.DataFrame(data)
        corr_matrix = df.pct_change().dropna().corr().round(2)
        result = corr_matrix.to_dict()
        
        set_cached_data(cache_key, result)
        logger.info(f"✅ Correlation matrix ready for {len(data)} assets")
        return result
    except Exception as e:
        logger.error(f"Correlation error: {e}")
        return None

def calculate_transition_matrix(closes):
    try:
        if len(closes) < 30:
            return None
        
        states = []
        for i in range(1, len(closes)):
            change = (closes[i] - closes[i-1]) / closes[i-1]
            if change > 0.01: states.append(0)
            elif change < -0.01: states.append(1)
            else: states.append(2)
        
        transition = np.zeros((3, 3))
        for i in range(len(states)-1):
            transition[states[i]][states[i+1]] += 1
        
        for i in range(3):
            if transition[i].sum() > 0:
                transition[i] /= transition[i].sum()
        
        return transition
    except:
        return None

def predict_market_states(transition_matrix, current_state, days=[1, 3, 5]):
    try:
        if transition_matrix is None:
            return None
        predictions = {}
        for days_ahead in days:
            future_probs = np.linalg.matrix_power(transition_matrix, days_ahead)
            probs = future_probs[current_state]
            predictions[days_ahead] = {'صاعد': float(probs[0]), 'هابط': float(probs[1]), 'جانبي': float(probs[2])}
        return predictions
    except:
        return None

def calculate_generating_function(closes, target_price, stop_loss, current_price):
    try:
        if len(closes) < 20:
            return None
        prices = np.array(closes, dtype=float)
        log_returns = np.diff(np.log(prices))
        mu, sigma = float(np.mean(log_returns)), float(np.std(log_returns))
        if sigma < 1e-10:
            return None
        
        dist_to_tp = np.log(target_price / current_price)
        dist_to_sl = np.log(stop_loss / current_price)
        
        if abs(mu) < 1e-10:
            prob_tp_5d = 0.5 + 0.5 * np.tanh(dist_to_tp / (sigma * np.sqrt(5)))
            prob_sl_5d = 0.5 + 0.5 * np.tanh(dist_to_sl / (sigma * np.sqrt(5)))
        else:
            prob_tp_5d = max(0.01, min(0.99, 1 - np.exp(-2 * mu * dist_to_tp / (sigma**2))))
            prob_sl_5d = max(0.01, min(0.99, 1 - np.exp(-2 * mu * dist_to_sl / (sigma**2))))
        
        return {
            'prob_tp_5d': float(prob_tp_5d),
            'prob_tp_10d': float(min(0.99, prob_tp_5d * 1.2)),
            'prob_tp_20d': float(min(0.99, prob_tp_5d * 1.35)),
            'prob_sl_5d': float(prob_sl_5d),
            'prob_sl_10d': float(min(0.99, prob_sl_5d * 1.5)),
            'prob_sl_20d': float(min(0.99, prob_sl_5d * 1.8)),
            'generating_function': f"1 / (1 - {2*mu/sigma**2:.2f}x - {-mu**2/sigma**2:.2f}x² + 0.05x³)",
            'risk_reward_ratio': float(abs(dist_to_tp / dist_to_sl)) if abs(dist_to_sl) > 1e-10 else 1.0
        }
    except:
        return None

def calculate_portfolio_eigenvalues():
    try:
        cache_key = 'portfolio_eigenvalues'
        cached = get_cached_data(cache_key)
        if cached:
            return cached
        
        symbols = ['BTC-USD', 'ETH-USD', 'GC=F', 'CL=F', '^GSPC']
        data = {}
        
        for symbol in symbols:
            try:
                logger.info(f"🔄 Fetching {symbol} for eigenvalues...")
                result = fetch_data(symbol, '1d')
                if result and len(result['closes']) >= 30:
                    data[symbol] = result['closes']
                    logger.info(f"✅ Got {symbol}")
                else:
                    logger.warning(f"⚠️ Failed for {symbol}")
            except Exception as e:
                logger.error(f"❌ Error for {symbol}: {e}")
        
        if len(data) < 2:
            return None
        
        df = pd.DataFrame(data).pct_change().dropna()
        if df.empty or len(df) < 10:
            return None
        
        corr_matrix = df.corr().values
        eigenvalues, eigenvectors = np.linalg.eigh(corr_matrix)
        
        idx = np.argsort(eigenvalues)[::-1]
        eigenvalues = eigenvalues[idx]
        
        total_var = eigenvalues.sum()
        if total_var < 1e-10:
            return None
        variance_ratio = eigenvalues / total_var
        
        n_assets = len(data)
        optimal_weights = np.ones(n_assets) / n_assets
        symbols_list = list(data.keys())
        
        for i in range(n_assets):
            for j in range(i+1, n_assets):
                if corr_matrix[i, j] > 0.7:
                    optimal_weights[i] *= 0.7
                    optimal_weights[j] *= 0.7
        
        optimal_weights = optimal_weights / optimal_weights.sum()
        
        current_var = (np.ones(n_assets)/n_assets) @ corr_matrix @ (np.ones(n_assets)/n_assets)
        optimal_var = optimal_weights @ corr_matrix @ optimal_weights
        risk_reduction = float(1 - optimal_var / current_var) if current_var > 0 else 0
        
        result = {
            'eigenvalues': [float(x) for x in eigenvalues],
            'variance_ratio': [float(x) for x in variance_ratio],
            'symbols': symbols_list,
            'current_weights': [1/n_assets]*n_assets,
            'optimal_weights': [float(x) for x in optimal_weights],
            'risk_reduction': risk_reduction
        }
        
        set_cached_data(cache_key, result)
        logger.info(f"✅ Portfolio eigenvalues calculated")
        return result
    except:
        return None

def unified_analysis(symbol, interval="4h"):
    try:
        data = fetch_data(symbol, interval)
        if not data:
            return None
        
        closes, highs, lows = data['closes'], data['highs'], data['lows']
        price = float(closes[-1])
        rsi, atr = calc_rsi(closes), calc_atr(highs, lows, closes)
        stochastic = calculate_stochastic_analysis(closes)
        diff_analysis = advanced_differential_analysis(closes)
        diff_trend_score = diff_analysis.get('trend_score', 0) if diff_analysis else 0
        
        smc_score, smc_zone = 0, 'محايد'
        if get_smc_analysis:
            try:
                smc_data = get_smc_analysis(closes, highs, lows, data.get('volumes', []))
                smc_score = smc_data.get('score', 0)
                smc_zone = smc_data.get('premium_discount', {}).get('zone', 'محايد')
            except:
                pass
        
        action, score, conf = get_recommendation(rsi, stochastic.get('sharpe_ratio', 0), smc_score, smc_zone, diff_trend_score)
        
        if 'شراء' in action:
            sl, tp1, tp2, tp3 = price - (atr * 0.8), price + (atr * 1.5), price + (atr * 2.5), price + (atr * 3.5)
        elif 'بيع' in action:
            sl, tp1, tp2, tp3 = price + (atr * 0.8), price - (atr * 1.5), price - (atr * 2.5), price - (atr * 3.5)
        else:
            sl, tp1, tp2, tp3 = price - (atr * 0.8), price + (atr * 1.5), price + (atr * 2.5), price + (atr * 3.5)
        
        rr = abs(tp1 - price) / abs(price - sl) if abs(price - sl) > 0 else 0
        
        result = {
            'symbol': symbol, 'price': float(price), 'interval': interval,
            'rsi': float(rsi), 'atr': float(atr), 'sl': float(sl),
            'tp1': float(tp1), 'tp2': float(tp2), 'tp3': float(tp3),
            'rr': float(rr), 'action': action, 'score': int(score),
            'confidence': conf, 'stochastic': stochastic, 'smc_zone': smc_zone,
            'differential': diff_analysis, 'transition_matrix': None,
            'market_predictions': None, 'generating_function': None,
            'correlation_matrix': None, 'portfolio_analysis': None
        }
        
        try:
            tm = calculate_transition_matrix(closes)
            if tm is not None:
                result['transition_matrix'] = tm.tolist()
                cs = 0 if diff_trend_score > 0 else (1 if diff_trend_score < 0 else 2)
                result['market_predictions'] = predict_market_states(tm, cs, [1, 3, 5])
        except: pass
        
        try:
            result['generating_function'] = calculate_generating_function(closes, tp1, sl, price)
        except: pass
        
        try:
            result['correlation_matrix'] = calculate_correlation_matrix()
        except: pass
        
        try:
            result['portfolio_analysis'] = calculate_portfolio_eigenvalues()
        except: pass
        
        return result
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        return None

def ask_qwen_ai(data):
    logger.info(f"🤖 Starting AI analysis for {data.get('symbol', 'unknown')}")
    
    if not OPENROUTER_API_KEY:
        logger.error("❌ OPENROUTER_API_KEY is not set!")
        return None
    
    logger.info(f"✅ OPENROUTER_API_KEY found (length: {len(OPENROUTER_API_KEY)})")
    
    try:
        diff_info = f"المشتقات: {data['differential']['trend']}. " if data.get('differential') else ""
        pred_info = ""
        if data.get('market_predictions') and 1 in data['market_predictions']:
            pred_info = f"توقع غد: صاعد {int(data['market_predictions'][1].get('صاعد', 0)*100)}%. "
        
        prompt = (f"أنت محلل تداول محترف. الرمز: {data['symbol']}. السعر: {data['price']}. "
                  f"التوصية: {data['action']}. RSI: {data['rsi']:.1f}. Sharpe: {data['stochastic'].get('sharpe_ratio', 0):.2f}. "
                  f"المنطقة: {data['smc_zone']}. {diff_info}{pred_info} "
                  f"أعطِ جملة واحدة فقط بالعربية تشرح سبب التوصية. لا تستخدم ** أو # أو أي رموز.")
        
        logger.info(f"📤 Sending request to OpenRouter...")
        
        res = requests.post(
            OPENROUTER_API_URL,
            json={
                "model": "qwen/qwen-2.5-72b-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 150
            },
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        
        logger.info(f"📥 Response status: {res.status_code}")
        
        if res.status_code == 200:
            result = res.json()
            if 'choices' in result and len(result['choices']) > 0:
                analysis = result['choices'][0]['message']['content'].strip()
                analysis = analysis.replace('**', '').replace('*', '').replace('#', '').replace('`', '')
                logger.info(f"✅ AI analysis received: {analysis[:50]}...")
                return analysis[:197] + "..." if len(analysis) > 200 else analysis
            else:
                logger.error(f"❌ No choices in response: {result}")
        else:
            logger.error(f"❌ API returned status {res.status_code}: {res.text[:200]}")
            
    except requests.exceptions.Timeout:
        logger.error("❌ Request timed out")
    except requests.exceptions.ConnectionError:
        logger.error("❌ Connection error")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
    
    return None

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Trading Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); color: #fff; min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { text-align: center; margin-bottom: 10px; background: linear-gradient(90deg, #00d2ff, #3a7bd5); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-size: 2.5em; }
        .subtitle { text-align: center; color: #aaa; margin-bottom: 30px; }
        .control-panel { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 15px; padding: 25px; margin-bottom: 30px; }
        .control-row { display: flex; gap: 15px; flex-wrap: wrap; align-items: center; justify-content: center; }
        .control-group { display: flex; flex-direction: column; gap: 8px; }
        .control-group label { color: #aaa; font-size: 0.9em; }
        select, button { padding: 12px 20px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(0,0,0,0.3); color: #fff; font-size: 1em; cursor: pointer; }
        button { background: linear-gradient(90deg, #00d2ff, #3a7bd5); border: none; font-weight: bold; }
        button:hover { transform: scale(1.05); }
        button:disabled { opacity: 0.5; }
        .results-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .result-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 15px; padding: 25px; }
        .result-card h3 { color: #00d2ff; margin-bottom: 15px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; }
        .result-item { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .result-label { color: #aaa; }
        .result-value { font-weight: bold; }
        .recommendation { text-align: center; padding: 30px; border-radius: 15px; margin-bottom: 30px; font-size: 1.5em; font-weight: bold; }
        .rec-buy { background: linear-gradient(135deg, #00c853, #009624); }
        .rec-sell { background: linear-gradient(135deg, #ff1744, #d50000); }
        .rec-wait { background: linear-gradient(135deg, #ffab00, #ff8f00); }
        .levels-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .level-card { background: rgba(255,255,255,0.05); border-radius: 10px; padding: 20px; text-align: center; }
        .level-card .label { color: #aaa; font-size: 0.9em; margin-bottom: 10px; }
        .level-card .value { font-size: 1.3em; font-weight: bold; }
        .sl { color: #ff1744; }
        .tp { color: #00c853; }
        .ai-section { background: rgba(0,210,255,0.1); border: 1px solid rgba(0,210,255,0.3); border-radius: 15px; padding: 25px; margin-bottom: 30px; }
        .ai-section h3 { color: #00d2ff; margin-bottom: 15px; }
        .corr-matrix { display: grid; gap: 5px; margin-top: 15px; }
        .corr-cell { padding: 8px; text-align: center; border-radius: 5px; font-size: 0.85em; font-weight: bold; }
        .corr-high { background: rgba(0,200,83,0.3); }
        .corr-med { background: rgba(255,171,0,0.3); }
        .corr-low { background: rgba(255,23,68,0.3); }
        .corr-header { background: rgba(0,210,255,0.2); color: #00d2ff; }
        .prediction-bar { display: flex; height: 30px; border-radius: 15px; overflow: hidden; margin: 10px 0; }
        .pred-up { background: #00c853; }
        .pred-down { background: #ff1744; }
        .pred-side { background: #ffab00; }
        .hidden { display: none; }
        .loading { text-align: center; padding: 40px; color: #00d2ff; }
        .error { background: rgba(255,23,68,0.1); border: 1px solid rgba(255,23,68,0.3); border-radius: 15px; padding: 25px; text-align: center; color: #ff1744; }
        @media (max-width: 768px) { .control-row { flex-direction: column; } select { width: 100%; } h1 { font-size: 1.8em; } }
    </style>
</head>
<body>
    <div class="container">
        <h1>Quant Trading Bot</h1>
        <p class="subtitle">Advanced Mathematics Edition</p>
        <div class="control-panel">
            <div class="control-row">
                <div class="control-group">
                    <label>الأصل</label>
                    <select id="symbol">
                        <optgroup label="💰 Crypto">
                            <option value="BTC-USD">BTC-USD (Bitcoin)</option>
                            <option value="ETH-USD">ETH-USD (Ethereum)</option>
                            <option value="XRP-USD">XRP-USD (Ripple)</option>
                            <option value="BNB-USD">BNB-USD (Binance)</option>
                            <option value="SOL-USD">SOL-USD (Solana)</option>
                            <option value="ADA-USD">ADA-USD (Cardano)</option>
                            <option value="DOGE-USD">DOGE-USD (Dogecoin)</option>
                        </optgroup>
                        <optgroup label="🥇 Metals">
                            <option value="GC=F">Gold (GC=F)</option>
                            <option value="SI=F">Silver (SI=F)</option>
                        </optgroup>
                        <optgroup label="⛽ Energy">
                            <option value="CL=F">Oil (CL=F)</option>
                        </optgroup>
                        <optgroup label="💱 Forex">
                            <option value="EURUSD=X">EUR/USD</option>
                            <option value="GBPUSD=X">GBP/USD</option>
                            <option value="USDJPY=X">USD/JPY</option>
                        </optgroup>
                        <optgroup label="📊 Indices">
                            <option value="^GSPC">S&P 500</option>
                            <option value="^IXIC">NASDAQ</option>
                            <option value="^DJI">Dow Jones</option>
                        </optgroup>
                        <optgroup label="🏢 Stocks">
                            <option value="AAPL">Apple</option>
                            <option value="TSLA">Tesla</option>
                            <option value="MSFT">Microsoft</option>
                            <option value="NVDA">NVIDIA</option>
                            <option value="GOOGL">Google</option>
                        </optgroup>
                    </select>
                </div>
                <div class="control-group">
                    <label>الفريم</label>
                    <select id="timeframe">
                        <option value="15m">15 دقيقة</option>
                        <option value="1h">1 ساعة</option>
                        <option value="4h" selected>4 ساعات</option>
                        <option value="1d">1 يوم</option>
                    </select>
                </div>
                <div class="control-group">
                    <label>&nbsp;</label>
                    <button id="analyzeBtn" onclick="analyze()">تحليل</button>
                </div>
            </div>
        </div>
        <div id="loading" class="loading hidden">جاري التحليل...</div>
        <div id="error" class="error hidden"></div>
        <div id="results" class="hidden">
            <div id="recommendation" class="recommendation"></div>
            <div class="levels-grid">
                <div class="level-card"><div class="label">Stop Loss</div><div class="value sl" id="sl"></div></div>
                <div class="level-card"><div class="label">Target 1</div><div class="value tp" id="tp1"></div></div>
                <div class="level-card"><div class="label">Target 2</div><div class="value tp" id="tp2"></div></div>
                <div class="level-card"><div class="label">Target 3</div><div class="value tp" id="tp3"></div></div>
            </div>
            <div class="results-grid">
                <div class="result-card">
                    <h3>المؤشرات الفنية</h3>
                    <div class="result-item"><span class="result-label">السعر</span><span class="result-value" id="price"></span></div>
                    <div class="result-item"><span class="result-label">RSI</span><span class="result-value" id="rsi"></span></div>
                    <div class="result-item"><span class="result-label">ATR</span><span class="result-value" id="atr"></span></div>
                    <div class="result-item"><span class="result-label">Score</span><span class="result-value" id="score"></span></div>
                </div>
                <div class="result-card">
                    <h3>المشتقات التفاضلية</h3>
                    <div class="result-item"><span class="result-label">السرعة</span><span class="result-value" id="velocity"></span></div>
                    <div class="result-item"><span class="result-label">التسارع</span><span class="result-value" id="acceleration"></span></div>
                    <div class="result-item"><span class="result-label">الاتجاه</span><span class="result-value" id="trend"></span></div>
                </div>
                <div class="result-card">
                    <h3>Stochastic Calculus</h3>
                    <div class="result-item"><span class="result-label">Sharpe Ratio</span><span class="result-value" id="sharpe"></span></div>
                    <div class="result-item"><span class="result-label">VaR 95%</span><span class="result-value" id="var95"></span></div>
                </div>
                <div class="result-card">
                    <h3>Smart Money Concepts</h3>
                    <div class="result-item"><span class="result-label">المنطقة</span><span class="result-value" id="smcZone"></span></div>
                    <div class="result-item"><span class="result-label">العائد/المخاطرة</span><span class="result-value" id="rr"></span></div>
                </div>
                <div class="result-card" id="corrCard"><h3>📊 مصفوفة الارتباط</h3><div id="corrMatrix"></div><div id="corrAdvice" style="margin-top:15px; color:#00d2ff;"></div></div>
                <div class="result-card" id="predCard"><h3>🔮 التنبؤ بحالة السوق</h3><div id="predictions"></div></div>
                <div class="result-card" id="genCard"><h3>🎲 دالة التوليد</h3><div id="genFunction"></div></div>
                <div class="result-card" id="eigenCard"><h3>🧬 القيم الذاتية</h3><div id="eigenvalues"></div></div>
            </div>
            <div class="ai-section"><h3>🤖 تحليل الذكاء الاصطناعي</h3><p id="aiAnalysis"></p></div>
        </div>
    </div>
    <script>
        async function analyze() {
            const symbol = document.getElementById('symbol').value;
            const timeframe = document.getElementById('timeframe').value;
            const btn = document.getElementById('analyzeBtn');
            const loading = document.getElementById('loading');
            const error = document.getElementById('error');
            const results = document.getElementById('results');
            btn.disabled = true; loading.classList.remove('hidden'); error.classList.add('hidden'); results.classList.add('hidden');
            try {
                const response = await fetch('/api/analyze', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({symbol, timeframe}) });
                const data = await response.json();
                if (data.error) { error.textContent = data.error; error.classList.remove('hidden'); } 
                else { displayResults(data); results.classList.remove('hidden'); }
            } catch (err) { error.textContent = 'حدث خطأ في الاتصال'; error.classList.remove('hidden'); } 
            finally { btn.disabled = false; loading.classList.add('hidden'); }
        }
        function displayResults(data) {
            const rec = document.getElementById('recommendation');
            rec.textContent = data.action + ' (Score: ' + data.score + ')';
            rec.className = 'recommendation';
            if (data.action.includes('شراء')) rec.classList.add('rec-buy');
            else if (data.action.includes('بيع')) rec.classList.add('rec-sell');
            else rec.classList.add('rec-wait');
            
            document.getElementById('price').textContent = data.price.toFixed(2);
            document.getElementById('rsi').textContent = data.rsi.toFixed(1);
            document.getElementById('atr').textContent = data.atr.toFixed(2);
            document.getElementById('score').textContent = data.score;
            document.getElementById('sl').textContent = data.sl.toFixed(2);
            document.getElementById('tp1').textContent = data.tp1.toFixed(2);
            document.getElementById('tp2').textContent = data.tp2.toFixed(2);
            document.getElementById('tp3').textContent = data.tp3.toFixed(2);
            document.getElementById('rr').textContent = '1:' + data.rr.toFixed(2);
            document.getElementById('smcZone').textContent = data.smc_zone;
            
            if (data.differential) {
                document.getElementById('velocity').textContent = data.differential.velocity.toFixed(2);
                document.getElementById('acceleration').textContent = data.differential.acceleration.toFixed(2);
                document.getElementById('trend').textContent = data.differential.trend;
            }
            if (data.stochastic) {
                document.getElementById('sharpe').textContent = data.stochastic.sharpe_ratio.toFixed(2);
                document.getElementById('var95').textContent = (data.stochastic.var_95 * 100).toFixed(2) + '%';
            }
            if (data.correlation_matrix) {
                const corrDiv = document.getElementById('corrMatrix');
                const symbols = Object.keys(data.correlation_matrix);
                let html = '<div class="corr-matrix" style="grid-template-columns: 80px repeat(' + symbols.length + ', 1fr);">';
                html += '<div class="corr-cell corr-header"></div>';
                symbols.forEach(s => { html += '<div class="corr-cell corr-header">' + s.split('-')[0] + '</div>'; });
                symbols.forEach(row => {
                    html += '<div class="corr-cell corr-header">' + row.split('-')[0] + '</div>';
                    symbols.forEach(col => {
                        const val = data.correlation_matrix[row][col];
                        let cls = 'corr-low';
                        if (val > 0.6) cls = 'corr-high';
                        else if (val > 0.3) cls = 'corr-med';
                        html += '<div class="corr-cell ' + cls + '">' + val.toFixed(2) + '</div>';
                    });
                });
                html += '</div>';
                corrDiv.innerHTML = html;
                let advice = '💡 ', highCorr = [];
                symbols.forEach((s1, i) => {
                    symbols.forEach((s2, j) => {
                        if (i < j && data.correlation_matrix[s1][s2] > 0.7) highCorr.push(s1.split('-')[0] + ' و ' + s2.split('-')[0]);
                    });
                });
                document.getElementById('corrAdvice').textContent = advice + (highCorr.length > 0 ? 'تجنب: ' + highCorr.join(', ') + ' (ارتباط عالي)' : 'المحفظة متنوعة بشكل جيد');
            } else {
                document.getElementById('corrCard').innerHTML = '<h3>📊 مصفوفة الارتباط</h3><p style="color:#aaa;">غير متاحة حالياً</p>';
            }
            if (data.market_predictions) {
                const predDiv = document.getElementById('predictions');
                let html = '';
                [1, 3, 5].forEach(days => {
                    const pred = data.market_predictions[days];
                    if (pred) {
                        html += '<div style="margin-bottom:15px;"><div style="margin-bottom:5px;">بعد ' + days + ' يوم:</div>';
                        html += '<div class="prediction-bar"><div class="pred-up" style="width:' + (pred['صاعد']*100) + '%"></div>';
                        html += '<div class="pred-side" style="width:' + (pred['جانبي']*100) + '%"></div>';
                        html += '<div class="pred-down" style="width:' + (pred['هابط']*100) + '%"></div></div>';
                        html += '<div style="display:flex;justify-content:space-between;font-size:0.85em;">';
                        html += '<span style="color:#00c853;">صاعد: ' + (pred['صاعد']*100).toFixed(0) + '%</span>';
                        html += '<span style="color:#ffab00;">جانبي: ' + (pred['جانبي']*100).toFixed(0) + '%</span>';
                        html += '<span style="color:#ff1744;">هابط: ' + (pred['هابط']*100).toFixed(0) + '%</span></div></div>';
                    }
                });
                predDiv.innerHTML = html;
            } else {
                document.getElementById('predCard').innerHTML = '<h3>🔮 التنبؤ بحالة السوق</h3><p style="color:#aaa;">غير متاحة حالياً</p>';
            }
            if (data.generating_function) {
                const genDiv = document.getElementById('genFunction');
                const gf = data.generating_function;
                let html = '<div style="background:rgba(0,0,0,0.3);padding:15px;border-radius:10px;margin-bottom:15px;font-family:monospace;">G(x) = ' + gf.generating_function + '</div>';
                html += '<div class="result-item"><span class="result-label">احتمال TP1 (5 أيام)</span><span class="result-value" style="color:#00c853;">' + (gf.prob_tp_5d*100).toFixed(0) + '%</span></div>';
                html += '<div class="result-item"><span class="result-label">احتمال SL (5 أيام)</span><span class="result-value" style="color:#ff1744;">' + (gf.prob_sl_5d*100).toFixed(0) + '%</span></div>';
                html += '<div class="result-item"><span class="result-label">نسبة المخاطرة/العائد</span><span class="result-value">1:' + gf.risk_reward_ratio.toFixed(2) + '</span></div>';
                genDiv.innerHTML = html;
            } else {
                document.getElementById('genCard').innerHTML = '<h3>🎲 دالة التوليد</h3><p style="color:#aaa;">غير متاحة حالياً</p>';
            }
            if (data.portfolio_analysis) {
                const eigDiv = document.getElementById('eigenvalues');
                const pa = data.portfolio_analysis;
                let html = '<div style="margin-bottom:15px;"><strong>القيم الذاتية:</strong></div>';
                pa.eigenvalues.forEach((val, i) => {
                    html += '<div style="display:flex;justify-content:space-between;padding:5px 0;"><span>λ' + (i+1) + ' = ' + val.toFixed(2) + '</span><span style="color:#00d2ff;">' + (pa.variance_ratio[i] * 100).toFixed(0) + '%</span></div>';
                });
                html += '<div style="margin-top:15px;"><strong>المحفظة المحسنة:</strong></div>';
                pa.symbols.forEach((sym, i) => {
                    html += '<div style="display:flex;justify-content:space-between;padding:5px 0;"><span>' + sym.split('-')[0] + '</span><span>' + (pa.optimal_weights[i]*100).toFixed(0) + '%</span></div>';
                });
                html += '<div style="margin-top:15px;padding:10px;background:rgba(0,200,83,0.1);border-radius:8px;">💡 تقليل المخاطر: ' + (pa.risk_reduction * 100).toFixed(0) + '%</div>';
                eigDiv.innerHTML = html;
            } else {
                document.getElementById('eigenCard').innerHTML = '<h3>🧬 القيم الذاتية</h3><p style="color:#aaa;">غير متاحة حالياً</p>';
            }
            document.getElementById('aiAnalysis').textContent = data.ai_analysis || 'غير متاح';
        }
    </script>
</body>
</html>
"""

app = Flask(__name__)

@app.route('/')
def home(): return "Bot is running!"

@app.route('/health')
def health(): return jsonify({"status": "ok"})

@app.route('/dashboard')
def dashboard(): return render_template_string(DASHBOARD_HTML)

@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    try:
        data = request.json
        symbol = data.get('symbol', 'BTC-USD')
        timeframe = data.get('timeframe', '4h')
        
        logger.info(f"🔍 Analyzing {symbol} {timeframe}")
        
        analysis = unified_analysis(symbol, timeframe)
        if not analysis:
            return jsonify({'error': f'فشل تحليل {symbol}'}), 400
        
        if OPENROUTER_API_KEY:
            logger.info("🤖 Attempting AI analysis...")
            try:
                ai_result = ask_qwen_ai(analysis)
                analysis['ai_analysis'] = ai_result
                if ai_result:
                    logger.info("✅ AI analysis added successfully")
                else:
                    logger.warning("⚠️ AI analysis returned None")
            except Exception as e:
                logger.error(f"❌ AI analysis failed: {e}")
                analysis['ai_analysis'] = None
        else:
            logger.warning("⚠️ OPENROUTER_API_KEY not set, skipping AI")
            analysis['ai_analysis'] = None
        
        return jsonify(analysis)
    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({'error': str(e)}), 500

def run_flask():
    try: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
    except Exception as e: logger.error(f"Flask error: {e}")

async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("الاستخدام: /smart <رمز> [تايم_فريم]")
            return
        symbol = args[0].strip()
        interval = args[1].strip().lower() if len(args) > 1 else "4h"
        if interval not in ['15m', '1h', '4h', '1d']: interval = "4h"
        
        await update.message.reply_text(f"جاري تحليل {symbol}...")
        data = unified_analysis(symbol, interval)
        if not data:
            await update.message.reply_text(f"فشل تحليل {symbol}.")
            return
        
        ai_analysis = None
        if OPENROUTER_API_KEY:
            await update.message.reply_text("جاري تحليل الذكاء الاصطناعي...")
            ai_analysis = ask_qwen_ai(data)
        
        emoji = "🟢" if 'شراء' in data['action'] else "🔴" if 'بيع' in data['action'] else "⚪"
        dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
        
        diff_section = f"\nالمشتقات:\n- السرعة: {data['differential']['velocity']:+.2f}\n- التسارع: {data['differential']['acceleration']:+.2f}\n- الاتجاه: {data['differential']['trend']}\n" if data.get('differential') else ""
        ai_section = f"\nتحليل الذكاء الاصطناعي:\n{ai_analysis}\n" if ai_analysis else ""
        
        msg = (f"{emoji} {data['symbol']} ({data['interval'].upper()})\n"
               f"السعر: {data['price']:,.2f}\nالتوصية: {data['action']} (Score: {data['score']})\n"
               f"العائد/المخاطرة: 1:{data['rr']:.2f}\n\nمستويات التداول:\n"
               f"SL: {data['sl']:,.2f}\nTP1: {data['tp1']:,.2f}\nTP2: {data['tp2']:,.2f}\nTP3: {data['tp3']:,.2f}\n"
               f"{diff_section}{ai_section}\nللتفاصيل: {dashboard_url}/dashboard\nهذه ليست نصيحة مالية.")
        
        await update.message.reply_text(msg)
        
        if log_trade:
            try: log_trade(symbol=data['symbol'], action=data['action'], price=data['price'], score=data['score'], tp1=data['tp1'], tp2=data['tp2'], tp3=data['tp3'], sl=data['sl'])
            except: pass
    except Exception as e:
        logger.error(f"Command error: {e}")
        await update.message.reply_text(f"حدث خطأ: {str(e)}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dashboard_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')
    await update.message.reply_text(f"بوت التداول الكمي المتقدم\n\nالأوامر:\n/smart <رمز> [تايم_فريم]\n/dashboard\n\n{dashboard_url}/dashboard")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_performance:
        stats = get_performance()
        await update.message.reply_text(f"الإجمالي: {stats.get('total', 0)}\nالرابحة: {stats.get('wins', 0)}\nالخاسرة: {stats.get('losses', 0)}\nالنجاح: {stats.get('win_rate', 0)}%")
    else:
        await update.message.reply_text("إحصائيات غير متاحة")

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Dashboard: {os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'worker-production-7139.up.railway.app')}/dashboard")

def main():
    logger.info("Starting bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask started")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("smart", smart_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
