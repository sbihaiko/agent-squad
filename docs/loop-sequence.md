# One issue through the autonomous loop

The trip of one issue from a Todo card to Closed in `dev`: every actor, what each reads and writes, and the failure paths. The doc describes what the code does, defects included, in neutral words. A defect is tracked as an issue, never here. References name a file and a function or step; the code is the source of truth.

Source: `contrib/squad-goal/scripts/*` (live copy `~/.claude/skills/squad-goal/scripts`) and the engine `squad/`. State: `$SG_STATE` (`~/.cache/squad-goal/agent-squad`).
Profile for this repository: `SG_BASE=dev`, `SG_FORK_DIR=$SG_CHECKOUT`, `SG_FORK_BRANCH=gui/%n`, `SG_PROMOTE_PROOF=local`, `SG_BOARD_VOCAB=shipped`.
Shipped columns (`board-definition.json`): Backlog, Todo, Doing, Code Review, Code Complete, Pushing, Testing, Closed, Abandoned. Blocked is no column: it is the `frozen:owner` label over the column the work reached (`board_columns.py`).

`watchd.sh` supervises two lanes:

- **Fast lane** (`fastloop.sh`, every `SG_FAST_INTERVAL` s, 120 by default). Each beat: start one `childcc.py` pass in the background under the `single.sh childcc` lock, unless the pass this lane started still lives; then start `autofrontier.sh` and `autobug.sh` detached. A lane whose supervisor died exits.
- **Slow lane** (`watch.sh`, about 90 s a cycle). Order: token guard; verdicts and wave landings; stall watchdog; `reconcile.py` (every eighth cycle, detached); `branchland.sh`, `branchreviewpass.sh`, `branchfixpass.sh` (detached, each under its own lock, skipped when `SG_LAND_PAUSED` is set); `childcc.py`, `autofrontier.sh`, `autobug.sh` only when the fast lane is down; `autohunt.sh`; `heartbeat.sh`; `wtclean.sh` (every third cycle); `janitor.sh`; `live-guard.sh`; `vendor-guard.sh`.

No cycle step promotes `dev` to `stg`. The owner starts each promotion (ADR-0145 rule 3).

An arrow to `GitHub` is a fetch, a push or an API call. A review, a rebase, a gate and a scratch clone run on the local machine.

## 1. Happy path

