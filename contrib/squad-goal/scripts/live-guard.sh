#!/bin/bash
# usage: live-guard.sh   Keeps the live skill ($HOME/.claude/skills/squad-goal) in step with the repository copy this
# checkout ships, and names the difference it read. The watch cycle runs it every cycle (single.sh liveguard).
#
# The live copy is what the loop and the owner's info bar run, and it followed the repository by hand: the repository
# dropped the `health:` and `last progress` blocks from the hub footer, and the bar kept printing them for as long as
# nobody published. So the drift is read, never assumed: this pass asks publish-live.sh --check for one `copy`/`drop`
# line per difference, prints what it read, publishes it, and reads again. A difference still there after the publish is
# a fact the cycle cannot leave unread, and it is named as one.
#
# A publish records the tested sha, the source HEAD, in the live PUBLISHED file, and the pass names it (#700). The
# drivers keep running the files they loaded at start until the owner restarts them, so the pass compares that build with
# the published tree for those files only: the lanes and the files they source once (#2019). A per-call script starts a
# new process each call, so the publish already reaches it. A changed in-memory file names the restart once per publish,
# with the file; with none changed the pass records the new build as current and names no restart. It never starts or
# stops a process. Live models.json is owner config: the pass never compares it
# with the repository, and names a hand edit once (see notes below).
#
# The rule of the publish is untouched: an explicit FILE LIST, never a mirror. Only the names the repository ships cross
# over, the names it retired are dropped by name, and the live copy's machine config (scripts/models.json, the per-repo
# profiles under scripts/profiles/, $SG_STATE) is never one of the names -- so this pass never builds a path of its own
# and never removes a file no one named.
#
# Source: the checkout this profile works in, when it carries the skill ($SG_CHECKOUT/contrib/squad-goal, named by its
# own SKILL.md). A profile whose checkout is another repository ships no copy of this skill and the pass says nothing.
# Destination: publish-live.sh's own default ($SG_LIVE_DIR, else $HOME/.claude/skills/squad-goal).
# Exit 0 always: a drift, and a publish that could not land, are reported, never raised as a failure of the cycle.
#
# The cycle runs this pass every cycle, and the publish check walks both trees (~2 s here). So the pass keeps a key of
# its input in $SG_STATE and returns at once when the key is unchanged (#568): no check, no publish, no write. The key
# is the tree's HEAD and status, origin/stg after the fetch, and the live copy's own state -- a hand edit of the live
# copy alone still runs the pass. The fetch stays above the skip: only the publish check after it is skipped. The key
# is kept after a pass that resolved every difference, never after one the publish could not land, so a drift the
# publish cannot resolve is named every cycle (ADR-0140). A checkout with no git has no cheap key and is read as before.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
live=${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}
repo=${SG_CHECKOUT:-}/contrib/squad-goal
[ -n "${SG_CHECKOUT:-}" ] && [ -f "$repo/SKILL.md" ] || exit 0                  # this checkout does not ship the skill
[ -d "$live" ] || exit 0                                                       # no live copy on this machine: nothing to keep in step
digest() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }
mode_of() { if stat -f '%Lp' / >/dev/null 2>&1; then xargs -0 stat -f '%Lp %N'; else xargs -0 stat -c '%a %n'; fi; }
live_state() { # every file the live copy holds, by name, mode and content: a hand edit there is an input too (ADR-0140)
  local h; if command -v sha256sum >/dev/null 2>&1; then h=sha256sum; else h="shasum -a 256"; fi
  { ( cd "$live" && find . -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | mode_of ) 2>/dev/null
    ( cd "$live" && find . -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 $h ) 2>/dev/null
  } | digest; }
# Notes that run every cycle, before any skip: each is named once, then silent until its input changes (#700). They
# read files only, never write the live copy, and never start or stop a process.
# - live models.json is owner config the publish never writes; the hub panel edits it. A change of its digest is named
#   once. The first digest a state dir sees is kept with no line.
# - a stray .bak-* copy in live scripts/ is clutter: the list is named once, and never removed.
# - the drivers run the sha watchd recorded at start ($SG_STATE/driver.sha); the pass compares that build with the
#   published tree for the files the long-lived loop holds in memory only (#2019). A per-call script starts a new process
#   each call, so the publish already reaches it. A changed in-memory file is a restart only the owner makes, named once
#   per published sha with the file; with none changed the pass records the new build as current and asks for no restart.
driver_paths=(scripts/watchd.sh scripts/watch.sh scripts/fastloop.sh scripts/cqflush.sh scripts/env.sh scripts/caps.sh
  scripts/ghcall.sh scripts/steprec.sh scripts/promotequiet.sh scripts/address.sh scripts/skiplabels.sh scripts/profiles)
