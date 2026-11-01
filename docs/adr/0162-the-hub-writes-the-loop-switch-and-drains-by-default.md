# ADR-0162: The hub writes the loop switch, drains by default, and the tailnet reads it unless granted

## Status

Accepted, on one condition: tailnet writes stay off by default, and only the owner turns them on. [ADR-0152](0152-reach-the-web-surfaces-over-the-tailnet.md), rule 9, holds the tailnet side of this decision.

## Context

The owner starts and stops the loop from the hub. The hub has read the loop state only, and a stop kills every live run and gate at once. A run and a gate that stop mid-work leave a branch, a lock and a record to recover. The tailnet reaches the hub through one rule ([ADR-0152](0152-reach-the-web-surfaces-over-the-tailnet.md)), and a write from it needs its own grant, as the phone's session send does ([ADR-0154](0154-the-phone-sends-to-a-session-by-the-wire-protocol.md)).

## Decision

1. **One route.** The hub serves `GET /api/loop`, which returns the state, who changed it and when, the live runs and gates, the change log, and whether this request may write, with the reason when it may not. `POST /api/loop` takes a JSON body with one action: `start`, `drain` or `stop-now`. The route runs the supervisor script and nothing else.
2. **Four states.** `on` and `draining` are read from the live supervisor and its drain mark. `starting` is a start that has just been asked and has no supervisor yet. `off` is every other case. Only the route and the supervisor write them.
3. **Drain is the default.** A drain pauses dispatch and marks itself. A detached waiter stops the loop only when no run lacks a verdict and no gate lock holds a live pid. A drain never sends a stop while live work remains, and a supervisor that dies during a drain ends the waiter.
4. **Stop-now is the second choice.** It needs `"confirm": true`. Its confirm lists the live runs and gates it kills, and its response and audit line name what it killed.
5. **Audit.** Every change the route runs appends one line to the loop audit file in the state directory: who, when, from where (loopback or tailnet), the action and, for a stop-now, what it killed. A refused request writes nothing.
6. **Tailnet.** A tailnet request may read the route. A write from the tailnet is refused, with its reason, unless the profile sets `tailnet.loop_write` (`SQUAD_TAILNET_LOOP_WRITE`) to true. The shipped default is false, and only the owner sets it. The grant holds for every allowed login of the profile, as `tailnet.terminals_send` does.
7. **Three places show one view.** The alert strip, Settings > Loop and the phone's Status panel read the same route. Each shows the state, who changed it and when; Settings > Loop and the phone also show the change log. The switch has no Save: a change is one request. The reason a tailnet request is read-only is shown beside the switch, and Settings > Access shows the tailnet rule itself.

Guard: `tests/test_hub_loop_switch_page.py`, `contrib/squad-goal/scripts/test-hub-loop-switch.sh`, `contrib/squad-goal/scripts/test-watchd-drain.sh`, `contrib/squad-goal/scripts/test-hub-mobile-loop.sh`, `tests/test_tailnet_env.py`, `tests/test_tailnet_settings.py`.
