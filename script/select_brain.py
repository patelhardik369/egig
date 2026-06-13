#!/usr/bin/env python3
"""
THE SELECTION BRAIN: what distinguishes the ~492 windows the bot TRADED from the
thousands it SKIPPED. Reconstruct every 5-min window for the week from Binance 1s
data, label traded/skipped, and test where the edge actually comes from.

Resolution truth = on-chain redeems (exact). Binance = price-path proxy.
"""
import json, os, re, glob, bisect, statistics as st, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
FULL = os.path.join(HERE, "binance_cache", "full")
SYM  = {"BTC":"BTCUSDT","ETH":"ETHUSDT","SOL":"SOLUSDT","XRP":"XRPUSDT"}
INV  = {v:k for k,v in SYM.items()}

# ---- load full-week price series per coin -----------------------------------
series={}  # coin -> (secs[], opens[], closes[])
for sym in SYM.values():
    pts=[]
    for fp in glob.glob(os.path.join(FULL, f"{sym}_*.json")):
        pts.extend(json.load(open(fp)))
    pts.sort()
    secs=[p[0] for p in pts]; opens=[p[1] for p in pts]; closes=[p[2] for p in pts]
    series[INV[sym]]=(secs,opens,closes)
    print(f"{INV[sym]}: {len(secs):,} seconds loaded "
          f"({(secs[-1]-secs[0])/86400:.2f}d)" if secs else f"{INV[sym]}: EMPTY")

def px_at(coin, t):           # forward-filled close at/just before t
    secs,opens,closes=series[coin]
    i=bisect.bisect_right(secs,t)-1
    return closes[max(i,0)]
def open_at(coin, T):         # window-open reference
    secs,opens,closes=series[coin]
    i=bisect.bisect_left(secs,T)
    if i<len(secs) and secs[i]==T: return opens[i]
    return closes[max(i-1,0)]
def path(coin, a, b):         # list of (sec,close) within [a,b]
    secs,opens,closes=series[coin]
    i=bisect.bisect_left(secs,a); j=bisect.bisect_right(secs,b)
    return secs[i:j], closes[i:j]

# ---- traded windows + bot buy timestamps from on-chain ----------------------
rows=json.load(open(os.path.join(HERE,"egig_activity_7d.json")))
def bucket(s):
    m=re.search(r"-(\d{9,})$", s or ""); return int(m.group(1)) if m else 0
def coinof(s,t):
    m=re.match(r"([a-z]+)-updown", s or ""); return m.group(1).upper() if m else (t or "?").split(" ")[0].upper()
traded=defaultdict(set); won=defaultdict(set); buy_ts=[]
for r in rows:
    T=bucket(r.get("slug")); c=coinof(r.get("slug"),r.get("title"))
    if not T or c not in SYM: continue
    if r["type"]=="TRADE" and r["side"]=="BUY":
        traded[c].add(T); buy_ts.append(r["timestamp"])
    elif r["type"]=="REDEEM":
        won[c].add(T)
buy_ts.sort()
def online(T):  # bot active within 15 min of this window?
    i=bisect.bisect_left(buy_ts,T)
    for j in (i-1,i):
        if 0<=j<len(buy_ts) and abs(buy_ts[j]-T)<=900: return True
    return False

# ---- build universe of windows ----------------------------------------------
def feats(coin,T):
    o=open_at(coin,T); end=T+300
    if o<=0: return None
    p=lambda off: px_at(coin,T+off)
    lead=lambda off:(p(off)-o)/o*100.0          # signed % vs open
    final=(px_at(coin,end)-o)/o*100.0
    secs,cl=path(coin,T,end)
    cross=0; prev=None
    for v in cl:
        s=1 if v>=o else -1
        if prev is not None and s!=prev: cross+=1
        prev=s
    rv=0.0
    if len(cl)>2:
        d=[ (cl[k]-cl[k-1])/o for k in range(1,len(cl)) ]
        rv=st.pstdev(d)*100.0
    return {"open":o,"l285":lead(285),"l297":lead(297),"l270":lead(270),
            "final":final,"cross":cross,"rv":rv,
            "maxexc":max((abs(v-o) for v in cl),default=0)/o*100.0,
            "npts":len(cl)}

