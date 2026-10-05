"""One-off: turn the v5 catalog entries into plain issue files. Drops Touches, Independence, Units (the answer key)."""
import json, re, sys
from pathlib import Path
cat = Path(sys.argv[1]).read_text()
man = json.load(open(sys.argv[2]))["tasks"]
out = Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
start = cat.index("### `M1` - `M10`")
body = cat[start:]
hdr = re.compile(r"^\*\*([MRK]\d{1,2}) — ", re.M)
ms = list(hdr.finditer(body))
ids = {t["task_id"]: t for t in man}
num = {t["task_id"]: t["cycle"] for t in man}
prereq = {"R4": ["M8"], "K2": ["K1"], "K3": ["K2", "K1"], "K5": ["K4"], "K6": ["K5", "K4"]}
def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
for i, m in enumerate(ms):
    tid = m.group(1)
    end = ms[i+1].start() if i+1 < len(ms) else len(body)
    chunk = body[m.start():end]
    chunk = re.split(r"^### ", chunk, flags=re.M)[0]
    t = re.sub(r"\s+", " ", ids[tid]["title"])
    rest = chunk[m.end()-m.start():]
    # drop the title tail + Touches run: up to the first "`.\n" after "*Touches:*"
    tm = re.search(r"\*Touches:\*.*?`\.\n", rest, re.S)
    assert tm, tid
    rest = rest[tm.end():]
    a = rest.index("- Acceptance:")
    ind = min(rest.index(k) for k in ("- Independence:","- Depends on:","- Units:") if k in rest)
    prose = rest[:a].strip()
    acc = rest[a+len("- Acceptance:"):ind].strip()
    n = num[tid]
    sub = lambda x: re.sub(r"`?\b([MRK]\d{1,2})\b`?", lambda mm: f"#{num[mm.group(1)]:02d}" if mm.group(1) in num else mm.group(0), x)
    prose, acc = sub(prose), sub(acc)
    deps = "".join(f"- #{num[d]:02d}\n" for d in prereq.get(tid, [])) or "- none\n"
    text = f"# {t}\n\nStatus: open\n\n## Blocked by\n\n{deps}\n## Description\n\n{prose}\n\n## Acceptance criteria\n\n{acc}\n"
    (out / f"{n:02d}-{slug(t)}.md").write_text(text)
print(len(ms))
