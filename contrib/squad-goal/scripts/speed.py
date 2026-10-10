#!/usr/bin/env python3
"""Speed check: PR open->merge minutes before vs after the autopilot (cutoff = SPEED_CUTOFF, default 2026-10-09T19:00:00Z).
usage: speed.py   (any cwd; uses SG_REPO)"""
import os,json,subprocess,statistics as st,datetime as dt
repo=os.environ['SG_REPO']; cut=os.environ.get('SPEED_CUTOFF','2026-10-09T19:00:00Z'); MIN=int(os.environ.get('RECONCILE_MIN','1115'))
f=lambda s:dt.datetime.fromisoformat(s.replace('Z','+00:00'))
out=subprocess.run(['gh','api','--paginate',f'repos/{repo}/pulls?state=closed&per_page=100&sort=updated&direction=desc','--jq','.[]|select(.merged_at!=null)|{n:.number,c:.created_at,m:.merged_at}'],capture_output=True,text=True).stdout
prs=[json.loads(l) for l in out.splitlines() if l.strip()]
rows=[(p['n'],(f(p['m'])-f(p['c'])).total_seconds()/60,p['m']) for p in prs if p['n']>=MIN]
def rep(name,r):
    v=[x[1] for x in r]
    print(f"{name:7} n={len(v):2} median={st.median(v):5.0f}m mean={st.mean(v):5.0f}m max={max(v):4.0f}m  >60m: {sum(x>60 for x in v)}" if v else f"{name:7} n=0")
rep('before',[r for r in rows if r[2]<cut]); rep('after',[r for r in rows if r[2]>=cut])
for r in sorted(rows,key=lambda x:x[2]):
    if r[2]>=cut: print(f"  #{r[0]} {r[1]:.0f}m merged {r[2]}")