```mermaid
sequenceDiagram
    autonumber
    participant O as owner
    participant WD as watchd.sh
    participant FL as fastloop.sh
    participant W as watch.sh
    participant AF as autofrontier.sh
    participant L as launch.py
    participant E as engine (squad start)
    participant C as child agent
    participant CC as childcc.py
    participant CR as childreview.sh
    participant WV as wave.sh
    participant G as gate.sh + fast-gate.sh
    participant J as janitor.py
    participant GH as GitHub (issues, board, dev and wave branches)

    O->>GH: move the card to Todo#59; the issue carries ready-for-agent
    O->>WD: watchd.sh start: clears token-guard.since and token-stop
    WD->>FL: spawn#59; fastloop writes fastloop.pid each beat
    WD->>W: spawn, restart 5 s after an exit
    FL->>CC: each beat, background: single.sh childcc childcc.py over every active run
    FL->>AF: each beat, detached: autofrontier.sh, then autobug.sh
    AF->>GH: open issues through ghcall#59; Todo cards (sg_todo_cards)
    AF->>AF: candidate: SG_FRONTIER_LABEL, card in Todo, no skip label, no open work branch, not live#59; stack.sh READY
    AF->>AF: write auto/disp-N#59; mkreq.sh builds the request
    AF->>L: launch.py REQ, detached
    AF->>GH: board --add Doing#59; a refused write is named
    L->>GH: fetch origin/dev into the checkout#59; a failed fetch refuses the start
    L->>L: target: one issue named, worktrees/child/gui-N on gui/N from origin/dev#59; else worktrees/run/KEY on squad/run/KEY from origin/dev
    L->>E: Popen squad start --keep-children --target TARGET, cwd TARGET
    E->>E: run_start#59; run folder TARGET/runs/RUN
    L->>L: wait up to 180 s for the run folder#59; write request.txt
    L->>WV: wave.sh start RUN: exit printed, never enforced
    WV->>GH: scratch clone: push dev to wave/RUN#59; markers wave-, waved-, wave-start-
    L->>L: append the absolute run folder to active-runs.txt
    E->>E: router: spawn_requested, spawn_accepted
    E->>C: worktree RUN/ws/LOGICAL-CHILD on squad/RUN_ID/SLUG, cut from the target HEAD
    C->>C: edits, commits "Refs #N", prepush --local#59; pushes nothing
    E->>E: outcome pass#59; Dispatch.land merges into the target#59; land event with before and head
    CC->>CC: pending: child with outcome pass, issue named, no cc- marker#59; cursor picks one child (SG_CHILDCC_BUDGET 1)
    CC->>GH: issue state open?
    CC->>CR: once per head (rvd- marker): childreview.sh RUN N in the child worktree
    CR->>GH: fetch wave/RUN
    CR->>CR: review.sh --worktree origin/wave/RUN: review-KEY.md, .sha, .base
    CC->>WV: wave.sh enter RUN N, cwd the child worktree
    WV->>WV: not expired#59; review APPROVE, base wave/RUN, sha HEAD
    WV->>GH: fetch wave/RUN#59; rebase on its tip
    WV->>G: SERIAL_TRY=1 gate.sh SG_WAVE_GATES: fast-gate.sh on the rebased tree
    WV->>GH: push HEAD:wave/RUN, fast-forward only#59; one repeat after a fresh rebase
    WV->>GH: card Code Complete#59; marker entered-RUN-N
    CC->>CC: touch cc-RUN-CHILD#59; queue the cq comment
    E->>E: verdict#59; prune keeps the child trees (keep_children)
    W->>W: read the verdict from TARGET/runs/RUN/record.jsonl
    W->>CC: land_wave: single.sh childcc childcc.py RUN, wait up to SG_LAND_WAIT polls
    CC-->>W: 0 all children had their turn#59; 3 children left for a later pass
    W->>WV: on 0: single.sh wland wave.sh land RUN FOLDER
    WV->>GH: scratch clone fetches dev and wave/RUN
    WV->>WV: rebase on origin/dev#59; wave_check: SERIAL_TRY=1 tip gate
    WV->>GH: push HEAD:dev, fast-forward only#59; delete wave/RUN
    WV->>GH: deliver: PATCH closed, completed#59; landed.tsv row#59; cq delivery#59; card Closed
    W->>W: echo RUN verdict#59; per-issue cq comment#59; mark seen
    W->>J: janitor.sh every cycle
    J->>GH: rule R: issue closed, card Closed (Done mapped by board_columns.py)
    O->>G: by hand: promote-pass.sh, or the full gate then promote-stg.sh
    G->>GH: full suite in both modes on the dev tip#59; gate-status-full#59; push dev tip to stg, fast-forward only
    W->>W: live-guard.sh: main tree on stg, ff to origin/stg, publish-live.sh file list
```

## 2. Step table

The column is the one the writer asks for. A board write that the API refuses leaves the card where it was: the wave entry drops the refusal; the frontier, autobug and the wave landing name it.

