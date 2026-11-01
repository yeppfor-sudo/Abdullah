#!/usr/bin/env python3
"""
MEXC perpetual (USDT only) scanner
Patterns : Doji star + Star candle (evening / morning)
Filter   : EMA 20 trend filter
Candles  : last fully CLOSED candle only
Timeframes: 1D, 2D, 3D, 5D, 7D  (2D/3D/5D/7D are built from 1D candles)
Alert    : chart photo sent to Telegram
"""
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import requests

# ----------------------------- CONFIG ---------------------------------
BASE = "https://contract.mexc.com"

TIMEFRAMES = [t.strip().upper() for t in os.getenv("TIMEFRAMES", "1D,2D,3D,5D,7D").split(",")]
TF_DAYS = {"1D": 1, "2D": 2, "3D": 3, "5D": 5, "7D": 7}

EMA_LEN = int(os.getenv("EMA_LEN", "20"))
USE_EMA_FILTER = os.getenv("USE_EMA_FILTER", "true").lower() == "true"

DOJI_BODY_PCT = float(os.getenv("DOJI_BODY_PCT", "0.10"))      # doji: body <= 10% of candle range
STAR_BODY_PCT = float(os.getenv("STAR_BODY_PCT", "0.30"))      # star: body <= 30% of candle range
STAR_VS_PRIOR = float(os.getenv("STAR_VS_PRIOR", "0.50"))      # star body <= 50% of prior body
PRIOR_BODY_PCT = float(os.getenv("PRIOR_BODY_PCT", "0.55"))    # prior candle body >= 55% of its range
PRIOR_VS_AVG = float(os.getenv("PRIOR_VS_AVG", "1.0"))         # prior body >= 1.0 x avg body (10 candles)
GAP_TOL = float(os.getenv("GAP_TOL", "0.15"))                  # allowed overlap with prior body (fraction)

MAX_CANDLE_AGE_HOURS = float(os.getenv("MAX_CANDLE_AGE_HOURS", "12"))  # ignore stale signals
CHART_BARS = int(os.getenv("CHART_BARS", "60"))
DAYS_BACK = int(os.getenv("DAYS_BACK", "800"))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
MIN_INTERVAL = float(os.getenv("MIN_INTERVAL", "0.15"))        # seconds between API calls
EXCLUDE = {s.strip().upper() for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s.strip()}

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
STATE_FILE = os.getenv("STATE_FILE", "state/sent.json")

# Monday 2019-12-30 00:00 UTC. 2D/3D/5D/7D candles are built in fixed blocks from here.
ANCHOR_TS = 1577664000

# ----------------------------- HTTP -----------------------------------
_lock = threading.Lock()
_last_call = [0.0]


