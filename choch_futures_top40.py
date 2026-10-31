import os, ccxt, pandas as pd, requests, json, time, pytz
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("CHAT_ID")
PKT = pytz.timezone('Asia/Karachi')
HIST_FILE = "alerts_history.json"
FRACTAL_LEN = 5
COOLDOWN = 24
TOP_GAINERS = 40
TIMEFRAMES = ['10m', '15m']

BLOCK = {'BTC','ETH','SOL','BNB','TRX','XRP','ADA','DOGE','AVAX','DOT','LINK','LTC','BCH','XLM','ETC','HBAR','APT','NEAR','UNI','FIL','ARB','OP','ATOM','MATIC','POL','TON','SHIB','PEPE','CRO','LEO','OKB','BGB','SUI','LDO','RNDR','TAO','FET','WIF','ENA','JUP','SEI','TIA','ONDO','W','PYTH','STRK','JTO','ENS','GOLD','SILVER','XAU','XAG','PAXG','OIL','STOCK','ETF','USDC','FDUSD','TRY','EUR','JPY'}

def is_futures_perp_crypto(sym):
    if not sym.endswith('USDT:USDT'): return False
    base = sym.split('/')[0].upper()
    if base in BLOCK: return False
    if len(base)<2 or len(base)>8: return False
    if any(c.isdigit() for c in base): return False
    if not base.isalpha(): return False
    return True

def send_photo(p,c):
    try:
        with open(p,'rb') as f: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data={"chat_id":CHAT,"caption":c,"parse_mode":"Markdown"}, files={"photo":f}, timeout=30)
    except Exception as e: print(f"Photo fail {e}", flush=True)

def load_hist():
    try: return json.load(open(HIST_FILE))
    except: return {}
def save_hist(d): json.dump(d, open(HIST_FILE,'w'))

def is_pivot_high(df,i,l): 
    if i<l or i>=len(df)-l: return False
    return df['h'].iloc[i]==df['h'].iloc[i-l:i+l+1].max()
def is_pivot_low(df,i,l):
    if i<l or i>=len(df)-l: return False
    return df['l'].iloc[i]==df['l'].iloc[i-l:i+l+1].min()

def detect_bearish_choch(df):
    if len(df)<100: return None
    upper=lower=None; direction=0; events=[]; upB=lowB=False
    for i in range(FRACTAL_LEN,len(df)-1):
        if is_pivot_high(df,i,FRACTAL_LEN): upper=float(df['h'].iloc[i]); upB=False
        if is_pivot_low(df,i,FRACTAL_LEN): lower=float(df['l'].iloc[i]); lowB=False
        if upper is None or lower is None: continue
        c=float(df['c'].iloc[i]); pc=float(df['c'].iloc[i-1])
        if not lowB and c<lower and pc>=lower:
            lowB=True
            if direction==1:
                lb=float(df['l'].iloc[i])
                events.append({'price':c,'upper':upper,'lower':lower,'rangeTop':upper,'rangeBottom':lb,'fib_top':upper-(upper-lb)*0.382,'fib_bot':upper-(upper-lb)*0.618,'bar':i,'eq':(upper+lb)/2})
            direction=-1
        if not upB and c>upper and pc<=upper: upB=True; direction=1
    if not events: return None
    if events[-1]['bar']<len(df)-4: return None
    return events[-1]

def fetch_tf(ex,sym,tf):
    try:
        if tf=='10m':
            o=ex.fetch_ohlcv(sym,'5m',limit=160)
            df=pd.DataFrame(o,columns=['t','o','h','l','c','v'])
            df['time']=pd.to_datetime(df['t'],unit='ms',utc=True)
            df.set_index('time',inplace=True)
            df=df.resample('10min').agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'}).dropna().reset_index()
            return df
        else:
            o=ex.fetch_ohlcv(sym,'15m',limit=200)
            return pd.DataFrame(o,columns=['t','o','h','l','c','v'])
    except: return None

