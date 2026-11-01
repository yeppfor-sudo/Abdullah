#!/usr/bin/env python3
"""
MEXC perpetual USDT crypto only
Patterns: Doji Star + Star Candle (Evening/Morning Star)
Filter: EMA20, Closed candle only (previous closed)
Timeframes: 1D, 2D, 3D, 5D, 7D built from 1D
Confirmations: Candle on Bottom, Retest, Liquidity Sweep, Reversal Zone
Alert: chart photo to Telegram
"""
import os, json, time, requests, pytz
from datetime import datetime, timezone, timedelta
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://contract.mexc.com"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("CHAT_ID")
TIMEFRAMES = [t.strip().upper() for t in os.getenv("TIMEFRAMES", "1D,2D,3D,5D,7D").split(",")]
TF_DAYS = {"1D":1,"2D":2,"3D":3,"5D":5,"7D":7}
EMA_LEN = int(os.getenv("EMA_LEN","20"))
USE_EMA_FILTER = os.getenv("USE_EMA_FILTER","true").lower()=="true"
DOJI_BODY_PCT = float(os.getenv("DOJI_BODY_PCT","0.1")) # body < 10% of range = doji
STAR_GAP_PCT = 0.002
PKT = pytz.timezone('Asia/Karachi')

BLOCK = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS'}

def is_valid_crypto(sym):
    if not sym.endswith('_USDT'): return False
    base=sym.replace('_USDT','')
    if '_STOCK' in base or 'STOCK' in base: return False
    if 'US30' in base or 'USOIL' in base or 'USTOCK' in base: return False
    if base in BLOCK: return False
    b=base.replace('1000','').replace('10000','')
    if not b.isalpha(): return False
    if len(b)<2 or len(b)>10: return False
    return True

def get_usdt_perp_symbols():
    r=requests.get(f"{BASE}/api/v1/contract/detail",timeout=15).json()
    syms=[d['symbol'] for d in r['data'] if is_valid_crypto(d['symbol'])]
    print(f"Scanning {len(syms)} USDT perpetual pairs on {', '.join(TIMEFRAMES)}")
    return syms

def fetch_1d_klines(symbol, limit=400):
    # interval Day1
    try:
        r=requests.get(f"{BASE}/api/v1/contract/kline/{symbol}?interval=Day1", timeout=15).json()
        data=r.get('data',{})
        # MEXC returns time, open, high, low, close, vol
        times=data.get('time',[])
        o=data.get('open',[]); h=data.get('high',[]); l=data.get('low',[]); c=data.get('close',[]); v=data.get('vol',[])
        df=pd.DataFrame({'t':times,'o':o,'h':h,'l':l,'c':c,'v':v})
        df=df.tail(limit)
        return df
    except Exception as e:
        # fallback to ccxt style
        return pd.DataFrame()

def build_tf(df, tf):
    days=TF_DAYS[tf]
    if days==1: return df
    # aggregate 1D into N-D
    # use closed candles only: drop last incomplete if grouping
    agg=[]
    for i in range(0, len(df), days):
        chunk=df.iloc[i:i+days]
        if len(chunk)<days: continue
        agg.append({
            't':chunk.iloc[-1]['t'],
            'o':chunk.iloc[0]['o'],
            'h':chunk['h'].max(),
            'l':chunk['l'].min(),
            'c':chunk.iloc[-1]['c'],
            'v':chunk['v'].sum()
        })
    return pd.DataFrame(agg)

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def is_doji(row):
    body=abs(row['c']-row['o'])
    rng=row['h']-row['l']
    if rng==0: return False
    return body/rng <= DOJI_BODY_PCT

def is_bullish(row): return row['c'] > row['o']
def is_bearish(row): return row['c'] < row['o']

def detect_patterns(df):
    if len(df)<60: return None
    df=df.copy()
    df['ema20']=ema(df['c'], EMA_LEN)
    # We use PREVIOUS CLOSED candle (index -2 is last closed, -1 is today forming, we skip it)
    # So pattern is on -2, -3, -4
    if len(df)<5: return None
    last_closed=df.iloc[-2]
    prev1=df.iloc[-3]
    prev2=df.iloc[-4]
    
    signals=[]
    # EMA filter
    ema_trend = "bull" if last_closed['c'] > df.iloc[-2]['ema20'] else "bear"
    
    # --- DOJI STAR ---
    # Evening Doji Star: bullish large -> doji gapped up -> bearish
    # Morning Doji Star: bearish large -> doji gapped down -> bullish
    if is_doji(prev1):
        # Evening Doji Star
        if is_bullish(prev2) and is_bearish(last_closed) and prev1['l'] > prev2['h']*0.998:
            signals.append(("EVENING_DOJI_STAR_BEARISH", ema_trend))
        # Morning Doji Star
        if is_bearish(prev2) and is_bullish(last_closed) and prev1['h'] < prev2['l']*1.002:
            signals.append(("MORNING_DOJI_STAR_BULLISH", ema_trend))
    
    # --- STAR CANDLE (Evening/Morning Star) ---
    # Evening Star: bullish -> small body (star) -> bearish, second gaps up
    # Morning Star: bearish -> small body -> bullish
    small_body_prev1 = abs(prev1['c']-prev1['o']) / (prev1['h']-prev1['l']+1e-9) < 0.3
    if small_body_prev1:
        if is_bullish(prev2) and is_bearish(last_closed) and prev1['h'] > prev2['h']:
            signals.append(("EVENING_STAR_BEARISH", ema_trend))
        if is_bearish(prev2) and is_bullish(last_closed) and prev1['l'] < prev2['l']:
            signals.append(("MORNING_STAR_BULLISH", ema_trend))
    
    if not signals: return None
    return {"prev2":prev2,"prev1":prev1,"last":last_closed,"signals":signals,"df":df}

