#!/usr/bin/env python3
"""
MEXC USDT perp only - Doji Star + Star Candle
20EMA, Closed candle, 1D 2D 3D 5D 7D, Bottom/Retest/Liq Sweep/Reversal Zone
Telegram photo alert - FIXED for TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""
import os, json, time, requests
from datetime import datetime, timezone
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://contract.mexc.com"
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT")

TIMEFRAMES = [t.strip().upper() for t in os.getenv("TIMEFRAMES", "1D,2D,3D,5D,7D").split(",")]
TF_DAYS = {"1D":1,"2D":2,"3D":3,"5D":5,"7D":7}
EMA_LEN = int(os.getenv("EMA_LEN","20"))
USE_EMA_FILTER = True
DOJI_BODY_PCT = 0.1

BLOCK = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS'}

def is_valid(sym):
    if not sym.endswith('_USDT'): return False
    base=sym.replace('_USDT','')
    if '_STOCK' in base or 'STOCK' in base: return False
    if 'US30' in base or 'USOIL' in base or 'USTOCK' in base: return False
    if base in BLOCK: return False
    b=base.replace('1000','').replace('10000','')
    if not b.isalpha(): return False
    if len(b)<2 or len(b)>10: return False
    return True

def get_symbols():
    r=requests.get(f"{BASE}/api/v1/contract/detail",timeout=15).json()
    syms=[d['symbol'] for d in r['data'] if is_valid(d['symbol'])]
    print(f"Scanning {len(syms)} USDT perpetual pairs on {', '.join(TIMEFRAMES)}", flush=True)
    return syms

def fetch_1d(symbol):
    try:
        r=requests.get(f"{BASE}/api/v1/contract/kline/{symbol}?interval=Day1",timeout=15).json()
        d=r.get('data',{})
        df=pd.DataFrame({'t':d.get('time',[]),'o':d.get('open',[]),'h':d.get('high',[]),'l':d.get('low',[]),'c':d.get('close',[]),'v':d.get('vol',[])})
        return df.tail(400)
    except: return pd.DataFrame()

def build_tf(df, tf):
    days=TF_DAYS[tf]
    if days==1: return df
    agg=[]
    for i in range(0,len(df),days):
        chunk=df.iloc[i:i+days]
        if len(chunk)<days: continue
        agg.append({'t':chunk.iloc[-1]['t'],'o':chunk.iloc[0]['o'],'h':chunk['h'].max(),'l':chunk['l'].min(),'c':chunk.iloc[-1]['c'],'v':chunk['v'].sum()})
    return pd.DataFrame(agg)

def is_doji(row):
    body=abs(row['c']-row['o'])
    rng=row['h']-row['l']
    return rng>0 and body/rng <= DOJI_BODY_PCT

def detect(df):
    if len(df)<60: return None
    df=df.copy()
    df['ema20']=df['c'].ewm(span=EMA_LEN,adjust=False).mean()
    last=df.iloc[-2] # PREVIOUS CLOSED CANDLE
    p1=df.iloc[-3]
    p2=df.iloc[-4]
    signals=[]
    if is_doji(p1):
        if p1['l']>p2['h']*0.998 and p2['c']>p2['o'] and last['c']<last['o']:
            signals.append("EVENING_DOJI_STAR_BEARISH")
        if p1['h']<p2['l']*1.002 and p2['c']<p2['o'] and last['c']>last['o']:
            signals.append("MORNING_DOJI_STAR_BULLISH")
    small=abs(p1['c']-p1['o'])/(p1['h']-p1['l']+1e-9) <0.3
    if small:
        if p2['c']>p2['o'] and last['c']<last['o'] and p1['h']>p2['h']:
            signals.append("EVENING_STAR_BEARISH")
        if p2['c']<p2['o'] and last['c']>last['o'] and p1['l']<p2['l']:
            signals.append("MORNING_STAR_BULLISH")
    if not signals: return None
    return {"p2":p2,"p1":p1,"last":last,"signals":signals,"df":df}

def confirmations(df,sig):
    conf=[]
    last=sig['last']
    tail=df.tail(50)
    low=tail['l'].min()
    high=tail['h'].max()
    # 1 Candle on bottom/top
    if "MORNING" in sig['signals'][0]:
        if abs(last['l']-low)/low <0.03: conf.append("CANDLE_ON_BOTTOM")
    else:
        if abs(last['h']-high)/high <0.03: conf.append("CANDLE_ON_TOP")
    # 2 Retest EMA20
    if abs(sig['p1']['h']-df.iloc[-3]['ema20'])/df.iloc[-3]['ema20']<0.015 or abs(sig['p1']['l']-df.iloc[-3]['ema20'])/df.iloc[-3]['ema20']<0.015:
        conf.append("RETEST_EMA20")
    # 3 Liquidity sweep
    look=tail.iloc[-20:-3]
    if "MORNING" in sig['signals'][0]:
        if look['l'].min() < low*1.001: conf.append("LIQ_SWEEP_LOW")
    else:
        if look['h'].max() > high*0.999: conf.append("LIQ_SWEEP_HIGH")
    # 4 Reversal zone
    if abs(last['c']-df.iloc[-2]['ema20'])/df.iloc[-2]['ema20']<0.02:
        conf.append("REVERSAL_ZONE")
    return conf

def draw(df,symbol,pat,conf,tf):
    view=df.tail(50)
    fig,ax=plt.subplots(figsize=(12,5),facecolor='black')
    ax.set_facecolor('black')
    for i,(_,r) in enumerate(view.iterrows()):
        col='#089981' if r['c']>=r['o'] else '#f23645'
        ax.plot([i,i],[r['l'],r['h']],color=col,lw=1)
        ax.plot([i,i],[r['o'],r['c']],color=col,lw=4)
    ax.plot(view['ema20'].values,color='#2962FF',lw=1.2,label='EMA20')
    ax.set_title(f"{symbol} {tf} {pat} | {' | '.join(conf)}",color='white',fontweight='bold',fontsize=9)
    ax.legend(facecolor='black',labelcolor='white',fontsize=6)
    ax.grid(alpha=0.15)
    plt.tight_layout()
    path=f"chart_{symbol}_{tf}.png"
    plt.savefig(path,dpi=150,facecolor='black'); plt.close()
    return path

def send_photo(path,caption):
    if not TOKEN or not CHAT:
        print("Missing TOKEN/CHAT secrets", flush=True)
        return
    try:
        with open(path,'rb') as f:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",data={"chat_id":CHAT,"caption":caption,"parse_mode":"Markdown"},files={"photo":f},timeout=20)
    except Exception as e: print(f"send fail {e}", flush=True)

def process(symbol):
    try:
        df1=fetch_1d(symbol)
        if df1.empty or len(df1)<80: return 0
        cnt=0
        for tf in TIMEFRAMES:
            df=build_tf(df1,tf)
            if len(df)<60: continue
            sig=detect(df)
            if not sig: continue
            if USE_EMA_FILTER:
                pat=sig['signals'][0]
                if "BEARISH" in pat and df.iloc[-2]['c'] < df.iloc[-2]['ema20']: continue
                if "BULLISH" in pat and df.iloc[-2]['c'] > df.iloc[-2]['ema20']: continue
            conf=confirmations(df,sig)
            if not conf: continue
            chart=draw(df,symbol,sig['signals'][0],conf,tf)
            cap=f"⭐ *{sig['signals'][0]}* `{tf}`\n*Pair:* `{symbol}`\n*Conf:* `{' | '.join(conf)}`\n*Bottom/Top* ✅ *Retest* ✅ *Liq Sweep* ✅ *Reversal* ✅\n*EMA20:* `{df.iloc[-2]['ema20']:.6f}` *Close:* `{sig['last']['c']:.6f}`\n*{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*"
            send_photo(chart,cap)
            cnt+=1
        return cnt
    except Exception as e:
        print(f"err {symbol} {e}", flush=True)
        return 0

if __name__=="__main__":
    if not TOKEN or not CHAT:
        print(f"ERROR TOKEN exists={bool(TOKEN)} CHAT exists={bool(CHAT)}", flush=True)
        exit(1)
    syms=get_symbols()
    total=0
    with ThreadPoolExecutor(max_workers=20) as ex:
        for f in as_completed({ex.submit(process,s):s for s in syms}):
            total+=f.result()
    print(f"Done signals={total}", flush=True)
