#!/usr/bin/env python3
"""
Reverse-engineer the egig bot's entry strategy on Polymarket 5-min UP/DOWN markets
by aligning every on-chain BUY against the Binance 1s price path of the same window.

Resolution (per Polymarket/CLOB): UP if price(end) >= price(start) of the 5-min window,
source = Chainlink <coin>/USD stream. Binance spot is used here as a price-path PROXY;
we validate the proxy against on-chain ground truth (who actually redeemed).
"""
import urllib.request, json, os, re, time, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "binance_cache"); os.makedirs(CACHE, exist_ok=True)
SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
HOST = "https://data-api.binance.vision"

def http(url, tries=5):
    last=None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"x"}), timeout=40) as r:
                return json.load(r)
        except Exception as e:
            last=e; time.sleep(0.6*(i+1))
    raise RuntimeError(f"{url}\n{last}")

def klines(sym, start, end):
    """1s klines covering [start-2, end+1] (unix s). Cached per (sym,start)."""
    fp = os.path.join(CACHE, f"{sym}_{start}.json")
    if os.path.exists(fp):
        return json.load(open(fp))
    url = (f"{HOST}/api/v3/klines?symbol={sym}&interval=1s"
           f"&startTime={(start-2)*1000}&endTime={(end+1)*1000}&limit=500")
    d = http(url)
    json.dump(d, open(fp,"w"))
    time.sleep(0.03)
    return d

def slug_bucket(slug):
    m = re.search(r"-(\d{9,})$", slug or ""); return int(m.group(1)) if m else 0
def coin_of(slug, title):
    m = re.match(r"([a-z]+)-updown", slug or "")
    return m.group(1).upper() if m else (title or "?").split(" ")[0].upper()

# ---- assemble markets from on-chain activity --------------------------------
rows = json.load(open(os.path.join(HERE,"egig_activity_7d.json")))
M = {}
for r in rows:
    cid=r["conditionId"]; m=M.setdefault(cid, {"coin":coin_of(r.get("slug"),r.get("title")),
        "slug":r.get("slug"),"start":slug_bucket(r.get("slug")),"title":r.get("title",""),
        "buys":[], "won":False, "redeem":0.0, "sides":set(), "idx":set()})
    if r["type"]=="TRADE" and r["side"]=="BUY":
        m["buys"].append({"ts":r["timestamp"],"px":r.get("price",0.0),"sz":r["size"],
                          "usd":r["usdcSize"],"outcome":r.get("outcome"),"idx":r.get("outcomeIndex")})
        m["sides"].add(r.get("outcome")); m["idx"].add(r.get("outcomeIndex"))
    elif r["type"]=="REDEEM":
        m["won"]=True; m["redeem"]+=r["usdcSize"]

markets=[m for m in M.values() if m["buys"] and m["start"] and m["coin"] in SYM]
print(f"markets analyzable: {len(markets)} (have buys, slug epoch, known coin)")
mixed=[m for m in markets if len(m["sides"])>1]
print(f"markets where bot bought BOTH sides: {len(mixed)}  -> {'single-sided as expected' if not mixed else 'SEE BELOW'}")

# ---- align each market with Binance price path ------------------------------
def build_px(kl):
    """second -> close(float); plus open-field map. forward-fillable."""
    close={}; openf={}
    for k in kl:
        s=k[0]//1000; openf[s]=float(k[1]); close[s]=float(k[4])
    return close, openf
def ff(d, s):
    while s not in d:
        s-=1
        if s < min(d): return d[min(d)]
    return d[s]