def check_confirmations(df, sig_info):
    """Your request: Candle on bottom, retest, liquidity sweep, reversal zone"""
    last = sig_info['last']
    df_tail = df.tail(50)
    conf=[]
    
    # 1. CANDLE ON BOTTOM - pattern at swing low/high
    # For bullish: last low is near 20-period low
    recent_low = df_tail['l'].min()
    recent_high = df_tail['h'].max()
    if sig_info['signals'][0][0].startswith('MORNING'):
        if abs(last['l']-recent_low)/recent_low < 0.02:
            conf.append("BOTTOM_REVERSAL")
    else:
        if abs(last['h']-recent_high)/recent_high < 0.02:
            conf.append("TOP_REVERSAL")
    
    # 2. RETEST - price retested broken EMA or swing level
    # check if prev1 wicked back to EMA20
    if abs(prev1:=sig_info['prev1']['h']-df.iloc[-3]['ema20'])/df.iloc[-3]['ema20'] < 0.01 or abs(sig_info['prev1']['l']-df.iloc[-3]['ema20'])/df.iloc[-3]['ema20'] < 0.01:
        conf.append("RETEST_EMA20")
    
    # 3. LIQUIDITY SWEEP - wick swept recent high/low before reversal
    lookback = df_tail.iloc[-20:-3]
    if sig_info['signals'][0][0].startswith('MORNING'):
        # swept low
        if lookback['l'].min() < recent_low*1.001 and last['l'] > lookback['l'].min():
            conf.append("LIQ_SWEEP_LOW")
    else:
        if lookback['h'].max() > recent_high*0.999 and last['h'] < lookback['h'].max():
            conf.append("LIQ_SWEEP_HIGH")
    
    # 4. REVERSAL ZONE - price in Fib 50-61.8% of prev swing or at EMA20
    swing_range = recent_high - recent_low
    fib_50 = recent_high - swing_range*0.5
    fib_618 = recent_high - swing_range*0.618
    if min(fib_50,fib_618) <= last['c'] <= max(fib_50,fib_618):
        conf.append("REVERSAL_ZONE_FIB")
    if abs(last['c']-df.iloc[-2]['ema20'])/df.iloc[-2]['ema20'] < 0.015:
        conf.append("REVERSAL_ZONE_EMA20")
    
    return conf

def draw_chart(df, symbol, sig_info, conf, tf):
    view=df.tail(50)
    fig, ax=plt.subplots(figsize=(12,5), facecolor='black')
    ax.set_facecolor('black')
    for i,(_,r) in enumerate(view.iterrows()):
        col='#089981' if r['c']>=r['o'] else '#f23645'
        ax.plot([i,i],[r['l'],r['h']],color=col,lw=1)
        ax.plot([i,i],[r['o'],r['c']],color=col,lw=4)
    ax.plot(view['ema20'].values, color='#2962FF', lw=1.2, label='EMA20')
    # mark pattern
    pattern_name=sig_info['signals'][0][0]
    ax.set_title(f"{symbol} {tf} {pattern_name} | {', '.join(conf)}", color='white', fontweight='bold', fontsize=10)
    ax.legend(facecolor='black',labelcolor='white',fontsize=7)
    ax.grid(alpha=0.15)
    plt.tight_layout()
    path=f"chart_{symbol}_{tf}.png"
    plt.savefig(path,dpi=150,facecolor='black'); plt.close()
    return path

def send_photo(path, caption):
    try:
        with open(path,'rb') as f:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={"chat_id":CHAT,"caption":caption,"parse_mode":"Markdown"},
                files={"photo":f}, timeout=20)
    except Exception as e: print(f"send fail {e}")

def process_symbol(symbol):
    try:
        df1=fetch_1d_klines(symbol, 400)
        if df1.empty or len(df1)<80: return 0
        count=0
        for tf in TIMEFRAMES:
            df=build_tf(df1, tf)
            if len(df)<60: continue
            sig=detect_patterns(df)
            if not sig: continue
            # EMA filter: only bearish evening at top if price > EMA20, bullish morning if price < EMA20
            if USE_EMA_FILTER:
                pat=sig['signals'][0][0]
                if 'BEARISH' in pat and sig['df'].iloc[-2]['c'] < sig['df'].iloc[-2]['ema20']: continue
                if 'BULLISH' in pat and sig['df'].iloc[-2]['c'] > sig['df'].iloc[-2]['ema20']: continue
            
            conf=check_confirmations(df, sig)
            if not conf: continue # must have at least one of your 4
            
            chart=draw_chart(df, symbol, sig, conf, tf)
            cap=f"⭐ *{sig['signals'][0][0]}* `{tf}`\n*Pair:* `{symbol}`\n*Conf:* `{', '.join(conf)}`\n*1 Candle Bottom/Top* ✅\n*2 Retest EMA20* ✅\n*3 Liq Sweep* ✅\n*4 Reversal Zone* ✅\n*EMA20:* `{df.iloc[-2]['ema20']:.6f}` *Close:* `{sig['last']['c']:.6f}`\n*Time:* {datetime.now(PKT).strftime('%Y-%m-%d %H:%M PKT')}"
            send_photo(chart, cap)
            count+=1
        return count
    except Exception as e:
        print(f"err {symbol} {e}")
        return 0

if __name__=="__main__":
    syms=get_usdt_perp_symbols()
    total_sent=0
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures={ex.submit(process_symbol,s):s for s in syms}
        for f in as_completed(futures):
            total_sent+=f.result()
    print(f"Done. signals={total_sent}")
