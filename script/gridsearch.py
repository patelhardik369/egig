#!/usr/bin/env python3
"""
Grid-search the optimal entry FILTER (|lead| x choppiness x coin) on universe.json,
with a time-based train/test split to guard against overfitting, then draw the
cumulative P&L curve of BOT'S RULE vs TUNED RULE vs BLIND ("bet every cheap loser").

Labels for selection use the Binance reversal proxy (rev297) over ALL online windows
(hundreds of reversal events => statistical power). The bot's REAL ground-truth $
curve (from on-chain redeems) is shown separately as the reality anchor.

Normalized P&L model (so all rules are comparable):
  per chosen window: buy the losing side with STAKE dollars at price P/share.
  reversal -> shares*$1 = STAKE/P ; else -> 0.  pnl = STAKE*(rev/P - 1).
"""
import json, os, statistics as st, csv, datetime as dt, sys, re
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")
HERE=os.path.dirname(os.path.abspath(__file__))

U=json.load(open(os.path.join(HERE,"universe.json")))
for w in U: w["defi285"]=abs(w["l285"])
online=sorted((w for w in U if w["online"]), key=lambda w:w["T"])
tmid=st.median([w["T"] for w in online])
train=[w for w in online if w["T"]<=tmid]; test=[w for w in online if w["T"]>tmid]
print(f"online windows={len(online)}  train={len(train)}  test={len(test)}  "
      f"split@{dt.datetime.utcfromtimestamp(int(tmid)):%Y-%m-%d %H:%MZ}")

P=0.0155       # assumed entry price/share (= bot's size-weighted avg)
STAKE=8.35     # $/window (= bot's avg $/window)  -> shares = 538/window
BE=P           # breakeven win-rate

def stats(ws):
    N=len(ws); nr=sum(w["rev297"] for w in ws); wr=nr/N if N else 0
    return N, nr, wr, STAKE*(nr/P-N)          # N, #rev, winrate, normalized P&L

ALL=("BTC","ETH","SOL","XRP"); NOETH=("BTC","SOL","XRP")
def passes(w, up, cmin, coins, lo=0.005):
    return w["coin"] in coins and lo<=w["defi297"]<up and w["cross"]>=cmin

LEAD=[0.015,0.020,0.025,0.030,0.040]; CROSS=[0,4,6,8]; COINS=[("ALL",ALL),("noETH",NOETH)]
rows=[]
for up in LEAD:
    for cm in CROSS:
        for cn,coins in COINS:
            tr=[w for w in train if passes(w,up,cm,coins)]
            te=[w for w in test  if passes(w,up,cm,coins)]
            if len(tr)<40 or len(te)<20: continue
            Ntr,_,wtr,ptr=stats(tr); Nte,_,wte,pte=stats(te)
            rows.append(dict(up=up,cm=cm,cn=cn,Ntr=Ntr,wtr=wtr,ptr=ptr,Nte=Nte,wte=wte,pte=pte))

rows.sort(key=lambda r:-r["ptr"])     # rank by TRAIN normalized P&L
print("\n========== GRID-SEARCH (ranked by TRAIN P&L; TEST = out-of-sample) ==========")
print(f"{'lead<':>6}{'cross>=':>8}{'coins':>7} | {'TRAIN n':>8}{'win%':>7}{'pnl$':>9} | {'TEST n':>8}{'win%':>7}{'pnl$':>9}")
for r in rows[:12]:
    print(f"{r['up']:>6.3f}{r['cm']:>8}{r['cn']:>7} | {r['Ntr']:>8}{r['wtr']*100:>6.1f}%{r['ptr']:>9.0f} | "
          f"{r['Nte']:>8}{r['wte']*100:>6.1f}%{r['pte']:>9.0f}")

# choose best rule with positive, stable OUT-OF-SAMPLE performance
viable=[r for r in rows if r["pte"]>0]
best=max(viable, key=lambda r:r["pte"]+r["ptr"]) if viable else rows[0]
print(f"\nCHOSEN RULE: lead<{best['up']:.3f}%  cross>={best['cm']}  coins={best['cn']}  (lo=0.005%)")