missing=0
recon_ok=0; recon_tot=0; mism=[]
buys_flat=[]   # per-buy enriched records
per_market=[]
for m in markets:
    start=m["start"]; end=start+300; sym=SYM[m["coin"]]
    try:
        kl=klines(sym,start,end)
    except Exception:
        kl=[]
    if not kl:
        missing+=1; continue
    close,openf=build_px(kl)
    smin=min(close)
    open_ref = openf.get(start, ff(close,start))          # price at window start
    close_ref= close.get(end, close.get(end-1, ff(close,end)))  # price at window end
    recon_up = close_ref >= open_ref
    # actual winning side from ground truth
    bought = next(iter(m["idx"]))           # outcomeIndex bot bought (0=Up,1=Down)
    bought_up = (bought==0)
    actual_up = bought_up if m["won"] else (not bought_up)
    recon_tot+=1
    if recon_up==actual_up: recon_ok+=1
    else: mism.append((m["coin"],abs(close_ref-open_ref),open_ref,close_ref,m["won"]))

    m_fills=[]
    for b in m["buys"]:
        ts=b["ts"]; sec_into=ts-start; sec_left=end-ts
        px=ff(close,ts) if ts>=smin else open_ref
        # momentum over the 15s before the buy
        px_15=ff(close,ts-15) if ts-15>=smin else open_ref
        mom15=px-px_15
        side_up=(b["idx"]==0)
        # 'deficit' = how far the BOUGHT side is currently losing by (in $ and %)
        # if bought Up, you're losing when px<open -> deficit=open-px
        deficit = (open_ref-px) if side_up else (px-open_ref)
        is_rev = deficit>0   # bought the side currently behind => betting reversal
        rec={"coin":m["coin"],"won":m["won"],"sec_into":sec_into,"sec_left":sec_left,
             "entry":b["px"],"usd":b["usd"],"sz":b["sz"],"side_up":side_up,
             "deficit":deficit,"deficit_pct":deficit/open_ref*100 if open_ref else 0,
             "mom15":mom15,"open":open_ref}
        buys_flat.append(rec); m_fills.append(rec)
    per_market.append({"m":m,"open":open_ref,"close":close_ref,"recon_up":recon_up,
                       "actual_up":actual_up,"fills":m_fills,
                       "last_left":min(f["sec_left"] for f in m_fills),
                       "nfills":len(m_fills),"usd":sum(f["usd"] for f in m_fills)})

print(f"klines missing for {missing} markets")
print("\n================ A) RESOLUTION-PROXY VALIDATION (Binance vs on-chain truth) ================")
print(f"reconstructed direction matched actual outcome: {recon_ok}/{recon_tot} = {recon_ok/recon_tot*100:.1f}%")
mism.sort(key=lambda x:x[1])
print(f"mismatches: {len(mism)}  (expected on thin moves: Binance != Chainlink)")
thin=[x for x in mism if x[1]<x[2]*0.0002]   # |move|<0.02%
print(f"  of which on <0.02% moves (coin-flip thin): {len(thin)}  -> {len(thin)/max(len(mism),1)*100:.0f}% of mismatches")
print("  closest 5 mismatches |Δ$|, open, close:", [(c,round(d,3),round(o,1),round(cl,1)) for c,d,o,cl,_ in mism[:5]])

print("\n================ B) WHEN it trades (timing within the 5-min window) ================")
lefts=[b["sec_left"] for b in buys_flat]
intos=[b["sec_into"] for b in buys_flat]
def pct(xs,p): xs=sorted(xs); return xs[min(len(xs)-1,int(len(xs)*p))]
print(f"buys={len(buys_flat)}  sec_left: min={min(lefts)} p10={pct(lefts,.10)} median={int(st.median(lefts))} p90={pct(lefts,.90)} max={max(lefts)}")
for thr in (10,15,30,45,60,90,120):
    n=sum(1 for x in lefts if x<=thr); print(f"  buys with <= {thr:3d}s left: {n:5d} ({n/len(lefts)*100:4.1f}%)")
print(f"first-buy timing per market: median sec_left at FIRST fill = {int(st.median([max(f['sec_left'] for f in pm['fills']) for pm in per_market]))}")
print(f"last-buy  timing per market: median sec_left at LAST  fill = {int(st.median([pm['last_left'] for pm in per_market]))}")

print("\n================ C) WHAT it sees (reversal / contrarian entry) ================")
rev=sum(1 for b in buys_flat if b["deficit"]>0)
print(f"entries buying the side currently LOSING (reversal bet): {rev}/{len(buys_flat)} = {rev/len(buys_flat)*100:.1f}%")
defs=[b["deficit_pct"] for b in buys_flat if b["deficit"]>0]
print(f"entry 'deficit' (how far behind the bought side is at entry), % of price:")
print(f"  p10={pct(defs,.1):.3f}%  median={st.median(defs):.3f}%  p90={pct(defs,.9):.3f}%  max={max(defs):.3f}%")
# deficit in $ for BTC specifically (intuitive)
btc_def=[b["deficit"] for b in buys_flat if b["deficit"]>0 and b["coin"]=="BTC"]
if btc_def: print(f"  BTC deficit in $: median=${st.median(btc_def):.1f}  p90=${pct(btc_def,.9):.1f}")
# momentum: did it buy right after a move AGAINST the bought side (fade the spike)?
fade=sum(1 for b in buys_flat if (b["side_up"] and b["mom15"]<0) or ((not b["side_up"]) and b["mom15"]>0))
print(f"entries where price moved AGAINST the bought side in the prior 15s (fading a spike): {fade/len(buys_flat)*100:.1f}%")

