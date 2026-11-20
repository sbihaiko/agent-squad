# Two landings run at once, and git arbitrates the push

A wave lands in one push to the base. The pass holds a machine-wide lock, `single.sh wland`, from its rebase to its push,
so one wave lands at a time. The tip check inside the pass runs the full pytest battery and the shell suites of the wave:
about ten to fifteen minutes. A wave whose tip check fails spends that time on every attempt, and each attempt takes the
only landing slot again.

On 2026-11-20 the wave of #2490 failed its tip check three times on tests its child did not update. Its landing held the
lock from 18:52 to 20:19, and the waves of #2592 and #2597 waited the whole time. The landing cadence follows that lock:
thirty-five landings in the day, at a median gap of forty-two minutes.

The lock exists for one reason: the push to the base must be a fast-forward. Git already refuses a push that is not one, and
the pass already repeats from its fetch when a push is refused, so the base's move is a race git settles and the lock does
not need to settle alone.

## Decision

1. `single.sh` takes one of `SG_SINGLE_SLOTS` slots, one by default. Slot one keeps today's lock name
   (`locks/single-<name>`); a later slot carries its number (`locks/single-<name>-2`). A lock whose holder is dead is
   reaped, one slot at a time, as before.
2. A landing passes two slots (`SG_LAND_PARALLEL`, default 2). Two waves rebase, check and push at the same time.
3. The push needs no lock of its own. Git refuses a push that is not a fast-forward, and the refused pass repeats from its
   fetch, rebases on the moved base and checks again, once, as it already does.
4. A tip check takes one gate slot ([ADR-0164](0164-delivery-outranks-the-proof.md),
   [#2604](https://github.com/sbihaiko/agent-squad/issues/2604)), so two checks and the child gates fit in `caps.gates` 4.
5. The readers of the landing lock read every slot: the watch cycle names the holder of a slot it did not get, and health
   alerts on a slot whose holder outlived the gate wait plus the gate bound.

## Consequences

- A wave that loses the push race pays one more tip check. The gate reuses a green verdict for the same tree and suite
  ([ADR-0168](0168-a-gate-stage-reuses-a-green-verdict.md)), and a rebase that touches no selected file keeps the tree, so
  the cost shows only when the two waves change the same files.
- Two tip checks load the machine more. The count is a setting, and one is the behavior of the day before.
- A pass that loses the race and fails its second push ends as it does today: `stuck` names the refusal, and the wave lands
  on a later cycle.
