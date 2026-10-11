# squad-goal profile: copy to profiles/<repo-name>.sh (matched against the origin remote) and fill in.
: "${SG_REPO:=owner/repo}"
: "${SG_OWNER:=owner}"                 # GitHub user that owns the Project board
: "${SG_PROJECT:=1}"                   # Project (v2) number of the board
: "${SG_CHECKOUT:=$HOME/src/repo}"     # main checkout the squad runs from
: "${SG_BASE:=main}"
: "${SG_MERGE_METHOD:=squash}"
: "${SG_AUTO_MERGE:=yes}"
: "${SG_E2E_DIR:=$HOME/wt-e2e}"         # detached worktree on the base branch for post-merge e2e
: "${SG_E2E_CMD:=make test}"
: "${SG_E2E_PASS_REGEX:=passed}"
: "${SG_REVIEW_RULES:=the repo's AGENTS.md/CLAUDE.md rules}"
: "${SG_CODING_RULES:=follow AGENTS.md; bug fixes in TDD with the RED output in the PR body}"
: "${SG_ROOT_NAME:=repo}"
# : "${SG_SQUAD:=$HOME/src/agent-squad}"   # unset: the installed agent-squad plugin
: "${SG_AUTO_BUGFIX:=yes}"           # autobug.sh dispatches open bug issues automatically (no = off)
: "${SG_BUG_LABEL:=bug}"
: "${SG_BUG_SKIP_LABELS:=needs-owner,no-auto,wontfix,question}"
# : "${SG_BUG_PROJECT:=2}"; : "${SG_BUG_DOING:=Doing}"   # a separate bug board, if any
: "${SG_FRONTIER_LABEL:=}"             # autofrontier.sh: label of dispatchable issues (empty = off)
# : "${SG_FRONTIER_PREAMBLE:=$here/profiles/<name>.frontier.txt}"   # shared rules prepended to every frontier request
# Fork-only work (forkclose.py): an issue delivered as a branch in a local fork, no PR, closes on merge + fork suite green
# : "${SG_FORK_DIR:=$HOME/src/fork}"; : "${SG_FORK_BASE:=main}"; : "${SG_FORK_BRANCH:=issue/%n}"; : "${SG_FORK_TEST_CMD:=make test}"
# : "${SG_E2E_BUILD_DIR:=$HOME/src/app-build}"; : "${SG_E2E_BUILD_CMD:=make}"   # app build the fork suite drives, rebuilt at the base head before each fork test
# : "${SG_E2E_HEADLESS_DIR:=$HOME/src/app-headless}"   # a SEPARATE tree for the suite's headless (dotnet test) cases, kept at the same base head under the same gui-suite lock; never the SG_E2E_BUILD_DIR tree, whose binary the real-app cases launch (#1414)
