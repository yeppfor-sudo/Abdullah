#!/usr/bin/env python3
"""
MEXC USDT crypto perp only - NO ETF/metal/mega/stock
Condition: Red RSI20 < 20
TF: 4H 8H 12H 1D 3D 5D 7D - closed candle
Telegram photo
"""
import os, requests, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE = "https://contract.mexc.com"
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAMES = ["4H","8H","12H","1D","3D","5D","7D"]
TF_MAP = {"4H":"Hour4","8H":"Hour8","12H":"Hour12","1D":"Day1","3D":"Day1","5D":"Day1","7D":"Day1"}
TF_AGG = {"4H":1,"8H":1,"12H":1,"1D":1,"3D":3,"5D":5,"7D":7}
RSI_THRESHOLD = 20

BLOCK_WORDS = ['STOCK','US30','USOIL','ETF','METAL','GOLD','SILVER','MEGA','BULL','BEAR','3L','3S','5L','5S']
BLOCK_COINS = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS'}

def is_valid(sym):
    if not sym.endswith('_USDT'): return False
    base=sym.replace('_USDT','').upper()
    for w in BLOCK_WORDS:
        if w in base: return False
    if base in BLOCK_COINS: return False
    clean=base.replace('1000','').replace('10000','')
    if not clean.isalpha(): return False
    if len(clean)<2 or len(clean)>10: return False
    return True

def get_symbols():
    r=requests.get(f"{BASE}/api/v1/contract/detail",timeout=15).json()
    syms=[d['symbol'] for d in r['data'] if is_valid(d['symbol'])]
    print(f"Scanning {len(syms)} USDT crypto perp only", flush=True)
    return syms

def fetch_kline(symbol, interval):
    try:
        r=requests.get(f"{BASE}/api/v1/contract/kline/{symbol}?interval={interval}",timeout=15).json()
        d=r.get('data',{})
        df=pd.DataFrame({'t':d.get('time',[]),'o':d.get('open',[]),'h':d.get('high',[]),'l':d.get('low',[]),'c':d.get('close',[]),'v':d.get('vol',[])})
        return df.tail(500)
    except: return pd.DataFrame()

def rsi(series, period=20):
    delta=series.diff()
    gain=delta.clip(lower=0)
    loss=-delta.clip(upper=0)
    avg_gain=gain.ewm(com=period-1, adjust=False, min_periods=period).mean()
    avg_loss=loss.ewm(com=period-1, adjust=False, min_periods=period).mean()
    rs=avg_gain/avg_loss
    return 100 - (100/(1+rs))

def build_agg(df, tf):
    days=TF_AGG[tf]
    if days==1: return df
    agg=[]
    for i in range(0,len(df),days):
        chunk=df.iloc[i:i+days]
        if len(chunk)<days: continue
        agg.append({'t':chunk.iloc[-1]['t'],'o':chunk.iloc[0]['o'],'h':chunk['h'].max(),'l':chunk['l'].min(),'c':chunk.iloc[-1]['c'],'v':chunk['v'].sum()})
    return pd.DataFrame(agg)

def draw(df, symbol, tf, r20):
    view=df.tail(100)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12,5), facecolor='black', gridspec_kw={'height_ratios':[3,1]}, sharex=True)
    ax1.set_facecolor('black')
    ax2.set_facecolor('black')
    for i,(_,row) in enumerate(view.iterrows()):
        col='#089981' if row['c']>=row['o'] else '#f23645'
        ax1.plot([i,i],[row['l'],row['h']],color=col,lw=1)
        ax1.plot([i,i],[row['o'],row['c']],color=col,lw=4)
    ax1.plot(view['ema20'].values,color='#2962FF',lw=1.2,label='EMA20')
    ax1.set_title(f"{symbol} {tf} RSI20 {r20:.2f} <20 OVERSOLD",color='white',fontweight='bold',fontsize=11)
    ax1.legend(facecolor='black',labelcolor='white',fontsize=6)
    ax1.grid(alpha=0.15)
    # ONLY RED RSI20
    ax2.plot(view['rsi20'].values, color='#FF0000', lw=1.8, label=f"RSI20:{r20:.2f}")
    ax2.axhline(20,color='#FF0000',ls='--',lw=0.8)
    ax2.axhline(70,color='gray',ls='--',lw=0.5)
    ax2.axhline(30,color='gray',ls='--',lw=0.5)
    ax2.set_ylim(0,100)
    ax2.legend(facecolor='black',labelcolor='white',fontsize=8, loc='upper left')
    ax2.grid(alpha=0.15)
    plt.tight_layout()
    path=f"chart_{symbol}_{tf}.png"
    plt.savefig(path,dpi=150,facecolor='black'); plt.close()
    return path

def send_photo(path, caption):
    try:
        with open(path,'rb') as f:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",data={"chat_id":CHAT,"caption":caption,"parse_mode":"Markdown"},files={"photo":f},timeout=20)
    except Exception as e: print(f"send fail {e}", flush=True)

def process(symbol):
    found=0
    for tf in TIMEFRAMES:
        try:
            interval=TF_MAP[tf]
            df=fetch_kline(symbol, interval)
            if df.empty or len(df)<80: continue
            df=build_agg(df, tf)
            if len(df)<80: continue
            df['ema20']=df['c'].ewm(span=20,adjust=False).mean()
            df['rsi20']=rsi(df['c'],20)
            last=df.iloc[-2]
            r20=float(last['rsi20'])
            if r20 >= RSI_THRESHOLD: continue
            print(f"ALERT {symbol} {tf} RSI20 {r20:.2f}", flush=True)
            chart=draw(df, symbol, tf, r20)
            cap=f"🔴 *RSI20 <20* `{tf}`\n*Pair:* `{symbol}`\n*RSI20 Red:* `{r20:.2f}` *<20*\n*Price:* `{float(last['c']):.6f}` *EMA20:* `{float(last['ema20']):.6f}`\n*{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*"
            send_photo(chart, cap)
            found+=1
        except Exception as e:
            print(f"err {symbol} {tf} {e}", flush=True)
    return found

if __name__=="__main__":
    syms=get_symbols()
    total=0
    with ThreadPoolExecutor(max_workers=15) as ex:
        for f in as_completed({ex.submit(process,s):s for s in syms}):
            total+=f.result()
    print(f"Done total={total}", flush=True)
