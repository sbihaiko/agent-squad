"""SPEC 505: a live contract test proves each registry entry answers on its route.

It makes one small model call per live entry, so it is opt-in: it is `heavy` (the fast battery and CI run
`-m "not heavy"`) and it skips unless SQUAD_LIVE_CONTRACT is set. Run it by hand:

    SQUAD_LIVE_CONTRACT=1 uv run --extra dev pytest tests/test_model_contract_live.py -q -p no:deepeval -rs

Each case prints one line: entry, route, status. The selection and reporting logic is proved with a stub in
`tests/test_contract_probe.py`. A closed usage window skips its case with that reason; it is never a pass.
"""
import os

import pytest

from squad import contract_probe as cp

OPT_IN = "SQUAD_LIVE_CONTRACT"
DEADLINE = 45

pytestmark = [
    pytest.mark.heavy,
    pytest.mark.slow(reason="one live model call per entry, bounded by its own deadline"),
    pytest.mark.skipif(not os.environ.get(OPT_IN), reason=f"live model calls: set {OPT_IN}=1 to run"),
]

PROBES = cp.plan()


@pytest.mark.parametrize("probe", PROBES, ids=[p.entry_id for p in PROBES])
def test_the_entry_answers_on_its_route(probe, capsys):
    result = cp.run(probe, cp.subprocess_runner, DEADLINE)
    with capsys.disabled():
        print(f"\n{result.line}")
    if result.status == "closed":
        pytest.skip(result.line)
    assert result.status == "ok", result.line