def _throttle():
    with _lock:
        wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def api_get(path, params=None, tries=4):
    for i in range(tries):
        _throttle()
        try:
            r = requests.get(BASE + path, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            j = r.json()
            if j.get("success") is False:
                raise RuntimeError(str(j)[:200])
            return j.get("data")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("request failed")


# Non-crypto words that MEXC uses in concept tags / names (stocks, ETFs, metals, forex, commodities)
NON_CRYPTO_WORDS = (
    "stock", "equity", "equities", "etf", "metal", "commodit", "forex", "fx",
    "tradfi", "index future", "gold", "silver", "oil", "energy", "nasdaq", "s&p",
)

# Known non-crypto base tickers (metals, energy, big stocks and ETFs). Add more with EXCLUDE_SYMBOLS.
NON_CRYPTO_BASES = {
    "XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM",
    "WTI", "BRENT", "USOIL", "UKOIL", "OIL", "NATGAS", "NGAS",
    "SPX", "SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "GLD", "SLV", "USO", "TLT", "ARKK",
    "NAS100", "US30", "US500", "DXY", "VIX",
    "TSLA", "AAPL", "NVDA", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NFLX", "AMD", "INTC",
    "COIN", "MSTR", "HOOD", "PLTR", "BABA", "TSM", "ORCL", "UBER", "DIS", "PYPL",
    "SQ", "SHOP", "CRCL", "GME", "AMC", "BA", "JPM", "V", "MA", "WMT", "KO", "NKE",
}


def _is_crypto(c):
    """Return (True, '') for crypto perpetuals, else (False, reason)."""
    base = str(c.get("baseCoin", "")).upper()
    if base in NON_CRYPTO_BASES:
        return False, "known non-crypto ticker"
    # only the concept tags are checked (never the coin name, to avoid false hits like GOLDEN or TOIL)
    tags = " ".join(str(x) for x in (c.get("conceptPlate") or [])).lower()
    for w in NON_CRYPTO_WORDS:
        if w in tags:
            return False, f"tag contains '{w}'"
    return True, ""


def list_symbols():
    data = api_get("/api/v1/contract/detail")
    if isinstance(data, dict):
        data = [data]
    out, skipped = [], []
    for c in data:
        sym = c.get("symbol", "")
        # USDT-margined, active, perpetual only
        if c.get("quoteCoin") != "USDT" or not sym.endswith("_USDT"):
            continue
        if c.get("state", 0) != 0 or c.get("futureType", 1) != 1:
            continue
        if sym.upper() in EXCLUDE:
            skipped.append((sym, "EXCLUDE_SYMBOLS"))
            continue
        ok, why = _is_crypto(c)
        if ok:
            out.append(sym)
        else:
            skipped.append((sym, why))
    if skipped:
        print(f"Skipped {len(skipped)} non-crypto contracts: " + ", ".join(s for s, _ in sorted(skipped)))
    return sorted(out)


def fetch_daily(sym):
    """Closed daily candles only: (ts, open, high, low, close)."""
    now = int(time.time())
    d = api_get(
        f"/api/v1/contract/kline/{sym}",
        {"interval": "Day1", "start": now - DAYS_BACK * 86400, "end": now},
    )
    if not d or not d.get("time"):
        return []
    rows = sorted(
        zip(d["time"], d["open"], d["high"], d["low"], d["close"]),
        key=lambda r: r[0],
    )
    # drop the candle that is still open
    return [r for r in rows if r[0] + 86400 <= now]


# --------------------------- CANDLES / EMA ----------------------------
def resample(daily, n):
    """Return closed candles: (ts, o, h, l, c, close_ts). Incomplete blocks are dropped."""
    if n == 1:
        return [(t, o, h, l, c, t + 86400) for t, o, h, l, c in daily]
    groups, order = {}, []
    for row in daily:
        k = ((row[0] - ANCHOR_TS) // 86400) // n
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)
    out = []
    for k in order:
        g = groups[k]
        if len(g) != n:
            continue
        out.append((
            g[0][0],
            g[0][1],
            max(x[2] for x in g),
            min(x[3] for x in g),
            g[-1][4],
            g[0][0] + n * 86400,
        ))
    return out


def ema(values, n):
    out = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    out[n - 1] = e
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


# ----------------------------- PATTERNS -------------------------------
def detect(c, e):
    """Check the last closed candle (c[-1]) as the star, c[-2] as the prior candle."""
    if len(c) < EMA_LEN + 14:
        return None
    i = len(c) - 1
    _, o, h, l, cl, _ = c[i]
    _, po, ph, pl, pc, _ = c[i - 1]
    rng, prng = h - l, ph - pl
    if rng <= 0 or prng <= 0:
        return None

    body, pbody = abs(cl - o), abs(pc - po)
    avg_body = sum(abs(x[4] - x[1]) for x in c[i - 11:i - 1]) / 10

    # strong prior candle
    if pbody / prng < PRIOR_BODY_PCT or pbody < PRIOR_VS_AVG * avg_body:
        return None
    # small star body
    if body > STAR_BODY_PCT * rng or body > STAR_VS_PRIOR * pbody:
        return None

    kind = "Doji star" if body <= DOJI_BODY_PCT * rng else "Star candle"
    s_lo, s_hi = min(o, cl), max(o, cl)
    tol = GAP_TOL * pbody
    ev = e[i]
    if USE_EMA_FILTER and ev is None:
        return None

    if pc > po and s_lo >= pc - tol:          # star above a bullish candle
        if USE_EMA_FILTER and not cl > ev:
            return None
        return {"name": f"Evening {kind.lower()}", "kind": kind, "bias": "bearish"}
    if pc < po and s_hi <= pc + tol:          # star below a bearish candle
        if USE_EMA_FILTER and not cl < ev:
            return None
        return {"name": f"Morning {kind.lower()}", "kind": kind, "bias": "bullish"}
    return None


# ------------------------------ CHART ---------------------------------
def fmt(v):
    return f"{v:.8g}"


def make_chart(sym, tf, cs, es, sig):
    cs, es = cs[-CHART_BARS:], es[-CHART_BARS:]
    bg, up, dn = "#0f1419", "#26a69a", "#ef5350"
    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    for x, (_, o, h, l, c, _) in enumerate(cs):
        col = up if c >= o else dn
        ax.plot([x, x], [l, h], color=col, lw=1.2, zorder=2)
        bl, bh = min(o, c), max(o, c)
        ht = max(bh - bl, (h - l) * 0.006)
        ax.add_patch(Rectangle((x - 0.35, bl), 0.7, ht, facecolor=col, edgecolor=col, zorder=3))

    xs = [i for i, v in enumerate(es) if v is not None]
    ax.plot(xs, [es[i] for i in xs], color="#ffb300", lw=1.6, zorder=4, label=f"EMA {EMA_LEN}")

    last = len(cs) - 1
    ax.axvspan(last - 1.5, last + 0.5, color="white", alpha=0.07, zorder=1)
    ax.annotate(
        sig["kind"], (last, cs[-1][2]), xytext=(0, 22), textcoords="offset points",
        ha="center", color="white", fontsize=10,
        arrowprops=dict(arrowstyle="->", color="white"),
    )

    step = max(1, len(cs) // 6)
    ticks = list(range(0, len(cs), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [datetime.fromtimestamp(cs[t][0], timezone.utc).strftime("%d %b") for t in ticks],
        color="#9aa4ae", fontsize=9,
    )
    ax.set_xlim(-1, last + 3)
    ax.yaxis.tick_right()
    ax.tick_params(axis="y", colors="#9aa4ae", labelsize=9)
    ax.grid(color="#1e2630", lw=0.6)
    for s in ax.spines.values():
        s.set_color("#1e2630")
    ax.set_title(f"MEXC {sym} Perp  |  {tf}  |  {sig['name']}", color="white", fontsize=13, loc="left")
    leg = ax.legend(loc="upper left", facecolor=bg, edgecolor="#1e2630")
    for t in leg.get_texts():
        t.set_color("#cfd6dd")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ----------------------------- TELEGRAM -------------------------------
def send_photo(png, caption):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
        data={"chat_id": TG_CHAT, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("chart.png", png, "image/png")},
        timeout=60,
    )
    r.raise_for_status()


def caption_for(hit):
    sig, cs, es = hit["sig"], hit["candles"], hit["ema"]
    icon = "🔴" if sig["bias"] == "bearish" else "🟢"
    closed = datetime.fromtimestamp(cs[-1][5], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"{icon} <b>{hit['sym']}</b> · {hit['tf']}\n"
        f"Pattern: <b>{sig['name']}</b> ({sig['bias']})\n"
        f"Close: {fmt(cs[-1][4])} | EMA{EMA_LEN}: {fmt(es[-1])}\n"
        f"Candle closed: {closed}\n"
        f"https://futures.mexc.com/exchange/{hit['sym']}"
    )


# ------------------------------ STATE ---------------------------------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    cutoff = time.time() - 45 * 86400
    state = {k: v for k, v in state.items() if v > cutoff}
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)


# ------------------------------- MAIN ---------------------------------
def scan_symbol(sym):
    daily = fetch_daily(sym)
    if len(daily) < 30:
        return []
    hits = []
    now = time.time()
    for tf in TIMEFRAMES:
        n = TF_DAYS.get(tf)
        if not n:
            continue
        cs = resample(daily, n)
        if len(cs) < EMA_LEN + 14:
            continue
        es = ema([x[4] for x in cs], EMA_LEN)
        sig = detect(cs, es)
        if not sig:
            continue
        age_h = (now - cs[-1][5]) / 3600
        if age_h < 0 or age_h > MAX_CANDLE_AGE_HOURS:
            continue
        hits.append({"sym": sym, "tf": tf, "candles": cs, "ema": es, "sig": sig})
    return hits


def main():
    if not DRY_RUN and not (TG_TOKEN and TG_CHAT):
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or DRY_RUN=true).")

    symbols = list_symbols()
    print(f"Scanning {len(symbols)} USDT perpetual pairs on {', '.join(TIMEFRAMES)}")

    hits, errors = [], 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(scan_symbol, s): s for s in symbols}
        for f in as_completed(futs):
            try:
                hits.extend(f.result())
            except Exception as e:
                errors += 1
                print(f"  ! {futs[f]}: {e}")

    hits.sort(key=lambda h: (h["sym"], TF_DAYS[h["tf"]]))
    state = load_state()
    sent = 0
    for h in hits:
        key = f"{h['sym']}|{h['tf']}|{h['candles'][-1][0]}|{h['sig']['name']}"
        if key in state:
            continue
        print(f"SIGNAL {h['sym']} {h['tf']} {h['sig']['name']}")
        if DRY_RUN:
            continue
        try:
            png = make_chart(h["sym"], h["tf"], h["candles"], h["ema"], h["sig"])
            send_photo(png, caption_for(h))
            state[key] = time.time()
            sent += 1
            time.sleep(1.2)
        except Exception as e:
            print(f"  ! send failed {h['sym']} {h['tf']}: {e}")

    if not DRY_RUN:
        save_state(state)
    print(f"Done. signals={len(hits)} sent={sent} errors={errors}")


if __name__ == "__main__":
    main()
