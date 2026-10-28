import ccxt, pandas as pd, os, requests, time

COINS = ["PHA/USDT:USDT", "GM/USDT:USDT", "KOMA/USDT:USDT", "WANGCHAI/USDT:USDT"]
TFS = ["5m", "15m", "1h", "4h", "1d"]
mexc = ccxt.mexc({"enableRateLimit": True})

def get_df(sym, tf):
    data = mexc.fetch_ohlcv(sym, tf, limit=100)
    df = pd.DataFrame(data, columns=["t","o","h","l","c","v"])
    df["ma5"] = df["v"].rolling(5).mean()
    df["ma10"] = df["v"].rolling(10).mean()
    return df

def analyze(sym, tf, df):
    last = df.iloc[-1]
    vol_ok = last["v"] > last["ma5"] > last["ma10"]  # PART 8 VOLUME
    sweep = last["h"] > df["h"].iloc[-20:-2].max() or last["l"] < df["l"].iloc[-20:-2].min()  # PART 1 SWEEP
    trend = "UP" if df["h"].iloc[-1] > df["h"].iloc[-10] else "DOWN"  # PART 3/4 TREND
    bull_ob = df.iloc[-4]["c"] < df.iloc[-4]["o"] and last["c"] > df.iloc[-3:]["h"].max()  # PART 2/6 DEMAND
    bear_ob = df.iloc[-4]["c"] > df.iloc[-4]["o"] and last["c"] < df.iloc[-3:]["l"].min()  # PART 2/5 SUPPLY
    fvg = df.iloc[-2]["l"] > df.iloc[-4]["h"] or df.iloc[-2]["h"] < df.iloc[-4]["l"]  # PART 2 FVG

    if vol_ok and fvg:
        if bull_ob:
            return f"LONG {sym} {tf} DEMAND {last['c']} VolOK"
        if bear_ob:
            return f"SHORT {sym} {tf} SUPPLY {last['c']} VolOK"
    return None

for coin in COINS:
    for tf in TFS:
        try:
            df = get_df(coin, tf)
            sig = analyze(coin, tf, df)
            if sig:
                print(sig)
                # Telegram
                if os.getenv("TG_TOKEN"):
                    requests.get(f"https://api.telegram.org/bot{os.getenv('TG_TOKEN')}/sendMessage",
                                 params={"chat_id": os.getenv("TG_CHAT"), "text": sig})
            time.sleep(0.4)
        except Exception as e:
            print(e)
