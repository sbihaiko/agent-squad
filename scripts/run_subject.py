# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk==0.2.161", "anyio", "pyyaml", "jsonschema"]
# ///
"""Run one subject issue through the engine with the real SDK adapter (ticket #44).

Run: uv run scripts/run_subject.py 1
Both agents take their model and effort from config/subject.yaml (squad.subject.load_config; owner ruling 2026-10-02:
claude-sonnet-5-5). The run works in a clone of ~/sources/subject.
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from squad import defaults, graph, jev, subject        # noqa: E402
from squad.engine import Engine                        # noqa: E402
from squad.sdk_adapter import SdkAdapter               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    key = jev.read_key(ROOT / ".env")        # read once, then drop from the environment so no agent tool inherits it (ticket #55)
    secrets = jev.secret_values(ROOT / ".env")
    for n in jev.KEY_NAMES:
        os.environ.pop(n, None)
    clean = subject.clean_env(os.environ)   # gate commands and the actor need the system python3, not uv's
    os.environ.clear()
    os.environ.update(clean)
    p = argparse.ArgumentParser()
    p.add_argument("issue", type=int)
    p.add_argument("--subject", default=str(Path.home() / "sources" / "subject"))
    p.add_argument("--bench", default=str(ROOT / "bench" / "subject"))
    p.add_argument("--runs-dir", default=str(ROOT / "runs"))
    p.add_argument("--defaults", default=str(defaults.PATH), help="agent defaults file; its engine-level keys load for this run (#46)")
    p.add_argument("--env-file", default=None, help="env file with the Jev key; default: .env at the repo root")
    p.add_argument("--no-jev", action="store_true", help="skip the Jev shadow scores")
    a = p.parse_args()
    if a.env_file:
        key, secrets = jev.read_key(a.env_file, {}) or key, secrets | jev.secret_values(a.env_file, {})
    out = Path(a.runs_dir) / f"subject-{a.issue:02d}-{time.strftime('%Y%m%d-%H%M%S')}"
    prep = subject.prepare(a.issue, a.subject, a.bench, out, runs_dir=a.runs_dir)
    cfg = defaults.load(a.defaults)
    bad = defaults.check(cfg)
    if bad:
        raise SystemExit(f"{a.defaults} is unsound: {', '.join(bad)}")
    rec = {"path": str(Path(a.defaults).resolve()), "sha256": defaults.digest(a.defaults), "engine": defaults.engine_view(cfg)}
    shadow = None
    if key and not a.no_jev:   # shadow mode: scores go to the record as diagnostics, no gate reads them (ADR-0008)
        shadow = jev.JevShadow(jev.JevClient(key), jev.roster(Path.home() / ".claude" / "skills"), [prep.issue_file, "CLAUDE.md"])
    eng = Engine(graph.load(prep.graph_path), SdkAdapter.from_defaults(cfg), prep.run_dir, target=prep.target, graph_path=prep.graph_path, defaults=rec,
                 shadow=shadow)
    v = asyncio.run(eng.run())
    leaked = [f for f in out.rglob("*") if f.is_file() and ".git/" not in f.as_posix() and any(sv.encode() in f.read_bytes() for sv in secrets)]
    if leaked:
        print(f"KEY LEAK in {len(leaked)} file(s) of the run directory", file=sys.stderr)
        return 3
    print(v.message)
    print(f"record: {prep.run_dir / 'record.jsonl'}")
    return v.exit_code


if __name__ == "__main__":
    sys.exit(main())
