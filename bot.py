import os, ccxt, pandas as pd, requests, json, time, pytz
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("CHAT_ID")
PKT = pytz.timezone('Asia/Karachi')
HIST_FILE = "alerts_history.json"
TIMEFRAMES = ['2h','4h','6h','8h','12h','1d','3d','5d','7d','30d']
FRACTAL_LEN = 7
REQUIRE_BODY = True # Break With Close = True like Pine
COOLDOWN = 24

def send_msg(t):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id":CHAT,"text":t,"parse_mode":"Markdown"}, timeout=20)
def send_photo(p,c):
    with open(p,'rb') as f:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={"chat_id":CHAT,"caption":c,"parse_mode":"Markdown"}, files={"photo":f}, timeout=30)
def load_hist():
    try:
        with open(HIST_FILE,'r') as f: return json.load(f)
    except: return {}
def save_hist(d):
    with open(HIST_FILE,'w') as f: json.dump(d,f)

exchange = ccxt.mexc({'enableRateLimit': True, 'options':{'defaultType':'swap'}})
exchange.load_markets()

def fetch_tf(sym, tf):
    try:
        if tf in ['2h','4h','6h','8h','12h']:
            h=int(tf.replace('h',''))
            ohlcv=exchange.fetch_ohlcv(sym,'1h',limit=600)
            df=pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            df['time']=pd.to_datetime(df['t'], unit='ms', utc=True)
            df.set_index('time', inplace=True)
            df=df.resample(f'{h}h').agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'}).dropna().reset_index()
            return df
        elif tf=='1d':
            ohlcv=exchange.fetch_ohlcv(sym,'1d',limit=400)
            df=pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            df['time']=pd.to_datetime(df['t'], unit='ms', utc=True)
            return df
        else:
            rule={'3d':'3D','5d':'5D','7d':'7D','30d':'30D'}[tf]
            need=900 if tf=='30d' else 500
            ohlcv=exchange.fetch_ohlcv(sym,'1d',limit=need)
            df=pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])
            df['time']=pd.to_datetime(df['t'], unit='ms', utc=True)
            df.set_index('time', inplace=True)
            df=df.resample(rule).agg({'o':'first','h':'max','l':'min','c':'last','v':'sum'}).dropna().reset_index()
            return df
    except: return None

def is_pivot_high(df, idx, length):
    if idx < length or idx >= len(df)-length: return False
    window = df['h'].iloc[idx-length:idx+length+1]
    return df['h'].iloc[idx] == window.max()

def is_pivot_low(df, idx, length):
    if idx < length or idx >= len(df)-length: return False
    window = df['l'].iloc[idx-length:idx+length+1]
    return df['l'].iloc[idx] == window.min()

def detect_choch(df):
    if len(df) < 100: return None
    # State like Pine
    upperLevel = None
    lowerLevel = None
    upperBroken = False
    lowerBroken = False
    marketDirection = 0 # 1 bull, -1 bear
    activeDirection = 0
    choch_events = []
    
    # Loop like Pine structureLeg
    for i in range(FRACTAL_LEN, len(df)-1):
        # Detect new pivots at i
        if is_pivot_high(df, i, FRACTAL_LEN):
            if not upperBroken:
                pass
            upperLevel = float(df['h'].iloc[i])
            upperBroken = False
        if is_pivot_low(df, i, FRACTAL_LEN):
            lowerLevel = float(df['l'].iloc[i])
            lowerBroken = False
        
        if upperLevel is None or lowerLevel is None:
            continue
            
        close = float(df['c'].iloc[i])
        high = float(df['h'].iloc[i])
        low = float(df['l'].iloc[i])
        
        # Break detection - Require Close like your Pine
        breakUp = False
        breakDown = False
        if not upperBroken and upperLevel:
            if REQUIRE_BODY:
                if close > upperLevel: # crossover close
                    # check previous close below
                    if float(df['c'].iloc[i-1]) <= upperLevel:
                        breakUp = True
            else:
                if high > upperLevel:
                    breakUp = True
        
        if not lowerBroken and lowerLevel:
            if REQUIRE_BODY:
                if close < lowerLevel:
                    if float(df['c'].iloc[i-1]) >= lowerLevel:
                        breakDown = True
            else:
                if low < lowerLevel:
                    breakDown = True
        
        if breakUp:
            upperBroken = True
            bullChoch = (marketDirection == -1)
            bullBos = (marketDirection == 1)
            if bullChoch:
                activeDirection = 1
                # CHoCH Box calculation like Pine
                rangeBottom = lowerLevel
                rangeTop = high
                eq = (rangeTop + rangeBottom)/2
                choch_events.append({
                    'type': 'BULL_CHOCH',
                    'bar': i,
                    'price': close,
                    'upper': upperLevel,
                    'lower': lowerLevel,
                    'eq': eq,
                    'rangeTop': rangeTop,
                    'rangeBottom': rangeBottom,
                    'fib_top': rangeBottom + (rangeTop-rangeBottom)*0.618,
                    'fib_bot': rangeBottom + (rangeTop-rangeBottom)*0.382,
                })
            elif bullBos:
                choch_events.append({'type':'BULL_BOS','bar':i,'price':close})
            marketDirection = 1
            
        if breakDown:
            lowerBroken = True
            bearChoch = (marketDirection == 1)
            bearBos = (marketDirection == -1)
            if bearChoch:
                activeDirection = -1
                rangeTop = upperLevel
                rangeBottom = low
                eq = (rangeTop + rangeBottom)/2
                choch_events.append({
                    'type': 'BEAR_CHOCH',
                    'bar': i,
                    'price': close,
                    'upper': upperLevel,
                    'lower': lowerLevel,
                    'eq': eq,
                    'rangeTop': rangeTop,
                    'rangeBottom': rangeBottom,
                    'fib_top': rangeTop - (rangeTop-rangeBottom)*0.382,
                    'fib_bot': rangeTop - (rangeTop-rangeBottom)*0.618,
                })
            elif bearBos:
                choch_events.append({'type':'BEAR_BOS','bar':i,'price':close})
            marketDirection = -1
    
    if not choch_events:
        return None
    last_event = choch_events[-1]
    # Only alert if CHoCH is on last closed candle (or 1 bar ago)
    if last_event['bar'] < len(df)-3: # too old
        return None
    if 'CHOCH' not in last_event['type']:
        return None
    
    return last_event