| step | actor | reads | writes | column asked |
|---|---|---|---|---|
| 1 | owner | - | card to Todo; `watchd.sh start` (watchd.pid, watch.log) | Todo |
| 2 | fastloop.sh | `SG_FAST_INTERVAL` (`env.sh`); its own childcc pid | fastloop.pid every beat; one background childcc pass; both dispatch passes, detached | - |
| 3 | autofrontier.sh | open issues (ghcall, one aggregate read); Todo cards; labels: needs `SG_FRONTIER_LABEL`, skips the bug label and `SG_FRONTIER_SKIP_LABELS` (spec, needs-owner, needs-ratification, needs-info, frozen:owner, frozen:dependency, wontfix); disp-/bug- markers; live issues in active runs (`startability.sh`); remote work branches; `stack.sh`; caps, envelope and `fundroom.py` | auto/disp-N; req/NAME.body; mkreq.sh request; cq comment; card Doing | Doing |
| 3b | autobug.sh | the same, over bug issues (go label and Todo card too) | auto/bug-N; card Doing, and the bug board when `SG_BUG_PROJECT` is set | Doing |
| 4 | launch.py | `origin/$SG_BASE`, fetched now (`_landing_base`); the request's issues (`_issue_named`) | target: the issue worktree `worktrees/child/gui-N` when the request names one issue (`_issue_worktree`; a reused tree is reset to the base, extra commits kept on `refs/squad/leftover/*`, a dirty tree falls back), else its own `worktrees/run/KEY` on `squad/run/KEY` (`_run_worktree`); the engine process with `--keep-children`; `TARGET/runs/RUN/request.txt`, `launch.log`; one active-runs.txt line, absolute | Doing |
| 5 | wave.sh start | origin/dev, origin/wave/RUN (scratch clone) | remote wave/RUN; auto/wave-RUN, waved-RUN, wave-start-RUN | Doing |
| 6 | engine router | request text | record: spawn_requested, spawn_accepted | Doing |
| 7 | child agent | its worktree `RUN/ws/LOGICAL-CHILD`, cut from the target HEAD (`Children.worktree`) | commits "Refs #N" on `squad/RUN_ID/SLUG`; no push | Doing |
| 8 | engine Dispatch.land | child outcome pass; workspace exists | merge into the target branch; land event with `effect.before` and `head` | Doing |
| 9 | engine verdict | record fold | verdict; `prune` keeps child trees for a `keep_children` run | Doing |
| 10 | childcc.py | active runs, or the run watch.sh hands it; spawn_accepted and outcome pass; issue state (gh); wave-/waved- markers; card column for a backfill; requeued-, rvd- markers; auto/childcc-cursor, childcc-round | rvd-RUN-CHILD (head and pid); cc-RUN-CHILD; cursor and round; cq comment; card Code Complete only for a run with no wave; exit 3 when children wait | Code Complete (no wave) |
| 11 | childreview.sh | child HEAD; origin/wave/RUN; review chain (step 11b) | review-KEY.md, .sha (review.sh), .base; cq status comment | - |
| 11b | review.sh | `review_models` of the `models.json` beside the script (the live copy is machine config), in config order (`review_order: config`) | the first model that answers with a verdict wins. Skipped: codex while `$SG_SHARED/codex-limited` holds a future time; gemini (agy) while `agy-limited` does; an `anthropic_models` entry while `quota-guard` exists; a model switched off in the hub; a model outside its `hours` window | - |
| 12 | wave.sh enter | wave-start-RUN age; review-KEY.{md,base,sha}; origin/wave/RUN, rebased on before the gates; `SG_WAVE_GATES` | gate-status fast line (gate.sh); remote wave/RUN tip; card Code Complete; auto/entered-RUN-N | Code Complete |
| 13 | watch.sh | verdict in the record; wave-RUN marker; wave age (`wave.sh expired`) | wland-RUN and its `.pass`/`.left` marks, transient; watch-seen.txt | - |
| 14 | wave.sh land | origin/dev, origin/wave/RUN; `Refs #N` in commits ahead of dev; the run record (`run_issues`) for a capped or empty wave; auto/wowed-RUN | remote dev tip; wave/RUN deleted; wave-start-, wland-tries- removed; issues closed; landed.tsv; cq delivery; card Closed; wave-RUN removed once every issue closed | Closed |
| 15 | GitHub CI | push to dev (`ci.yml`) | check runs on the dev tip | - |
| 16 | wtclean.sh (every third cycle) | child trees under `*/runs/RUN/ws/*`; verdict or zombie; wave hold (`wave-RUN`, capped at `SG_WAVE_HOLD_MAX_SECONDS`); clean tree; HEAD in the base or on a remote or other branch; issue trees under `worktrees/child/*` | removes those trees and throwaway branches; wt tally | - |
| 17 | janitor.py R | run records (active runs and run folders under the checkout, `worktrees/run/*` and `worktrees/child/*`); entered- markers; issue state; board cards, read once; remote branches; review files | board PATCH only on a mismatch, highest rank wins; Run fields; cycle.log | Closed |
| 18 | owner | the dev tip | `promote-pass.sh`: full suite in a clean clone under the gate lock (try mode), gate-status-full, commit status `squad/full-proof`, then `promote-stg.sh` | - |
| 19 | promote-stg.sh | gate-status-full (`SG_PROMOTE_PROOF=local`) or the dev tip's CI (`ci`), under the gate lock | stg ref, fast-forward only; promote.log once per tip and reason | - |
| 20 | live-guard.sh | main tree branch is stg and clean; fetch origin/stg; `publish-live.sh --check` | ff of the main tree; the file list into `~/.claude/skills/squad-goal` | - |

