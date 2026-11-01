#!/usr/bin/env python3
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
EMA_LEN = 20

BLOCK = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS'}

def is_valid(sym):
    if not sym.endswith('_USDT'): return False
    base=sym.replace('_USDT','')
    if '_STOCK' in base or 'STOCK' in base or 'US30' in base or 'USOIL' in base: return False
    if base in BLOCK: return False
    b=base.replace('1000','').replace('10000','')
    if not b.isalpha(): return False
    if len(b)<2 or len(b)>10: return False
    return True

def get_symbols():
    r=requests.get(f"{BASE}/api/v1/contract/detail",timeout=15).json()
    syms=[d['symbol'] for d in r['data'] if is_valid(d['symbol'])]
    print(f"Scanning {len(syms)} USDT perpetual pairs", flush=True)
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

def rsi(series, period):
    delta=series.diff()
    gain=delta.clip(lower=0)
    loss=-delta.clip(upper=0)
    avg_gain=gain.ewm(com=period-1, adjust=False, min_periods=period).mean()
    avg_loss=loss.ewm(com=period-1, adjust=False, min_periods=period).mean()
    rs=avg_gain/avg_loss
    return 100 - (100/(1+rs))

def is_doji(row):
    body=abs(row['c']-row['o'])
    rng=row['h']-row['l']
    return rng>0 and body/rng <= 0.1

def detect(df):
    if len(df)<80: return None
    df=df.copy()
    df['ema20']=df['c'].ewm(span=EMA_LEN,adjust=False).mean()
    df['rsi20']=rsi(df['c'],20)
    df['rsi40']=rsi(df['c'],40)
    df['rsi60']=rsi(df['c'],60)
    last=df.iloc[-2]
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

def confirmations(sig):
    df=sig['df']
    last=sig['last']
    tail=df.tail(50)
    low=tail['l'].min()
    high=tail['h'].max()
    conf=[]
    if "MORNING" in sig['signals'][0]:
        if abs(last['l']-low)/low <0.05: conf.append("CANDLE_ON_BOTTOM")
    else:
        if abs(last['h']-high)/high <0.05: conf.append("CANDLE_ON_TOP")
    try:
        if abs(sig['p1']['h']-df.iloc[-3]['ema20'])/df.iloc[-3]['ema20']<0.02 or abs(sig['p1']['l']-df.iloc[-3]['ema20'])/df.iloc[-3]['ema20']<0.02:
            conf.append("RETEST_EMA20")
    except: pass
    look=tail.iloc[-20:-3]
    if "MORNING" in sig['signals'][0]:
        if look['l'].min() < low*1.005: conf.append("LIQ_SWEEP_LOW")
    else:
        if look['h'].max() > high*0.995: conf.append("LIQ_SWEEP_HIGH")
    if abs(last['c']-df.iloc[-2]['ema20'])/df.iloc[-2]['ema20']<0.03:
        conf.append("REVERSAL_ZONE")
    return conf

def draw(df,symbol,pat,conf,tf):
    view=df.tail(80)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12,6), facecolor='black', gridspec_kw={'height_ratios':[3,1]}, sharex=True)
    ax1.set_facecolor('black')
    ax2.set_facecolor('black')
    for i,(_,r) in enumerate(view.iterrows()):
        col='#089981' if r['c']>=r['o'] else '#f23645'
        ax1.plot([i,i],[r['l'],r['h']],color=col,lw=1)
        ax1.plot([i,i],[r['o'],r['c']],color=col,lw=4)
    ax1.plot(view['ema20'].values,color='#2962FF',lw=1.2,label='EMA20')
    ax1.set_title(f"{symbol} {tf} {pat} | {' | '.join(conf)}",color='white',fontsize=10,fontweight='bold')
    ax1.legend(facecolor='black',labelcolor='white',fontsize=6)
    ax1.grid(alpha=0.15)
    # EDITED: RSI20 Red, RSI40 Blue, RSI60 Yellow
    ax2.plot(view['rsi20'].values, color='#FF0000', lw=1.2, label=f"RSI20:{view['rsi20'].iloc[-2]:.2f}")
    ax2.plot(view['rsi40'].values, color='#0080FF', lw=1.2, label=f"RSI40:{view['rsi40'].iloc[-2]:.2f}")
    ax2.plot(view['rsi60'].values, color='#FFD700', lw=1.2, label=f"RSI60:{view['rsi60'].iloc[-2]:.2f}")
    ax2.axhline(70,color='gray',ls='--',lw=0.5)
    ax2.axhline(30,color='gray',ls='--',lw=0.5)
    ax2.set_ylim(0,100)
    ax2.legend(facecolor='black',labelcolor='white',fontsize=7, loc='upper left')
    ax2.grid(alpha=0.15)
    plt.tight_layout()
    path=f"chart_{symbol}_{tf}.png"
    plt.savefig(path,dpi=150,facecolor='black'); plt.close()
    return path

def send_photo(path,caption):
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
            df_raw=build_tf(df1,tf)
            if len(df_raw)<80: continue
            sig=detect(df_raw)
            if not sig: continue
            pat=sig['signals'][0]
            if "BEARISH" in pat and sig['df'].iloc[-2]['c'] < sig['df'].iloc[-2]['ema20']: continue
            if "BULLISH" in pat and sig['df'].iloc[-2]['c'] > sig['df'].iloc[-2]['ema20']: continue
            conf=confirmations(sig)
            if not conf: continue
            chart=draw(sig['df'],symbol,pat,conf,tf)
            r20=sig['df'].iloc[-2]['rsi20']
            r40=sig['df'].iloc[-2]['rsi40']
            r60=sig['df'].iloc[-2]['rsi60']
            cap=f"⭐ *{pat}* `{tf}`\n*Pair:* `{symbol}`\n*Conf:* `{' | '.join(conf)}`\n*RSI20:{r20:.2f} RSI40:{r40:.2f} RSI60:{r60:.2f}*\n*EMA20:* `{sig['df'].iloc[-2]['ema20']:.6f}`"
            send_photo(chart,cap)
            cnt+=1
        return cnt
    except Exception as e:
        print(f"err {symbol} {e}", flush=True)
        return 0

if __name__=="__main__":
    syms=get_symbols()
    total=0
    with ThreadPoolExecutor(max_workers=20) as ex:
        for f in as_completed({ex.submit(process,s):s for s in syms}):
            total+=f.result()
    print(f"Done signals={total}", flush=True)