driver_changed() { # driver_changed FROM TO: the in-memory files that differ between two builds, one a line; rc 3 = cannot tell
  local p
  git -C "${SG_CHECKOUT:-}" cat-file -e "$1^{commit}" 2>/dev/null && git -C "${SG_CHECKOUT:-}" cat-file -e "$2^{commit}" 2>/dev/null || return 3
  for p in "${driver_paths[@]}"; do git -C "$SG_CHECKOUT" diff --quiet "$1" "$2" -- "contrib/squad-goal/$p" 2>/dev/null || printf '%s\n' "$p"; done
  return 0; }
hub_paths=(scripts/hub.py scripts/hubmux.py scripts/board_columns.py scripts/board-definition.json scripts/board_history.py scripts/board_cfd.py scripts/board_flow_history.py)
hub_changed() { # hub_changed FROM TO: the files the hub imports or pins that differ between two builds, one a line; rc 3 = cannot tell
  local p
  git -C "${SG_CHECKOUT:-}" cat-file -e "$1^{commit}" 2>/dev/null && git -C "$SG_CHECKOUT" cat-file -e "$2^{commit}" 2>/dev/null || return 3
  for p in "${hub_paths[@]}"; do git -C "$SG_CHECKOUT" diff --quiet "$1" "$2" -- "contrib/squad-goal/$p" 2>/dev/null || printf '%s\n' "$p"; done
  return 0; }
once() { # once NAME VALUE LINE: print LINE when VALUE differs from the value kept under NAME; keep VALUE
  local f=$SG_STATE/live-guard.$1; [ "$(cat "$f" 2>/dev/null)" = "$2" ] && return 0
  mkdir -p "$SG_STATE" && printf '%s\n' "$2" > "$f.$$" && mv "$f.$$" "$f"; [ -n "$3" ] && echo "$3"; return 0; }