### Run folders

A loop run keeps its folder inside its target: `worktrees/child/gui-N/runs/RUN` or `worktrees/run/KEY/runs/RUN`. The owner's checkout is never written. active-runs.txt holds the absolute path. The loop consumers read every place a run folder can be: `<checkout>/runs`, `worktrees/run/*/runs`, `worktrees/child/*/runs` (`tokens.py`, `janitor.py`, `health.sh`, `hub.py run_roots`, `wtclean.sh`).

### The childcc pass

One pass works at most `SG_CHILDCC_BUDGET` children (1 by default). A child costs budget only when its entry reaches the review and the gates. A closed issue, a landed wave, a backfill skip and a requeue held on the same head cost nothing. The pass starts after the child that `auto/childcc-cursor` names. `auto/childcc-round` holds the children worked since the last complete pass. A pass that leaves a child with no turn in the round exits 3. On 3, `land_wave` defers the landing and keeps the run unread.

### Gate lock

`gate.sh` takes the machine-wide `gate` lock through `serial.sh`. The wave entry and the wave tip check take it in try mode (`SERIAL_TRY=1`): a held lock exits 73 at once, never queued, and counts nothing toward `stuck`. The entry prints `WAVE HOLD` and the child enters on a later pass. The landing prints `WAVE WAIT` and the wave lands on a later cycle. `promote-pass.sh` takes the lock in try mode too. A hand-started gate waits.

### Spec cards

A spec is never work (ADR-0142). `/to-spec` does not label it `ready-for-agent`, and the frontier skips the `spec` label. The janitor folds a spec's column over its sub-issues (rank 15, forward only, nothing below Doing). When the owner moves a spec card to Todo, the janitor moves each open sub-issue whose card is in Backlog or has no column to Todo (rank 12). A spec with at least one sub-issue, all closed, is closed with one comment.

## 3. Failure paths

