import ccxt
import pandas as pd

SYMBOLS = ["GM/USDT:USDT","TOSHI/USDT:USDT","DOPE/USDT:USDT","PHA/USDT:USDT","NAORIS/USDT:USDT","WANGCHAI/USDT:USDT"]
TIMEFRAMES = ["15m","30m","1h","4h","1d"]

exchange = ccxt.mexc()

def get_df(symbol, tf, limit=300):
    ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
    df['swing_high'] = df['h'].rolling(10).max()
    df['swing_low'] = df['l'].rolling(10).min()
    return df

# --- SMC CORE FUNCTIONS ---
def liquidity_sweep(df):
    # Part 01 & 09: Sweep above/below old high/low then close back inside
    prev_high = df['swing_high'].iloc[-2]
    prev_low = df['swing_low'].iloc[-2]
    last = df.iloc[-1]
    swept_high = last['h'] > prev_high and last['c'] < prev_high
    swept_low = last['l'] < prev_low and last['c'] > prev_low
    return swept_high or swept_low

def mss(df): # Market Structure Shift
    # Break of previous swing
    return df['c'].iloc[-1] > df['swing_high'].iloc[-2] or df['c'].iloc[-1] < df['swing_low'].iloc[-2]

def choch(df):
    # Part 08: Change of Character
    if df['c'].iloc[-1] > df['swing_high'].iloc[-2]: return "BULL_CHoCH"
    if df['c'].iloc[-1] < df['swing_low'].iloc[-2]: return "BEAR_CHoCH"
    return None

def fvg(df):
    # Part 02: Fair Value Gap
    for i in range(len(df)-3, len(df)-1):
        if df['l'].iloc[i] > df['h'].iloc[i-2] and df['c'].iloc[i] > df['o'].iloc[i]:
            return {"type":"BULL_FVG", "level": df['l'].iloc[i]}
        if df['h'].iloc[i] < df['l'].iloc[i-2] and df['c'].iloc[i] < df['o'].iloc[i]:
            return {"type":"BEAR_FVG", "level": df['h'].iloc[i]}
    return None

def ifvg(df):
    # Part 04: Inverted FVG - FVG that got violated = reversal
    f = fvg(df)
    if f and df['c'].iloc[-1] < f['level'] and f['type']=="BULL_FVG":
        return "BEAR_IFVG"
    if f and df['c'].iloc[-1] > f['level'] and f['type']=="BEAR_FVG":
        return "BULL_IFVG"
    return None

def order_block(df):
    # Part 03: Last opposite candle before impulse
    last_bear = df[df['c'] < df['o']].tail(1)
    last_bull = df[df['c'] > df['o']].tail(1)
    if not last_bear.empty and df['c'].iloc[-1] > last_bear['h'].values[0]:
        return {"type":"HIGH_OB_SHORT", "level": last_bear['h'].values[0]}
    if not last_bull.empty and df['c'].iloc[-1] < last_bull['l'].values[0]:
        return {"type":"LOW_OB_LONG", "level": last_bull['l'].values[0]}
    return None

def premium_discount(df):
    # Part 05: 50% of range
    high = df['h'].rolling(50).max().iloc[-1]
    low = df['l'].rolling(50).min().iloc[-1]
    eq = (high + low) / 2
    price = df['c'].iloc[-1]
    return "PREMIUM" if price > eq else "DISCOUNT"

def htf_poi_ltf_confirm(df_htf, df_ltf):
    # Part 07: HTF POI -> LTF Confirmation
    poi = order_block(df_htf)
    ltf_signal = choch(df_ltf) and fvg(df_ltf)
    return poi and ltf_signal

# --- MAIN SCANNER ---
def scan(symbol, tf):
    df = get_df(symbol, tf)
    last = df.iloc[-1]

    models = []
    if liquidity_sweep(df) and mss(df): models.append("PART01_LiquiditySweep_MSS_Entry")
    if fvg(df): models.append(f"PART02_FVG_Retest_{fvg(df)['type']}")
    if order_block(df) and mss(df): models.append(f"PART03_OB_MSS_{order_block(df)['type']}")
    if ifvg(df): models.append(f"PART04_{ifvg(df)}")
    models.append(f"PART05_{premium_discount(df)}")
    if choch(df) and fvg(df): models.append(f"PART08_{choch(df)}_FVG_Entry") # YOUR MAIN
    if liquidity_sweep(df): models.append("PART09_LiquidityGrab_Displacement")
    
    # Part 10 Failed model = if entry fails, invert
    # Logic handled in execution

    signal = "WAIT"
    if "PART08_BULL_CHoCH_FVG_Entry" in str(models) and premium_discount(df)=="DISCOUNT":
        signal = "LONG"
    if "PART08_BEAR_CHoCH_FVG_Entry" in str(models) and premium_discount(df)=="PREMIUM":
        signal = "SHORT"
    # Low OB = Long, High OB = Short (your rule)
    if order_block(df):
        if order_block(df)['type']=="LOW_OB_LONG": signal = "LONG"
        if order_block(df)['type']=="HIGH_OB_SHORT": signal = "SHORT"

    return {"tf":tf, "price":last['c'], "signal":signal, "models":models}

for sym in SYMBOLS:
    print(f"\n--- {sym} ---")
    for tf in TIMEFRAMES:
        try:
            r = scan(sym, tf)
            if r['signal']!="WAIT":
                print(f"{tf} => {r['signal']} | {r['models']} | Price {r['price']}")
        except Exception as e:
            print(f"{tf} error {e}")