U=[]
for c in SYM:
    Ts=sorted(traded[c])
    lo=(min(Ts)//300)*300; hi=(max(Ts)//300)*300
    for T in range(lo,hi+1,300):
        f=feats(c,T)
        if not f: continue
        f.update(coin=c,T=T,traded=(T in traded[c]),won=(T in won[c]),online=online(T))
        # reversal = whoever led at 297 (3s left) lost by close
        f["rev297"]= (f["l297"]>0 and f["final"]<0) or (f["l297"]<0 and f["final"]>0)
        f["rev285"]= (f["l285"]>0 and f["final"]<0) or (f["l285"]<0 and f["final"]>0)
        f["defi297"]=abs(f["l297"]); f["defi285"]=abs(f["l285"])
        U.append(f)

onl=[w for w in U if w["online"]]
trd=[w for w in U if w["traded"]]
skp=[w for w in onl if not w["traded"]]
print(f"\nUNIVERSE: {len(U)} windows in active span | online={len(onl)} | TRADED={len(trd)} | SKIPPED(online)={len(skp)}")
print(f"trade rate among online windows = {len(trd)/len(onl)*100:.1f}%")

def med(xs): return st.median(xs) if xs else float('nan')
def pq(xs,q): xs=sorted(xs); return xs[min(len(xs)-1,int(len(xs)*q))] if xs else float('nan')

print("\n========== A) TRADED vs SKIPPED: the price signature ==========")
print(f"{'feature':<26}{'TRADED med':>12}{'SKIP med':>12}{'TRADED p90':>12}{'SKIP p90':>12}")
for name,key in [("|lead 15s-left| %","defi285"),("|lead 3s-left| %","defi297"),
                 ("zero-crossings(choppy)","cross"),("realized vol %","rv"),
                 ("max excursion %","maxexc")]:
    print(f"{name:<26}{med([w[key] for w in trd]):>12.4f}{med([w[key] for w in skp]):>12.4f}"
          f"{pq([w[key] for w in trd],.9):>12.4f}{pq([w[key] for w in skp],.9):>12.4f}")

print("\n========== B) NATURAL reversal rate by |lead @3s-left| (the win curve) ==========")
print("  'If you blindly bid the LOSER 3s before close, how often does it flip?'")
bands=[(0,0.005),(0.005,0.01),(0.01,0.02),(0.02,0.04),(0.04,0.07),(0.07,0.12),(0.12,0.25),(0.25,99)]
print(f"{'deficit% band':<16}{'#windows':>9}{'reversal%':>11}{'#bot-traded':>12}{'bot win%':>10}")
for lo,hi in bands:
    grp=[w for w in onl if lo<=w["defi297"]<hi]
    bt =[w for w in grp if w["traded"]]
    rev=sum(w["rev297"] for w in grp)/len(grp)*100 if grp else 0
    bwin=sum(w["won"] for w in bt)/len(bt)*100 if bt else 0
    print(f"[{lo:.3f},{hi:.3f}){'':<2}{len(grp):>9}{rev:>10.1f}%{len(bt):>12}{bwin:>9.1f}%")

print("\n========== C) Does the bot's selection beat blind bidding? ==========")
def winrate(ws):
    b=[w for w in ws];
    return (sum(w['won'] for w in b)/len(b)*100) if b else 0
# bot band = where it actually plays (match its deficit footprint)
band=[w for w in onl if 0.005<=w["defi297"]<0.12]
print(f"breakeven win-rate (pays ~2c)                : ~2.0%   (size-wtd 1.55%)")
print(f"BLIND: bid loser on ALL online windows       : reversal {sum(w['rev297'] for w in onl)/len(onl)*100:4.1f}%  (n={len(onl)})")
print(f"BLIND: bid loser on the 'cheap-loser' band    : reversal {sum(w['rev297'] for w in band)/len(band)*100:4.1f}%  (n={len(band)})")
print(f"BOT actual (its 492 chosen windows)           : win      {sum(w['won'] for w in trd)/len(trd)*100:4.1f}%  (n={len(trd)})")
# coverage: of the cheap-loser band, how many did the bot take?
covered=[w for w in band if w["traded"]]
print(f"cheap-loser-band windows the bot actually took: {len(covered)}/{len(band)} = {len(covered)/len(band)*100:.0f}%")

print("\n========== D) Within the band, what makes the bot PICK a window? ==========")
bt=[w for w in band if w["traded"]]; bs=[w for w in band if not w["traded"]]
for name,key in [("choppiness(cross)","cross"),("realized vol %","rv"),("|lead 15s| %","defi285"),("max exc %","maxexc")]:
    print(f"  {name:<20} picked med={med([w[key] for w in bt]):.4f}  skipped med={med([w[key] for w in bs]):.4f}")
# reversal rate picked vs skipped within band -> is the pick predictive?
print(f"  reversal% PICKED={sum(w['rev297'] for w in bt)/len(bt)*100:.1f}%   "
      f"SKIPPED={sum(w['rev297'] for w in bs)/len(bs)*100:.1f}%   (within same band)")

print("\n========== E) per-coin selectivity ==========")
print(f"{'coin':<5}{'online':>8}{'traded':>8}{'rate%':>7}{'bandWin%':>10}{'botWin%':>9}")
for c in SYM:
    o=[w for w in onl if w["coin"]==c]; t=[w for w in o if w["traded"]]
    bnd=[w for w in o if 0.005<=w["defi297"]<0.12]
    print(f"{c:<5}{len(o):>8}{len(t):>8}{len(t)/max(len(o),1)*100:>6.1f}%"
          f"{sum(w['rev297'] for w in bnd)/max(len(bnd),1)*100:>9.1f}%{sum(w['won'] for w in t)/max(len(t),1)*100:>8.1f}%")

json.dump([{k:w[k] for k in ('coin','T','traded','won','online','l285','l297','final','cross','rv','defi297','rev297')} for w in U],
          open(os.path.join(HERE,"universe.json"),"w"))
print("\nsaved -> universe.json")
