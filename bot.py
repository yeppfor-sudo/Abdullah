import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests

# --- Configuration ---
EXCHANGE_ID = 'mexc'
SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
TARGET_TIMEFRAMES = ['2h', '4h', '6h', '8h', '12h', '1d', '3d', '5d', '7d']

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_telegram_alert(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")
    else:
        print(f"[ALERT] {message}")

def fetch_and_resample_ohlcv(exchange, symbol, tf, limit=500):
    # Fetch base timeframe data (e.g., 1h or 2h) to resample exotic TFs like 6h, 8h, 3d, 5d, 7d
    base_tf = '1h' if tf in ['6h', '8h', '3d', '5d', '7d'] else tf
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=base_tf, limit=limit * 3)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Resample if needed
        if tf == '6h':
            df = df.resample('6h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        elif tf == '8h':
            df = df.resample('8h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        elif tf == '3d':
            df = df.resample('3D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        elif tf == '5d':
            df = df.resample('5D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        elif tf == '7d':
            df = df.resample('7D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
            
        return df.tail(limit)
    except Exception as e:
        print(f"Error fetching {symbol} on {tf}: {e}")
        return pd.DataFrame()

def analyze_market_structure(df, length=7):
    if len(df) < length * 2 + 1:
        return None, None, None
    
    # Calculate 20 EMA
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    # Swing highs and lows confirmation
    df['swing_high'] = df['high'][(df['high'] == df['high'].rolling(window=length*2+1, center=True).max())]
    df['swing_low'] = df['low'][(df['low'] == df['low'].rolling(window=length*2+1, center=True).min())]
    
    last_close = df['close'].iloc[-1]
    last_ema20 = df['ema20'].iloc[-1]
    
    # Recent valid swings for CHoCH / BOS detection
    valid_highs = df['swing_high'].dropna()
    valid_lows = df['swing_low'].dropna()
    
    if len(valid_highs) < 2 or len(valid_lows) < 2:
        return "WAITING", last_ema20, False
        
    latest_high = valid_highs.iloc[-1]
    latest_low = valid_lows.iloc[-1]
    
    structure = "BULLISH" if last_close > latest_low else "BEARISH"
    ema_cross = (last_close > last_ema20) if structure == "BULLISH" else (last_close < last_ema20)
    
    return structure, last_ema20, ema_cross

def run_scanner():
    exchange = ccxt.mexc({'options': {'defaultType': 'swap'}} )
    exchange.load_markets()
    
    for symbol in SYMBOLS:
        for tf in TARGET_TIMEFRAMES:
            df = fetch_and_resample_ohlcv(exchange, symbol, tf)
            if df.empty:
                continue
                
            structure, ema20, ema_ok = analyze_market_structure(df)
            last_close = df['close'].iloc[-1]
            
            # Condition for alert setup (CHoCH/BOS alignment with 20 EMA)
            if ema_ok and structure in ["BULLISH", "BEARISH"]:
                msg = f"🚨 *Setup Detected*\nSymbol: `{symbol}`\nTF: `{tf}`\nStructure: *{structure}*\nClose: `{last_close}`\n20 EMA: `{ema20:.4f}`"
                send_telegram_alert(msg)
                
            time.sleep(exchange.rateLimit / 1000)

if __name__ == "__main__":
    run_scanner()