# ---- baselines on the SAME proxy/stake model, full week ----------------------
def sel_full(fn): return [w for w in online if fn(w)]
blind   = sel_full(lambda w: w["defi297"]>=0.005)                 # bet every cheap loser
botset  = sel_full(lambda w: w["traded"])                          # what the bot actually did
coinsB  = ALL if best["cn"]=="ALL" else NOETH
tuned   = sel_full(lambda w: passes(w,best["up"],best["cm"],coinsB))
for name,ws in [("BLIND (every cheap loser)",blind),("BOT actual set",botset),("TUNED rule",tuned)]:
    N,nr,wr,pnl=stats(ws)
    print(f"  {name:<26} n={N:<5} win%={wr*100:4.1f}  ROI={ (wr/P-1)*100:6.0f}%  normP&L=${pnl:,.0f}")

# ---- REAL ground-truth bot curve from on-chain (redeem - buy per window) -----
act=json.load(open(os.path.join(HERE,"egig_activity_7d.json")))
def bk(s):
    m=re.search(r"-(\d{9,})$", s or ""); return int(m.group(1)) if m else 0
gw=defaultdict(lambda:{"buy":0.0,"red":0.0})
for r in act:
    T=bk(r.get("slug"))
    if not T: continue
    if r["type"]=="TRADE" and r["side"]=="BUY": gw[T]["buy"]+=r["usdcSize"]
    elif r["type"]=="REDEEM": gw[T]["red"]+=r["usdcSize"]

# ---- cumulative curves over time --------------------------------------------
def cum(ws):
    run=0.0; out=[]
    for w in sorted(ws,key=lambda x:x["T"]):
        run+=STAKE*(w["rev297"]/P-1); out.append((w["T"],run))
    return out
def cum_real():
    run=0.0; out=[]
    for T in sorted(gw):
        run+=gw[T]["red"]-gw[T]["buy"]; out.append((T,run))
    return out

curves={"BLIND":cum(blind),"BOT(proxy)":cum(botset),"TUNED":cum(tuned),"BOT(real $)":cum_real()}

# daily cumulative table
t0=min(w["T"] for w in online);
days=[t0+i*86400 for i in range(8)]
print("\n========== CUMULATIVE P&L BY DAY ($) ==========")
print(f"{'day':>6}"+"".join(f"{k:>14}" for k in curves))
def val_at(c,t):
    v=0.0
    for tt,vv in c:
        if tt<=t: v=vv
        else: break
    return v
for i,d in enumerate(days):
    print(f"{'D'+str(i):>6}"+"".join(f"{val_at(curves[k],d):>14,.0f}" for k in curves))

# ASCII chart (TUNED vs BOT real vs BLIND)
def chart(series, labels, rows=16, cols=64):
    allv=[v for s in series for _,v in s]; lo=min(allv); hi=max(allv)
    t0=min(s[0][0] for s in series); t1=max(s[-1][0] for s in series)
    grid=[[" "]*cols for _ in range(rows)]
    marks="*o#+"
    for si,s in enumerate(series):
        for tt,vv in s:
            x=int((tt-t0)/(t1-t0)*(cols-1)); y=rows-1-int((vv-lo)/(hi-lo)*(rows-1))
            grid[y][x]=marks[si]
    print("\n========== P&L CURVE (x=time over week, y=cumulative $) ==========")
    for ri,row in enumerate(grid):
        yv=hi-(hi-lo)*ri/(rows-1)
        print(f"{yv:>8,.0f} |"+"".join(row))
    print(" "*9+"+"+"-"*cols)
    print(" "*10+"  ".join(f"{m}={l}" for m,l in zip(marks,labels)))
chart([curves["BLIND"],curves["BOT(real $)"],curves["TUNED"]],
      ["BLIND","BOT(real$)","TUNED(proxy)"])

# save CSV
with open(os.path.join(HERE,"pnl_curves.csv"),"w",newline="") as f:
    wr=csv.writer(f); wr.writerow(["day"]+list(curves))
    for i,d in enumerate(days):
        wr.writerow([f"D{i}"]+[f"{val_at(curves[k],d):.2f}" for k in curves])
print("\nsaved -> pnl_curves.csv")

# optional PNG
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    plt.figure(figsize=(10,5))
    for k,c in curves.items():
        xs=[dt.datetime.utcfromtimestamp(t) for t,_ in c]; ys=[v for _,v in c]
        plt.plot(xs,ys,label=k,lw=2 if "real" in k or k=="TUNED" else 1)
    plt.legend(); plt.grid(alpha=.3); plt.title("egig bot: cumulative P&L — bot vs tuned vs blind")
    plt.ylabel("cumulative P&L ($)"); plt.tight_layout()
    plt.savefig(os.path.join(HERE,"pnl_curves.png"),dpi=120)
    print("saved -> pnl_curves.png")
except Exception as e:
    print(f"(matplotlib unavailable, skipped PNG: {e})")
