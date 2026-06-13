#!/usr/bin/env python3
"""Warm a local cache of full-week Binance 1s klines for the 4 coins, so we can
reconstruct EVERY 5-min window (traded + skipped). Threaded + idempotent cache."""
import json, os, re, sys, time, urllib.request, concurrent.futures as cf
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
FULL = os.path.join(HERE, "binance_cache", "full"); os.makedirs(FULL, exist_ok=True)
SYM = {"BTC":"BTCUSDT","ETH":"ETHUSDT","SOL":"SOLUSDT","XRP":"XRPUSDT"}
HOST = "https://data-api.binance.vision"

rows = json.load(open(os.path.join(HERE,"egig_activity_7d.json")))
def bucket(s):
    m=re.search(r"-(\d{9,})$", s or ""); return int(m.group(1)) if m else 0
Ts=[bucket(r.get("slug")) for r in rows if bucket(r.get("slug"))]
minT, maxT = min(Ts), max(Ts)
START = (minT//1000)*1000 - 1000
END   = ((maxT+300)//1000)*1000 + 2000
pages=list(range(START, END, 1000))
print(f"span {START}..{END}  ({(END-START)/86400:.2f}d)  pages/coin={len(pages)}  coins={len(SYM)}")

def fetch(args):
    sym, ps = args
    fp=os.path.join(FULL, f"{sym}_{ps}.json")
    if os.path.exists(fp): return 0
    url=f"{HOST}/api/v3/klines?symbol={sym}&interval=1s&startTime={ps*1000}&endTime={(ps+1000)*1000-1}&limit=1000"
    for i in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"x"}),timeout=40) as r:
                d=json.load(r)
            json.dump([[k[0]//1000, float(k[1]), float(k[4])] for k in d], open(fp,"w"))
            return 1
        except Exception as e:
            time.sleep(0.5*(i+1))
    print(f"FAIL {sym} {ps}"); return 0

jobs=[(s,p) for s in SYM.values() for p in pages]
done=0; t0=time.time()
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    for i,_ in enumerate(ex.map(fetch, jobs),1):
        done+=1
        if done%200==0: print(f"  {done}/{len(jobs)} pages  ({time.time()-t0:.0f}s)")
print(f"DONE {len(jobs)} pages in {time.time()-t0:.0f}s")
