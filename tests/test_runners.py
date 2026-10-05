"""The runner registry covers every node kind in the graph (ticket #121)."""
from squad.graph import KINDS
from squad.runners import default_registry


def test_every_graph_kind_has_a_runner():
    missing = set(KINDS) - set(default_registry())
    assert not missing, f"node kinds with no runner: {sorted(missing)}"


def test_every_runner_serves_a_graph_kind():
    assert set(default_registry()) <= set(KINDS)


def test_dispatch_state_lives_in_the_dispatch_module_not_the_engine():
    """Ticket #124: children, landed, and ac_before belong to Dispatch; the engine keeps none of them."""
    import inspect

    from squad.engine import Engine
    from squad.runners.dispatch import Dispatch, DispatchRunner
    src = inspect.getsource(Engine)
    for attr in ("self.children", "self.landed", "self.ac_before", "def _run_dispatch", "def _land", "def _decide"):
        assert attr not in src, f"the engine still holds {attr}"
    d = Dispatch(object())
    assert (d.children, d.landed, d.ac_before) == ({}, {}, None)
    body = inspect.getsource(DispatchRunner.run).strip().splitlines()
    assert len(body) < 30, f"the dispatch runner is {len(body)} lines"


def test_the_agent_runner_owns_shadow_prompt_and_proposals_and_the_engine_does_not(  # ticket #122
):
    import inspect

    from squad import session
    from squad.engine import Engine
    from squad.runners import agent
    for name in ("_run_agent", "_prompt", "_agent_outcome", "_shadow", "_apply_proposals"):
        assert not hasattr(Engine, name), f"Engine still defines {name}"
    assert isinstance(default_registry()["agent"], agent.AgentRunner)
    assert hasattr(session, "agent_outcome")
    assert len(inspect.getsource(agent.AgentRunner.run).splitlines()) < 30


def test_child_execution_lives_in_the_children_module_and_the_engine_does_not(  # ticket #125
):
    import inspect

    from squad.engine import Engine
    from squad.runners.children import Children
    for name in ("_run_children", "_run_one", "_wait_after", "_child_session", "_child_outcome", "_run_checks", "child_workspace",
                 "_child_worktree", "_child_isolated", "_child_snapshot", "run_children_of"):
        assert not hasattr(Engine, name), f"Engine still defines {name}"
    body = inspect.getsource(Children.session).strip().splitlines()
    assert len(body) < 30, f"the child session is {len(body)} lines"
    for step in ("prepare", "request", "close", "settle", "check", "worktree", "snapshot"):
        assert callable(getattr(Children, step))
