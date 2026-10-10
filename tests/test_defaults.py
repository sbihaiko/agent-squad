from pathlib import Path

from squad import defaults


def test_the_defaults_file_is_sound():
    assert defaults.check(defaults.load()) == []


def test_every_role_file_has_a_role_entry():
    roles = defaults.load()["roles"]
    files = {p.stem for p in Path("prompts").glob("*.md")} - {"_contract", "_autonomy", "README"}
    assert files == set(roles)


def test_a_role_key_outside_options_is_a_defect():
    cfg = defaults.load()
    cfg["roles"]["actor"]["bogus"] = 1
    assert defaults.check(cfg) == ["roles.actor.bogus"]


def test_a_missing_cap_is_a_defect():
    cfg = defaults.load()
    del cfg["limits"]["max_stalls"]
    assert defaults.check(cfg) == ["limits.max_stalls"]


def test_a_read_only_role_drops_the_write_tools():
    roles = defaults.load()["roles"]
    for r in ("scout", "critic", "router"):
        assert {"Edit", "Write"} <= set(roles[r]["disallowed_tools"])
    assert {"Edit", "Write"}.isdisjoint(roles["actor"]["disallowed_tools"])


def test_run_deadline_may_be_unset_but_not_zero():
    cfg = defaults.load()
    assert cfg["run"]["deadline"] is None and defaults.check(cfg) == []
    cfg["run"]["deadline"] = 0
    assert defaults.check(cfg) == ["run.deadline"]


def test_call_env_carries_the_output_and_context_limits():
    env = defaults.call_env(defaults.load())
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "32000"
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "200000"
    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "3"


def test_a_missing_token_limit_is_a_defect():
    cfg = defaults.load()
    del cfg["limits"]["context_tokens"]
    assert defaults.check(cfg) == ["limits.context_tokens"]


def test_compaction_must_be_logged():
    cfg = defaults.load()
    cfg["hooks"]["log_compaction"] = False
    assert defaults.check(cfg) == ["hooks.log_compaction"]


def test_no_role_may_start_a_subagent():
    cfg = defaults.load()
    assert {"Task", "Agent"} <= set(cfg["options"]["disallowed_tools"])
    for r, v in cfg["roles"].items():
        assert {"Task", "Agent"} <= set(v["disallowed_tools"]), r
        assert "code-review" not in v.get("skills", []), r


def test_a_context_window_below_the_probed_minimum_is_refused():
    cfg = defaults.load()
    cfg["limits"]["context_tokens"] = 50000      # the CLI clamps it up to 100000 (probe 2026-09-30)
    assert "limits.context_tokens" in defaults.check(cfg)


def test_autonomy_defaults_are_on_with_three_resumes_and_sound():
    cfg = defaults.load()
    assert defaults.autonomy(cfg) == {"enabled": True, "max_resumes": 3} and defaults.check(cfg) == []


def test_autonomy_values_outside_the_range_are_defects():
    for key, val in (("max_resumes", 11), ("max_resumes", -1), ("max_resumes", True), ("max_resumes", 1.5), ("enabled", "yes"), ("enabled", 1)):
        cfg = defaults.load()
        cfg["autonomy"][key] = val
        assert defaults.check(cfg) == [f"autonomy.{key}"], (key, val)
    cfg = defaults.load()
    cfg["autonomy"]["max_resumes"] = 0
    cfg["autonomy"]["enabled"] = False
    assert defaults.check(cfg) == []