notes() {
  [ -n "${SG_STATE:-}" ] || return 0
  local m dg cl ds ps
  m=$live/scripts/models.json
  if [ -f "$m" ]; then dg=$(digest < "$m" | cut -c1-12)
    if [ -f "$SG_STATE/live-guard.models" ]; then once models "$dg" "LIVE-GUARD live models.json changed by hand: $dg"; else once models "$dg" ""; fi
  fi
  cl=$( cd "$live/scripts" 2>/dev/null && find . -maxdepth 1 -type f -name '*.bak-*' | sed 's|^\./|scripts/|' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')
  if [ -n "$cl" ]; then once clutter "$cl" "LIVE-GUARD live clutter, not shipped: $cl"; else rm -f "$SG_STATE/live-guard.clutter"; fi
  ds=$(cat "$SG_STATE/driver.sha" 2>/dev/null); ps=$(cut -d' ' -f1 "$live/PUBLISHED" 2>/dev/null)
  if [ -n "$ds" ] && [ -n "$ps" ] && [ "$ds" != "$ps" ]; then
    db=$(cat "$SG_STATE/live-guard.driver" 2>/dev/null); [ -n "$db" ] || db=$ds   # the build the drivers effectively run
    ch=$(driver_changed "$db" "$ps"); cr=$?
    if [ "$cr" = 3 ]; then once restart "$ps" "LIVE-GUARD drivers run ${ds:0:8}, published ${ps:0:8}: restart pending (owner)"
    elif [ -n "$ch" ]; then once restart "$ps" "LIVE-GUARD drivers run ${ds:0:8}, published ${ps:0:8}: restart pending (owner): $(printf '%s' "$ch" | tr '\n' ' ' | sed 's/ $//')"
    else printf '%s\n' "$ps" > "$SG_STATE/live-guard.driver.$$" && mv "$SG_STATE/live-guard.driver.$$" "$SG_STATE/live-guard.driver"; fi
  fi
  hs=$(cat "$SG_STATE/hub.sha" 2>/dev/null)
  if [ -n "$hs" ] && [ -n "$ps" ] && [ "$hs" != "$ps" ]; then
    hch=$(hub_changed "$hs" "$ps"); hcr=$?
    if [ "$hcr" = 3 ]; then once hub "$ps" "LIVE-GUARD hub runs ${hs:0:8}, published ${ps:0:8}: restart pending (owner): hubctl.sh --restart"
    elif [ -n "$hch" ]; then once hub "$ps" "LIVE-GUARD hub runs ${hs:0:8}, published ${ps:0:8}: restart pending (owner): hubctl.sh --restart for $(printf '%s' "$hch" | tr '\n' ' ' | sed 's/ $//')"
    fi
  fi
  return 0; }
key_now() { { git -C "$tree" rev-parse HEAD; git -C "$tree" status --porcelain --untracked-files=no
  git -C "$tree" rev-parse "origin/$base"; live_state; } 2>/dev/null | digest; }

notes

# The publish reads the main working tree, and nothing moved that tree to origin: it sat at an old commit while later PRs
# merged, and the live loop ran old scripts. The source is stg and only stg (ADR-0145): a work branch lands in dev, and the
# loop promotes a proven dev tip to stg by itself (promote-pass.sh, ADR-0145 rule 3, #576), so a defect in dev and not in
# stg never reaches the running loop. The tree is fast-forwarded first (git only, no GitHub call), ff-only, from origin/stg,
# and only when it is clean and on stg; the publish runs only from a tree at origin/stg. A tree on any other branch is not a
# source: it is named and nothing is published. A dirty tree (a file the publish copies; machine config is not dirt), a
# diverged tree and a failed fetch are named, nothing is published, and the tree is never touched. The base here is stg,
# never $SG_BASE: that one names where work branches start.
if git -C "$SG_CHECKOUT" rev-parse --git-dir >/dev/null 2>&1; then
  base=stg; tree=$SG_CHECKOUT; cur=$(git -C "$tree" branch --show-current)
  if [ "$cur" != "$base" ]; then echo "LIVE-GUARD main tree $tree not fast-forwarded: it is on ${cur:-a detached head}, not $base; nothing is published"; exit 0
  elif [ -n "$(git -C "$tree" status --porcelain --untracked-files=no -- contrib/squad-goal ':!contrib/squad-goal/scripts/models.json' ':!contrib/squad-goal/scripts/profiles')" ]; then echo "LIVE-GUARD main tree $tree not fast-forwarded: a shipped file is dirty; nothing is published"; exit 0
  elif ! GIT_TERMINAL_PROMPT=0 git -C "$tree" fetch --quiet origin "$base" >/dev/null 2>&1; then echo "LIVE-GUARD main tree $tree not fast-forwarded: git fetch origin $base failed; nothing is published"; exit 0
  else
    from=$(git -C "$tree" rev-parse --short HEAD)
    if git -C "$tree" merge --ff-only --quiet "origin/$base" >/dev/null 2>&1; then
      to=$(git -C "$tree" rev-parse --short HEAD)
      [ "$from" = "$to" ] || echo "LIVE-GUARD main tree $tree fast-forwarded $base $from..$to"
    else echo "LIVE-GUARD main tree $tree not fast-forwarded: $base has diverged from origin/$base (not a fast-forward); nothing is published"; exit 0; fi
  fi
fi

kf=$SG_STATE/live-guard.key; key=
if git -C "$SG_CHECKOUT" rev-parse --git-dir >/dev/null 2>&1; then
  key=$(key_now)
  [ -n "$key" ] && [ "$(cat "$kf" 2>/dev/null)" = "$key" ] && exit 0    # nothing the pass reads changed: no check, no publish, no write
fi

diff=$(bash "$here/publish-live.sh" --check --no-compare "$repo" "$live")
if [ -n "$diff" ]; then
  echo "LIVE-GUARD the live skill differs from the repository copy: $(printf '%s' "$diff" | tr '\n' ' ')"
  out=$(bash "$here/publish-live.sh" --no-compare "$repo" "$live" 2>&1); rc=$?
  [ -n "${SG_STATE:-}" ] && mkdir -p "$SG_STATE" && touch "$SG_STATE/live-publish.stamp"   # the janitor's rule M reads this change as the publish's own (#659)
  [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/LIVE-GUARD /'
  [ "$rc" = 0 ] || echo "LIVE-GUARD the publish exited $rc"
  [ "$rc" = 0 ] && [ -s "$live/PUBLISHED" ] && echo "LIVE-GUARD published $(cut -c1-8 "$live/PUBLISHED")"
  left=$(bash "$here/publish-live.sh" --check --no-compare "$repo" "$live")
  if [ -n "$left" ]; then
    echo "LIVE-GUARD the publish could not resolve: $(printf '%s' "$left" | tr '\n' ' ')"   # named every cycle: the key is not kept
    exit 0
  fi
  echo "LIVE-GUARD the live skill now matches the repository copy"
fi
notes                                                                 # a publish to another sha: the restart is named now
if [ -n "$key" ] && [ -n "${SG_STATE:-}" ]; then                      # keep the key the next cycle reads
  mkdir -p "$SG_STATE"; next=$(key_now); [ -n "$next" ] && { printf '%s\n' "$next" > "$kf.$$" && mv "$kf.$$" "$kf"; }
fi
exit 0