def draw(df,sym,sig,exn,tf,gain):
    fig,ax=plt.subplots(figsize=(14,6),facecolor='black'); ax.set_facecolor('black')
    view=df.tail(80); live=float(view['c'].iloc[-1])
    for i,(_,r) in enumerate(view.iterrows()):
        col='#089981' if r['c']>=r['o'] else '#f23645'
        ax.plot([i,i],[r['l'],r['h']],color=col,lw=1); ax.plot([i,i],[r['o'],r['c']],color=col,lw=4)
    ax.axhline(sig['lower'],color='white',ls='--',lw=1.2,label=f"BREAK {sig['lower']:.6f}")
    ax.axhspan(sig['fib_bot'],sig['fib_top'],color='#f23645',alpha=0.45,label="SELL ZONE")
    ax.axhline(live,color='yellow',lw=1.2,label=f"LIVE {live:.6f}")
    ax.set_title(f"{sym} MEXC {tf} BEAR CHoCH | 24h +{gain:.1f}% | FUT PERP",color='white',fontweight='bold')
    ax.legend(facecolor='black',labelcolor='white',fontsize=7); ax.grid(alpha=0.15); plt.tight_layout()
    p=f"chart_{sym.replace('/','').replace(':','')}_{tf}.png"; plt.savefig(p,dpi=150,facecolor='black'); plt.close(); return p,live

hist=load_hist()
now=datetime.now(PKT)
found=0

print("Starting MEXC FAST scan...", flush=True)
ex=ccxt.mexc({'enableRateLimit':True,'options':{'defaultType':'swap'}})
ex.load_markets()

print("Fetching tickers - 1 call for all 759 pairs...", flush=True)
tickers = ex.fetch_tickers()
print(f"Got {len(tickers)} tickers", flush=True)

gainers=[]
for sym,t in tickers.items():
    if not is_futures_perp_crypto(sym): continue
    # use 24h change from ticker - INSTANT
    try:
        ch = t.get('percentage')
        if ch is None: 
            # fallback to change
            ch = t.get('info',{}).get('riseFallRate')
            if ch: ch = float(ch)*100
        if ch is None: continue
        if ch>0:
            gainers.append((sym,float(ch)))
    except: continue

gainers.sort(key=lambda x:x[1],reverse=True)
top=gainers[:TOP_GAINERS]

top_display=[]
for s,c in top[:10]:
    coin=s.split('/')[0]
    top_display.append(f"{coin} {c:.1f}%")
print(f"MEXC TOP {TOP_GAINERS} FUT PERP: {top_display}", flush=True)

for sym,gain in top:
    coin_name=sym.split('/')[0].upper()
    if coin_name in hist and time.time()-hist[coin_name]<COOLDOWN*3600:
        print(f"Skip {coin_name} - sent in 24h", flush=True)
        continue
    for tf in TIMEFRAMES:
        print(f"Checking {coin_name} {tf} +{gain:.1f}%", flush=True)
        df=fetch_tf(ex,sym,tf)
        if df is None or len(df)<100: 
            print(f"  No data {coin_name}", flush=True)
            continue
        sig=detect_bearish_choch(df)
        if not sig: 
            print(f"  No CHoCH {coin_name} {tf}", flush=True)
            continue
        chart,live=draw(df,sym,sig,'MEXC',tf,gain)
        cap=f"🔴 *BEAR CHoCH {tf} FUT PERP*\n\n*Pair:* `{sym}` | MEXC\n*24h:* `+{gain:.2f}%` TOP {TOP_GAINERS} FUTURES\n*Break:* `{sig['lower']:.6f}`\n*Sell Zone:* `{sig['fib_bot']:.6f}`-`{sig['fib_top']:.6f}`\n*SL:* `{sig['rangeTop']:.6f}`\n*Live:* `{live:.6f}`"
        send_photo(chart,cap)
        hist[coin_name]=time.time()
        save_hist(hist)
        found+=1
        print(f"ALERT SENT {coin_name}", flush=True)
        time.sleep(0.5)
        break

print(f"Done. Found {found} | Total time <60s", flush=True)
