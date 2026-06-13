#!/usr/bin/env python3
import urllib.request, json, os, re, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
U = "0x69c7b8de588c68d3927f094eb9ec9a2b1bbeb03c"

def get(url):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "x"}), timeout=40))

# ---------- 1) official cumulative-PnL anchor for exact 7-day delta ----------
pnl = get(f"https://user-pnl-api.polymarket.com/user-pnl?user_address={U}&interval=max&fidelity=1d")
latest = pnl[-1]
target = latest["t"] - 7 * 86400
before = [x for x in pnl if x["t"] <= target]
anchor = before[-1] if before else pnl[0]
print("=== Official Polymarket cumulative-PnL curve ===")
print(f"all-time first point: t={pnl[0]['t']} p=${pnl[0]['p']:.2f}")
print(f"latest:               t={latest['t']} p=${latest['p']:.2f}")
print(f"anchor ~7d before:    t={anchor['t']} p=${anchor['p']:.2f}")
print(f">>> official trailing-7d delta = ${latest['p']-anchor['p']:.2f}")

# ---------- 2) per-market breakdown from saved 7d activity --------------------
rows = json.load(open(os.path.join(HERE, "egig_activity_7d.json")))
mk = defaultdict(lambda: {"coin": "?", "bucket": 0, "buy_usd": 0.0, "buy_sh": 0.0,
                          "red_usd": 0.0, "red_sh": 0.0, "nbuys": 0,
                          "title": "", "prices": []})
def coin_of(slug, title):
    m = re.match(r"([a-z]+)-updown", slug or "")
    if m: return m.group(1).upper()
    return (title or "?").split(" ")[0]
def bucket_of(slug):
    m = re.search(r"-(\d{9,})$", slug or "")
    return int(m.group(1)) if m else 0

for r in rows:
    c = r["conditionId"]; e = mk[c]
    e["coin"] = coin_of(r.get("slug"), r.get("title")); e["title"] = r.get("title", "")
    e["bucket"] = bucket_of(r.get("slug"))
    if r["type"] == "TRADE" and r["side"] == "BUY":
        e["buy_usd"] += r["usdcSize"]; e["buy_sh"] += r["size"]; e["nbuys"] += 1
        e["prices"].append(r.get("price", 0))
    elif r["type"] == "REDEEM":
        e["red_usd"] += r["usdcSize"]; e["red_sh"] += r["size"]

markets = list(mk.values())
traded = [m for m in markets if m["buy_usd"] > 0]
won = [m for m in markets if m["red_usd"] > 0]
print("\n=== Per-market (7d) ===")
print(f"distinct markets touched: {len(markets)}")
print(f"markets with a buy:       {len(traded)}")
print(f"markets that WON (redeem):{len(won)}   ->  win rate = {len(won)/len(traded)*100:.1f}% of markets")
tot_buy = sum(m["buy_usd"] for m in traded)
tot_red = sum(m["red_usd"] for m in won)
print(f"total buy ${tot_buy:,.2f} | total redeem ${tot_red:,.2f} | net ${tot_red-tot_buy:,.2f}")

# per coin
bycoin = defaultdict(lambda: {"buy": 0.0, "red": 0.0, "n": 0, "w": 0})
for m in traded:
    b = bycoin[m["coin"]]
    b["buy"] += m["buy_usd"]; b["red"] += m["red_usd"]; b["n"] += 1
    if m["red_usd"] > 0: b["w"] += 1
print("\n=== By coin ===")
print(f"{'coin':<6}{'mkts':>6}{'won':>5}{'win%':>7}{'buy$':>11}{'redeem$':>12}{'net$':>12}")
for c in sorted(bycoin, key=lambda k: -bycoin[k]["red"]):
    b = bycoin[c]
    print(f"{c:<6}{b['n']:>6}{b['w']:>5}{(b['w']/b['n']*100):>6.0f}%"
          f"{b['buy']:>11,.0f}{b['red']:>12,.0f}{b['red']-b['buy']:>12,.0f}")

# top winners
print("\n=== Top 12 winning redemptions ===")
won.sort(key=lambda m: -m["red_usd"])
print(f"{'coin':<6}{'redeem$':>10}{'cost$':>9}{'profit$':>10}{'shares':>10}  title")
for m in won[:12]:
    print(f"{m['coin']:<6}{m['red_usd']:>10,.0f}{m['buy_usd']:>9,.1f}"
          f"{m['red_usd']-m['buy_usd']:>10,.0f}{m['red_sh']:>10,.0f}  {m['title']}")

# buy-price distribution
allp = [p for m in traded for p in m["prices"]]
allp.sort()
import statistics as st
print("\n=== Buy-price distribution (per fill) ===")
print(f"fills={len(allp)} min={min(allp):.3f} med={st.median(allp):.3f} "
      f"mean={st.mean(allp):.3f} max={max(allp):.3f}")
buckets = [(0,0.01),(0.01,0.02),(0.02,0.03),(0.03,0.05),(0.05,0.10),(0.10,0.25),(0.25,1.01)]
for lo,hi in buckets:
    n=sum(1 for p in allp if lo<=p<hi)
    print(f"  [{lo:.2f},{hi:.2f}): {n:>5} ({n/len(allp)*100:4.1f}%)")