def draw_choch(df, sym, tf, sig):
    fig, ax = plt.subplots(figsize=(13,6), facecolor='black')
    ax.set_facecolor('black')
    view = df.tail(70).copy()
    x = range(len(view))
    # candles
    for i,(_,r) in enumerate(view.iterrows()):
        col='#089981' if r['c']>=r['o'] else '#f23645'
        ax.plot([i,i],[r['l'],r['h']],color=col,lw=1)
        ax.plot([i,i],[r['o'],r['c']],color=col,lw=4)
    # BLVL levels
    if sig.get('upper'):
        ax.axhline(sig['upper'], color='gray', ls=':', lw=1, label=f"BLVL High {sig['upper']:.5f}")
    if sig.get('lower'):
        ax.axhline(sig['lower'], color='gray', ls=':', lw=1, label=f"BLVL Low {sig['lower']:.5f}")
    # CHoCH box
    if sig['type']=='BULL_CHOCH':
        ax.axhspan(sig['rangeBottom'], sig['rangeTop'], color='#089981', alpha=0.15)
        ax.axhline(sig['eq'], color='#089981', ls='--', lw=1, label=f"EQ {sig['eq']:.5f}")
        # Buy Zone 0.382-0.618
        ax.axhspan(sig['fib_bot'], sig['fib_top'], color='#089981', alpha=0.35, label="Buy Zone 0.382-0.618")
        # Targets
        rng = sig['rangeTop']-sig['rangeBottom']
        ax.axhline(sig['rangeBottom']+rng*1.55, color='#089981', ls='--', alpha=0.5)
        ax.axhline(sig['rangeBottom']+rng*2.68, color='#089981', ls='--', alpha=0.5)
    else:
        ax.axhspan(sig['rangeBottom'], sig['rangeTop'], color='#f23645', alpha=0.15)
        ax.axhline(sig['eq'], color='#f23645', ls='--', lw=1, label=f"EQ {sig['eq']:.5f}")
        ax.axhspan(sig['fib_bot'], sig['fib_top'], color='#f23645', alpha=0.35, label="Sell Zone 0.382-0.618")
    
    last_idx = len(view)-1
    ax.scatter(last_idx, sig['price'], color='white', s=100, zorder=5)
    ax.text(last_idx, sig['price'], f" {sig['type']}", color='white', weight='bold', fontsize=9)
    
    ax.set_title(f"{sym} {tf} | {sig['type']} | Price {sig['price']:.6f} | PKT {datetime.now(PKT).strftime('%H:%M')}", color='white')
    ax.legend(facecolor='black', labelcolor='white', fontsize=7)
    ax.grid(alpha=0.1)
    plt.tight_layout()
    path=f"chart_{sym.replace('/','')}_{tf}.png"
    plt.savefig(path,dpi=150,facecolor='black')
    plt.close()
    return path

hist=load_hist()
syms=[s for s in exchange.markets if s.endswith('/USDT:USDT')]
try:
    tickers=exchange.fetch_tickers()
    syms=[s for s in syms if tickers.get(s,{}).get('quoteVolume',0)>400_000]
except: pass
syms=syms[:120]
found=0
now=datetime.now(PKT)

for sym in syms:
    if sym in hist and time.time()-hist[sym] < COOLDOWN*3600: continue
    for tf in TIMEFRAMES:
        df=fetch_tf(sym,tf)
        if df is None or len(df)<100: continue
        sig=detect_choch(df)
        if not sig: continue
        
        chart=draw_choch(df,sym,tf,sig)
        if 'BULL' in sig['type']:
            cap=f"🟢 *{sig['type']}*\n\n*Pair:* {sym}\n*TF:* {tf} Closed\n*CHoCH Price:* `{sig['price']:.6f}`\n*BLVL Break:* `{sig['upper']:.6f}`\n*EQ:* `{sig['eq']:.6f}`\n*Buy Zone:* `{sig['fib_bot']:.6f}` - `{sig['fib_top']:.6f}`\n*SL:* Below `{sig['rangeBottom']:.6f}`\n*Target 1.55:* `{(sig['rangeBottom'] + (sig['rangeTop']-sig['rangeBottom'])*1.55):.6f}`\n*Time PKT:* {now.strftime('%Y-%m-%d %I:%M %p')}"
        else:
            cap=f"🔴 *{sig['type']}*\n\n*Pair:* {sym}\n*TF:* {tf} Closed\n*CHoCH Price:* `{sig['price']:.6f}`\n*BLVL Break:* `{sig['lower']:.6f}`\n*EQ:* `{sig['eq']:.6f}`\n*Sell Zone:* `{sig['fib_bot']:.6f}` - `{sig['fib_top']:.6f}`\n*SL:* Above `{sig['rangeTop']:.6f}`\n*Time PKT:* {now.strftime('%Y-%m-%d %I:%M %p')}"
        
        send_photo(chart,cap)
        hist[sym]=time.time()
        save_hist(hist)
        found+=1
        time.sleep(1)
        break

if found==0:
    send_msg("*please patience* No CHoCH - Scan done")
