import sys, glob, json, os
from collections import defaultdict
D=sys.argv[1]; rows=[]
for p in glob.glob(os.path.join(D,"lvl_*.json")): rows+=json.load(open(p))
rows.sort(key=lambda r:r["entry_at"])
def take(rs):
    busy={}; out=[]
    for r in rs:
        k=r["symbol"]
        if k in busy and r["entry_at"]<busy[k]: continue
        out.append(r); busy[k]=r["entry_at"]  # placeholder, replaced below
    return out
def take2(rs, bar_minutes):
    from datetime import datetime, timedelta
    busy={}; out=[]
    for r in rs:
        t=datetime.fromisoformat(r["entry_at"])
        if r["symbol"] in busy and t<busy[r["symbol"]]: continue
        out.append(r); busy[r["symbol"]]=t+timedelta(minutes=bar_minutes*(r["bars_held"]+1))
    return out
def st(rs):
    if not rs: return "0 tr"
    mins = 15 if rs[0]["trigger"]=="15m" else 60
    rs=take2(rs, mins); v=[r["net_r"] for r in rs]
    eq=pk=dd=0
    for x in v: eq+=x; pk=max(pk,eq); dd=min(dd,eq-pk)
    m=defaultdict(float)
    for r in rs: m[r["entry_at"][:7]]+=r["net_r"]
    return f"{len(v):4d} tr ({len(v)/12.8:5.1f}/mo) win {100*sum(x>0 for x in v)/len(v):3.0f}% net {eq:+7.1f}R {eq/len(v):+.3f}/tr DD {dd:6.1f} green {sum(x>0 for x in m.values())}/{len(m)}"
F=lambda **kw: [r for r in rows if all(r[k]==v for k,v in kw.items())]
filt=lambda rs: [r for r in rs if r["fee_ok"] and r["tp2_beyond_tp1"]]
print("== all 5 coins, live fee rule + TP2 beyond TP1 ==")
for trig in ("15m","1h"):
    for lv in ("4h","8h","1d"):
        for var in ("1bar","2bar"):
            print(f"{trig:3s} levels {lv:2s} {var}: {st(filt(F(trigger=trig,level_tf=lv,variant=var)))}")
print("\n== BTC only ==")
for trig in ("15m","1h"):
    for lv in ("4h","8h","1d"):
        print(f"{trig:3s} levels {lv:2s} 1bar: {st(filt(F(symbol='BTCUSDT',trigger=trig,level_tf=lv,variant='1bar')))}")
print("\n== effect of the filters (15m, 1bar) ==")
for lv in ("4h","8h","1d"):
    base=F(trigger="15m",level_tf=lv,variant="1bar")
    print(lv, "no filter:", st(base))
    print(lv, "fee only :", st([r for r in base if r["fee_ok"]]))
