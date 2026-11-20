# squad-goal profile: copy to profiles/<repo-name>.sh (matched against the origin remote) and fill in.
: "${SG_REPO:=owner/repo}"
: "${SG_OWNER:=owner}"                 # GitHub user that owns the Project board
: "${SG_PROJECT:=1}"                   # Project (v2) number of the board
: "${SG_CHECKOUT:=$HOME/src/repo}"     # main checkout the squad runs from
: "${SG_BASE:=dev}"                   # the branch a wave is cut from and lands in
# The branch the promotion merges into, `stg` unless named: a repository that promotes dev into `staging` sets it here (#1646).
# : "${SG_STG_BRANCH:=staging}"
# A profile that delivers by another path turns the promotion pass off: it exits 0 before any fetch and grows no log line (#1646).
# : "${SG_PROMOTE:=off}"
: "${SG_MERGE_METHOD:=squash}"
: "${SG_AUTO_MERGE:=yes}"
# : "${SG_PROMOTE_PROOF:=local}"         # promote-stg.sh: the promotion proof. `ci` (unset: the default) takes the dev tip's CI; `local` takes the promotion gate's own status line, the full suite in both modes, for when CI cannot start (#439)
: "${SG_E2E_DIR:=$HOME/wt-e2e}"         # detached worktree on the base branch for post-merge e2e
: "${SG_E2E_CMD:=make test}"
: "${SG_E2E_PASS_REGEX:=passed}"
: "${SG_REVIEW_RULES:=the repo's AGENTS.md/CLAUDE.md rules; a view constructs, binds and delegates - it never reaches a file, a client, application state or a domain service, and each rule lives where no host is needed to test it (ADR-0137)}"
# Path prefixes, space-separated, left out of every review diff: a tracked file a hook or a tool regenerates (#816).
: "${SG_REVIEW_EXCLUDE:=}"
: "${SG_CODING_RULES:=follow AGENTS.md; bug fixes in TDD with the RED output in the issue status comment; a view constructs, binds and delegates - keep the rule in the layer the host-free test project compiles, never read a file, open a client, mutate application state or instantiate a domain service from a view (ADR-0137)}"
: "${SG_ROOT_NAME:=repo}"
# : "${SG_WAVE_GATES:=uv run --extra dev ruff check . && uv run --extra dev mypy && uv run --extra dev pytest -n auto -m \"not heavy\"}"   # wave.sh: the local gates a child runs in its own worktree before it enters the wave branch (env.sh's default is this repository's; an empty value refuses the entry)
# The gate commands a child's request names (#740; address.sh sg_gate_cmds). Unset: agent-squad's own scripts where the checkout
# holds them, else none (the request then says no gate is configured). An empty value is none. The prepush command prints nothing when clean.
# SG_FAST_GATE_CMD='make check'   SG_PREPUSH_CMD=''   SG_SUITES_HINT='the targeted cases (`make test K=<case>`)'   SG_FULL_GATE_HINT='`make test`'
# : "${SG_WAVE_MAX_AGE_SECONDS:=7200}"   # wave.sh: a wave older than this (seconds) lands what it holds; the children still running go back to the queue (env.sh's default is two hours)
# : "${SG_WAVE_TIP_GATES:=...}"          # wave.sh: the wave tip's own check (#377), run in the wave's throwaway clone; a red one makes each child's suite run again against the tip, last entrant first. Unset: the fast gate. Empty: the wave's CI is its only check
# nightly.sh: the full suite once a day, one command per mode, run in the nightly tree. Unset: pytest, and a pytest that collects
# no test (exit 5) counts as no suite, not red (#758). A repository without a Python suite names its own, e.g. `make clean all`.
# : "${SG_NIGHTLY_PARALLEL:=make test}"; : "${SG_NIGHTLY_SERIAL:=make test}"
# : "${SG_NIGHTLY_HOUR:=04}"   # the hour (America/Sao_Paulo) the nightly suite starts in; the dispatch stops in it, and the suite never starts outside it
# : "${SG_NIGHTLY_DRAIN_MINUTES:=40}"   # the dispatch stops this long before the hour, so the live runs end before it opens (#2527)
# : "${SG_NIGHTLY_NEED_MINUTES:=45}"   # the suite never starts with less time left in the hour, and the dispatch hold ends then (#2527)
# The checks the nightly settles a proof obligation with (spec 1163). Unset: the fast gate's battery over this repository's
# layout (ruff and mypy over `squad`/`tests`, then the full serial pytest), and a tree that does not hold those paths has
# nothing measured: its obligation is reported unmeasured, never settled red. Another layout names its own here.
# : "${SG_SETTLE_RUFF:=make lint}"; : "${SG_SETTLE_MYPY:=make typecheck}"; : "${SG_SETTLE_PYTEST:=make test}"
# : "${SG_NIGHTLY_PERIOD_SECONDS:=86400}"   # how long a settled red from a pass that cannot read the tip still holds (one day)
# : "${SG_SQUAD:=$HOME/src/agent-squad}"   # unset: the installed agent-squad plugin
: "${SG_AUTO_BUGFIX:=yes}"           # autobug.sh dispatches open bug issues automatically (no = off)
: "${SG_BUG_LABEL:=bug}"
# : "${SG_BUG_SKIP_LABELS:=needs-owner,no-auto,wontfix,question}"   # the default, with SG_FRONTIER_SKIP_LABELS, lives in skiplabels.sh (#1593)
# : "${SG_BUG_PROJECT:=2}"; : "${SG_BUG_DOING:=Doing}"   # a separate bug board, if any
: "${SG_FRONTIER_LABEL:=}"             # autofrontier.sh: label of dispatchable issues (empty = off)
# : "${SG_FRONTIER_PREAMBLE:=$here/profiles/<name>.frontier.txt}"   # shared rules prepended to every frontier request
# Fork-only work (forkclose.py): an issue delivered as a branch in a local fork, no PR, closes on merge + fork suite green
# : "${SG_FORK_DIR:=$HOME/src/fork}"; : "${SG_FORK_BASE:=main}"; : "${SG_FORK_BRANCH:=issue/%n}"; : "${SG_FORK_TEST_CMD:=make test}"
# : "${SG_E2E_BUILD_DIR:=$HOME/src/app-build}"; : "${SG_E2E_BUILD_CMD:=make}"   # app build the fork suite drives, rebuilt at the base head before each fork test
# : "${SG_E2E_HEADLESS_DIR:=$HOME/src/app-headless}"   # a SEPARATE tree for the suite's headless (dotnet test) cases, kept at the same base head under the same gui-suite lock; never the SG_E2E_BUILD_DIR tree, whose binary the real-app cases launch (#1414)