print("\n================ D) entry price vs time/deficit ================")
ep=[b["entry"] for b in buys_flat]
print(f"entry price (cents): min={min(ep)*100:.1f} median={st.median(ep)*100:.1f} mean={sum(ep)/len(ep)*100:.2f} max={max(ep)*100:.1f}")
# relationship: bucket by sec_left
for lo,hi in [(0,15),(15,30),(30,60),(60,120),(120,300)]:
    g=[b for b in buys_flat if lo<=b["sec_left"]<hi]
    if g: print(f"  sec_left [{lo:3d},{hi:3d}): n={len(g):5d}  median entry={st.median([x['entry'] for x in g])*100:.1f}c  median deficit={st.median([x['deficit_pct'] for x in g]):.3f}%")

print("\n================ E) laddering (fills & size per market) ================")
nf=[pm["nfills"] for pm in per_market]; us=[pm["usd"] for pm in per_market]
print(f"fills/market: median={int(st.median(nf))} mean={sum(nf)/len(nf):.1f} max={max(nf)}")
print(f"$/market: median=${st.median(us):.2f} mean=${sum(us)/len(us):.2f} max=${max(us):.2f}")
# does it add MORE as deficit grows / price drops? compare entry price of 1st vs last fill
firstlast=[]
for pm in per_market:
    fs=sorted(pm["fills"], key=lambda f:f["sec_left"], reverse=True)
    if len(fs)>=2: firstlast.append((fs[0]["entry"],fs[-1]["entry"]))
if firstlast:
    drops=sum(1 for a,b in firstlast if b<a)
    print(f"markets where LAST fill cheaper than FIRST (adds as it falls): {drops}/{len(firstlast)} = {drops/len(firstlast)*100:.0f}%")

print("\n================ F) outcome & edge (EV) ================")
won_m=[pm for pm in per_market if pm["m"]["won"]]
tot_sz=sum(b["sz"] for b in buys_flat); win_sz=sum(b["sz"] for pm in won_m for b in [] )  # placeholder
tot_usd=sum(b["usd"] for b in buys_flat)
red=sum(pm["m"]["redeem"] for pm in per_market)
swavg=tot_usd/tot_sz
print(f"markets won: {len(won_m)}/{len(per_market)} = {len(won_m)/len(per_market)*100:.1f}%")
print(f"size-weighted avg entry price = {swavg*100:.3f}c  -> breakeven win-rate(by shares) = {swavg*100:.3f}%")
# share win rate
red_sz=sum(pm['m']['redeem'] for pm in won_m)   # redeem usd == winning shares ($1 each)
print(f"shares bought={tot_sz:,.0f}  winning shares(=redeem$)={red_sz:,.0f}  share win-rate={red_sz/tot_sz*100:.2f}%")
print(f"EV/share = P(win)*$1 - avgcost = {red_sz/tot_sz:.4f} - {swavg:.4f} = {red_sz/tot_sz - swavg:+.4f}  ({(red_sz/tot_sz-swavg)/swavg*100:+.0f}% ROI)")
print(f"NET P&L (redeem-buy) = ${red-tot_usd:,.2f}")
# robustness: drop the single biggest winner
big=max(per_market, key=lambda pm: pm["m"]["redeem"])
print(f"biggest single win: {big['m']['title']} redeem=${big['m']['redeem']:,.0f}")
print(f"P&L EXCLUDING that one market = ${(red-big['m']['redeem'])-(tot_usd-big['usd']):,.2f}")
# per coin EV
print("per-coin: coin  mkts won  win%  shareWin%  net$")
bycoin=defaultdict(lambda:{"n":0,"w":0,"sz":0.0,"wsz":0.0,"buy":0.0,"red":0.0})
for pm in per_market:
    c=pm["m"]["coin"]; bycoin[c]["n"]+=1; bycoin[c]["buy"]+=pm["usd"]; bycoin[c]["red"]+=pm["m"]["redeem"]
    bycoin[c]["sz"]+=sum(f["sz"] for f in pm["fills"])
    if pm["m"]["won"]: bycoin[c]["w"]+=1; bycoin[c]["wsz"]+=pm["m"]["redeem"]
for c,b in sorted(bycoin.items(), key=lambda kv:-kv[1]["red"]):
    print(f"  {c:<4} {b['n']:4d} {b['w']:3d}  {b['w']/b['n']*100:4.1f}%  {b['wsz']/b['sz']*100:5.2f}%  ${b['red']-b['buy']:+,.0f}")

# save enriched buys for any follow-up
json.dump(buys_flat, open(os.path.join(HERE,"buys_enriched.json"),"w"))
print("\nsaved -> buys_enriched.json")
