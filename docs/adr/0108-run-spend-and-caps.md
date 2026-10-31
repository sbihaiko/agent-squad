# ADR-0108: Run spend is a measured fact; a cap never starts an entry it cannot fund; the floor is an optional gate

## Status

Accepted.

## Context

A budget is a stall cap and a spend cap, each with a minimum and a maximum ([ADR-0100](0100-node-kinds-gate-control.md)). The run block holds a run spend cap, and an override may only lower it. A proposal may shrink a cap and never raise it. The SDK checks its spend cap after a response, so spend can pass a cap by one response.

## Decision

1. **Node spend counts against the run cap.** Code reads each agent entry's spend from the result message whose `origin` is empty. A subagent's spend is already inside it. The SDK prices a model it does not know at Claude rates. So a gateway model with an entry in the price table (`prices` in `config/agent-defaults.yaml`) spends its tokens at the table rates, and the outcome names the source: `spend_source` is `price_table` or `total_cost_usd`. A model with no entry keeps `total_cost_usd`. Code sums it into `run_spend`, a registered fact. `run_spend_remaining` is derived: the run cap minus `run_spend`.
2. **The `outcome` event carries `spend`, `turns` and the effective cap** passed to the SDK. `turns` is the measured count the SDK reports; no turn cap exists. A stall cap ends the call with no result, so its `spend` is null, and the grant stays as unmeasured spend (ADR-0134). A node killed at its deadline may yield no result. Its event then records `spend: null`, and `run_spend` leaves it out. Unknown spend is never counted as zero. A child with a `call_start` event and no outcome crashed: a resume writes its outcome as fail, reasons `crashed` and `launched_no_outcome`, and keeps its grant as unmeasured spend.
3. **Before each agent entry the engine passes the SDK the lesser of the node's cap and the run remainder.** That is not an engine override of the node's budget. For a priced model the adapter multiplies that amount by the largest ratio of a borrowed rate to the table rate, so the SDK's borrowed price never stops a call before its table spend reaches the cap. Parallel `each` items reserve their spend atomically, so two items never claim the same remainder.
4. **A cap ends the node through its artifact.** The outcome still derives from the artifact. If the artifact is absent or invalid, the outcome is `fail`, reason `node_stall_cap`, `node_spend_cap` or `run_spend_cap`. If it is valid, the outcome is `pass` and the cap reason rides as a diagnostic. Through the real SDK a capped call returns no result text, so a capped node reads `fail`.
5. **The engine starts no agent entry that it cannot fund.** When `run_spend_remaining` is at or below zero, or below the node's spend minimum, the entry does not start. The node ends `fail`, reason `run_spend_cap`, with no session id. Then `next.fail` applies. An author may route it to a wait node. The default fold is the run verdict `failed`.
6. **Spend that passes a cap by one response is measured, not a defect.** Overshoot beyond one response is a defect to investigate (ADR-0018).
7. **A proposer sees spend facts only when its `sees` list names them.** No proposal kind raises a cap (ADR-0103).
8. **The dev_squad floor is an optional gate.** An author may place a gate that compares `run_spend_remaining` to a fixed threshold, with `next.fail` set to `exit:fail` and reason `spend_floor`. Its threshold never adapts. It is the author's choice, not an engine rule.
9. **A cap may also count tokens.** `run.spend_cap_tokens` caps the run, and `budget.spend.max_tokens` caps one call or one child. A token is input plus output, as the outcome records them (`tokens_in` plus `tokens_out`). The dollar cap stays valid, and both apply when both are set. Rules 2 and 5 hold in tokens: unknown tokens keep their grant reserved, and no entry or child starts when the run has no tokens left to grant. The SDK cannot stop a call on tokens, so a call may pass its token grant; the next entry then has less to fund. Gateway models burn a dollar cap unevenly, so tokens are the owner's measure for a child's work.
10. **The validator refuses a graph whose run spend cap is below any agent node's spend minimum.** It also refuses an engine option that lowers the run cap below the first agent node's minimum.

11. **A project ceiling bounds every child's grant, not only the node that names it.** A request that names no spend is
    granted the least of the envelope's max and `max_budget_usd`; a request that names more than that ceiling is refused
    `spend_over_ceiling`, before the remainder answers for it. A router held at the project's $4 spawned a child at the
    envelope's $12 (#960).

Rejected: a cap outcome of `needs-human`, because raising a cap is a graph edit and a changed graph cannot resume. Rejected: the floor as a `when`, because a `when` skips silently and the run could fold `completed` with no work done. Rejected: starting an entry below its minimum.

## Guard

`tests/test_spend.py`, `tests/test_graph.py`, `tests/test_each.py`, `tests/test_dispatch.py`, `tests/test_gateway_price.py`.
