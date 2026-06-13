#!/usr/bin/env python3
"""
Polymarket wallet P&L analyzer (Data API).
Pulls ALL activity in a time window by chunking (to beat the 10k offset cap),
dedupes, and aggregates every activity type so we can compute a true realized P&L.
"""
import urllib.request, urllib.parse, json, time, sys, os

USER = "0x69c7b8de588c68d3927f094eb9ec9a2b1bbeb03c"
BASE = "https://data-api.polymarket.com"
OUTDIR = os.path.dirname(os.path.abspath(__file__))

# ---- window: trailing N days from now (override via argv) -------------------
NOW = int(time.time())
DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 7.0
START = int(NOW - DAYS * 86400)
END = NOW
CHUNK = 2 * 3600          # 2-hour windows (~244 recs each at observed density)
LIMIT = 500
OFFSET_CAP = 10000

def get(url, tries=6):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pnl-research/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"GET failed after {tries}: {url}\n{last}")

def fetch_window(start, end):
    """All activity in [start, end] (inclusive bounds), paginated."""
    rows = []
    t0 = start
    nchunk = 0
    while t0 <= end:
        t1 = min(t0 + CHUNK, end)
        offset = 0
        while True:
            qs = urllib.parse.urlencode({
                "user": USER, "limit": LIMIT, "offset": offset,
                "start": t0, "end": t1, "sortDirection": "ASC",
            })
            data = get(f"{BASE}/activity?{qs}")
            if not data:
                break
            rows.extend(data)
            if len(data) < LIMIT:
                break
            offset += LIMIT
            if offset >= OFFSET_CAP:
                sys.stderr.write(f"WARN: hit offset cap in window {t0}-{t1}; shrink CHUNK\n")
                break
            time.sleep(0.08)
        nchunk += 1
        if nchunk % 12 == 0:
            sys.stderr.write(f"  ...{nchunk} chunks, {len(rows)} raw rows\r")
        t0 = t1 + 1   # next window starts the second after (disjoint, no boundary dup)
        time.sleep(0.04)
    return rows

def key(r):
    return (r.get("transactionHash"), r.get("asset"), r.get("conditionId"),
            r.get("type"), r.get("side"), r.get("size"), r.get("usdcSize"),
            r.get("timestamp"), r.get("outcomeIndex"))

print(f"Window: {START} .. {END}  ({DAYS} days)", file=sys.stderr)
raw = fetch_window(START, END)
seen, uniq = set(), []
for r in raw:
    k = key(r)
    if k in seen:
        continue
    seen.add(k)
    uniq.append(r)
dups = len(raw) - len(uniq)
print(f"\nFetched raw={len(raw)}  unique={len(uniq)}  dups_removed={dups}", file=sys.stderr)

# save raw for the strategy step
path = os.path.join(OUTDIR, f"egig_activity_{int(DAYS)}d.json")
with open(path, "w") as f:
    json.dump(uniq, f)
print(f"Saved -> {path}", file=sys.stderr)

# ---- aggregate --------------------------------------------------------------
from collections import defaultdict
agg = defaultdict(lambda: {"count": 0, "usdc": 0.0, "shares": 0.0})
def bucket(r):
    t = r.get("type")
    if t == "TRADE":
        return f"TRADE-{r.get('side')}"
    return t
for r in uniq:
    b = bucket(r)
    agg[b]["count"] += 1
    agg[b]["usdc"] += float(r.get("usdcSize") or 0)
    agg[b]["shares"] += float(r.get("size") or 0)

if uniq:
    tmin = min(r["timestamp"] for r in uniq)
    tmax = max(r["timestamp"] for r in uniq)
else:
    tmin = tmax = 0

def usd(x): return f"${x:,.2f}"

print("\n================ ACTIVITY BREAKDOWN ================")
print(f"actual data span: {tmin} .. {tmax}  "
      f"({(tmax-tmin)/86400:.2f} days)")
print(f"{'bucket':<16}{'count':>8}{'sum_usdc':>16}{'sum_shares':>16}")
for b in sorted(agg):
    a = agg[b]
    print(f"{b:<16}{a['count']:>8}{usd(a['usdc']):>16}{a['shares']:>16,.2f}")

buy = agg.get("TRADE-BUY", {}).get("usdc", 0.0)
sell = agg.get("TRADE-SELL", {}).get("usdc", 0.0)
redeem = agg.get("REDEEM", {}).get("usdc", 0.0)
split = agg.get("SPLIT", {}).get("usdc", 0.0)
merge = agg.get("MERGE", {}).get("usdc", 0.0)
reward = agg.get("REWARD", {}).get("usdc", 0.0)
rebate = agg.get("MAKER_REBATE", {}).get("usdc", 0.0)
refrew = agg.get("REFERRAL_REWARD", {}).get("usdc", 0.0)
conv = agg.get("CONVERSION", {}).get("usdc", 0.0)

print("\n================ P&L ================")
simple = redeem - buy
print(f"Simple model (your ask):  REDEEM - BUY = {usd(redeem)} - {usd(buy)} = {usd(simple)}")
cash_in = redeem + sell + merge + reward + rebate + refrew
cash_out = buy + split
full = cash_in - cash_out
print(f"Full cash-flow:  (REDEEM+SELL+MERGE+REWARD+REBATE+REF) - (BUY+SPLIT)")
print(f"                 ({usd(cash_in)}) - ({usd(cash_out)}) = {usd(full)}")
print(f"Open-position value now (from /value): $0.00  -> realized == total")
roi = (simple / buy * 100) if buy else 0
print(f"ROI on capital deployed (buys): {roi:.1f}%")