```mermaid
sequenceDiagram
    participant AF as autofrontier.sh
    participant L as launch.py
    participant E as engine
    participant CC as childcc.py
    participant WV as wave.sh
    participant W as watch.sh
    participant J as janitor.py
    participant LG as live-guard.sh
    participant GH as GitHub
    Note over AF,L: before a run
    AF->>AF: mkreq.sh refuses a slice: the contested tickets are deferred, their disp- markers freed, the rest rebuilt#59; a whole-batch refusal frees every marker
    L->>L: fetch of origin/dev fails or names no commit: refuse the start, no tree made
    L->>L: worktree add fails three times: refuse the start
    L->>L: squad exits before a run folder, or none in 180 s: exit, no wave, no active-runs line
    L->>WV: wave.sh start fails: printed, the run goes on#59; no wave markers, so childcc moves the card itself
    J->>GH: rule J: no record 5 min after the request: free the markers#59; rule K: request log says refusing to start: drop disp-N
    Note over E: inside the run
    E->>E: spawn_rejected: request_malformed, max_children_over, envelope errors, after_unmet, spend_over_remainder
    E->>E: child fail: spend cap, deadline, child_workspace_failed, adapter error#59; a failed check turns a pass into a fail
    E->>E: land fail: land_workspace_missing, land_timeout, merge_conflict#59; ac_regression resets the target
    Note over E,CC: childcc reads the child outcome, never its land event: a child whose engine landing failed still enters the wave
    Note over CC,WV: entry refused: gates red, rebase conflict, push refused twice, wave expired
    WV->>GH: requeue: rm disp-N, bug-N#59; card Todo#59; cq why#59; at SG_REQUEUE_CAP (3) frozen:owner and SQUAD-NOT-STARTABLE instead
    CC->>CC: requeued-RUN-CHILD = HEAD, no cc- marker: no new try on the same head
    Note over CC,WV: entry held: no review, wrong base, other sha, not APPROVE, gate lock held
    WV-->>CC: exit 1 WAVE HOLD, nothing requeued
    CC->>WV: review says CHANGES: wave.sh requeue N with the first finding
    CC->>CC: no cc- marker: the next pass tries again
    W->>WV: on the verdict cycle the landing runs with the child out of the wave
    WV->>GH: nothing ahead of dev: delete wave/RUN#59; deliver each dispatched issue whose non-RED Refs commit dev holds#59; exit 0
    CC->>CC: later passes see waved- without wave-: cc- marker written, no entry
    J->>GH: R: the child's engine landing ranks Code Complete#59; the work sits in the run target only
    Note over W,CC: childcc pass incomplete
    CC-->>W: exit 3: the landing waits, the run stays unread
    W->>W: childcc lock held past SG_LAND_WAIT, or the pass failed: the run stays unread
    Note over WV,GH: landing fails
    WV->>WV: rebase on dev conflicts, push refused twice, base or wave branch gone: stuck() counts wland-tries
    WV->>GH: at SG_WAVE_LAND_TRIES (3): frozen:owner per issue, cq comment, rm wave-RUN#59; wave/RUN stays on the remote
    WV->>WV: gate lock held at the tip check: WAVE WAIT, exit 1, no count
    Note over WV: red tip
    WV->>WV: wave_check: child_gate per commit, newest first#59; drop the culprit by rebase --onto#59; requeue it
    WV->>WV: no culprit, or the drop conflicts: stuck()
    Note over WV,GH: close refused
    WV->>GH: PATCH closed refused twice: no cq, no ledger row, no card move#59; auto/wowed-RUN keeps the issue owed
    WV-->>W: exit 1: wave-RUN stays, the run stays unread#59; the next landing only closes the owed issues
    Note over W,WV: wave age cap (SG_WAVE_MAX_AGE_SECONDS, 7200 s)
    W->>WV: expired and no verdict: land_wave now
    WV->>GH: enter refuses a child that arrives after the cap: requeue
    WV->>GH: land lands what entered#59; requeues each run issue the wave did not carry
    Note over W,J: run needs-human or failed
    J->>GH: R: a failed or completed run with nothing landed: Todo#59; needs-human with nothing landed: frozen:owner#59; a Closed or Abandoned card is never frozen
    J->>J: rule O drops disp-N of a run that landed nothing#59; rule C kills a zombie run
    Note over W: token cap
    W->>W: token-guard.sh exits 1 with a sentence: cycle.log, token-stop, watchd.sh stop, exit
    Note over LG: live publication
    LG->>LG: main tree not on stg, a shipped file dirty, fetch failed, or diverged: named, nothing published
```

## 4. Edges the code leaves open

- An empty wave returns before the age-cap requeue. A capped run whose children never entered gets no requeue for an issue that dev does not hold.
- No step removes a run worktree `worktrees/run/KEY` or its branch `squad/run/KEY`. `wtclean.sh` removes child trees under its `runs/RUN/ws` and the issue trees under `worktrees/child`.
- The wave entry drops the output of its Code Complete board write, so a refused write is not named.
