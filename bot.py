import ccxt, pandas as pd, os
from datetime import datetime

SYMBOLS = ["GM/USDT:USDT", "TOSHI/USDT:USDT", "DOPE/USDT:USDT", "PHA/USDT:USDT", "NAORIS/USDT:USDT", "WANGCHAI/USDT:USDT"]
TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]

exchange = ccxt.mexc({'enableRateLimit': True})

def get_df(symbol, tf):
    ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=200)
    df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
    return df

def find_order_blocks(df):
    # High Order Block = last bearish candle before bullish move = Short zone
    # Low Order Block = last bullish candle before bearish move = Long zone
    df['is_bull'] = df['c'] > df['o']
    df['is_bear'] = df['c'] < df['o']
    # Simplistic detection: high OB
    high_ob = df[(df['is_bear']) & (df['c'].shift(-1) > df['h'])].tail(1)
    low_ob = df[(df['is_bull']) & (df['c'].shift(-1) < df['l'])].tail(1)
    return high_ob, low_ob

def find_fvg(df):
    # Fair Value Gap: 3 candle pattern with gap
    fvg_bull = (df['l'].shift(1) > df['h'].shift(3)) & (df['c'] > df['o'])
    fvg_bear = (df['h'].shift(1) < df['l'].shift(3)) & (df['c'] < df['o'])
    return fvg_bull.iloc[-1], fvg_bear.iloc[-1]

def find_choch(df):
    # Change of Character: break of last high/low
    last_high = df['h'].rolling(20).max().shift(1).iloc[-1]
    last_low = df['l'].rolling(20).min().shift(1).iloc[-1]
    close = df['c'].iloc[-1]
    if close > last_high: return "BULL_CHOCH"
    if close < last_low: return "BEAR_CHOCH"
    return "NO_CHOCH"

def scan_symbol(symbol):
    results = {}
    for tf in TIMEFRAMES:
        df = get_df(symbol, tf)
        if len(df) < 50: continue
        
        high_ob, low_ob = find_order_blocks(df)
        fvg_bull, fvg_bear = find_fvg(df)
        choch = find_choch(df)
        last_price = df['c'].iloc[-1]
        vol_confirm = df['v'].iloc[-1] > df['v'].rolling(10).mean().iloc[-1]

        # YOUR RULE from voice:
        signal = "WAIT"
        if not low_ob.empty and fvg_bull and choch == "BULL_CHOCH" and vol_confirm:
            # Price retapped Low Order Block + FVG + CHoCH = LONG
            ob_low = low_ob['l'].values[0]
            if abs(last_price - ob_low)/ob_low < 0.03:  # within 3% of OB
                signal = "LONG - Low Order Block"
        if not high_ob.empty and fvg_bear and choch == "BEAR_CHOCH" and vol_confirm:
            ob_high = high_ob['h'].values[0]
            if abs(last_price - ob_high)/ob_high < 0.03:
                signal = "SHORT - High Order Block"
        
        results[tf] = {"signal": signal, "choch": choch, "fvg": fvg_bull or fvg_bear, "price": last_price}
    
    return results

def run():
    for sym in SYMBOLS:
        try:
            res = scan_symbol(sym)
            print(f"\n=== {sym} {datetime.utcnow()} ===")
            for tf, data in res.items():
                print(f"{tf}: {data['signal']} | {data['choch']} | Price {data['price']}")
                # Final filtered signal for your primary TF
                if tf == "1h" and "LONG" in data['signal']:
                    print(f">>> ALERT BUY {sym} on {tf}")
                if tf == "1h" and "SHORT" in data['signal']:
                    print(f">>> ALERT SELL {sym} on {tf}")
        except Exception as e:
            print(f"Error {sym} {e}")

if __name__ == "__main__":
    run()
