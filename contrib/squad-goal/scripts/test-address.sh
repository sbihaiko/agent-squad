#!/bin/bash
# seconds: 3.28
# guards: every-script
# Table test for the address seam (address.sh): ONE function, sg_resolve_repo, turns each spelling of "which repository"
# into one `owner/name` -- an address `owner/repo#N`, a bare `N`, a profile name and a checkout -- and sg_same_repo calls
# two names one repository only when owner AND name match. A short name with no owner is resolved against the repositories
# the profile works, or refused by name. The same seam turns a profile, a checkout, an address or a bare number into one
# repository context (sg_repo_ctx, and `bash address.sh <input>`), refuses an unknown repository by name with the known
# list, and is what every script and the hub read: a stub gh records every call, and a script that reaches it before a
# repository is resolved fails here. Offline: throwaway profiles and checkouts, a stub gh. usage: bash test-address.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/profiles" "$T/co"
printf ': "${SG_REPO:=acme/emu}"\n' > "$T/profiles/emu.sh"
git -C "$T/co" init -q; git -C "$T/co" remote add origin https://github.com/acme/tool.git
mkdir -p "$T/co2"; git -C "$T/co2" init -q; git -C "$T/co2" remote add origin git@github.com:acme/emu.git
export SG_REPO=acme/emu SG_REPOS="acme/emu acme/tool other/emu2" SG_PROFILES_DIR=$T/profiles
. "$d/startability.sh"; . "$d/address.sh"   # address.sh last: the seam under test, whichever startability.sh is beside it
row() {  # name, input, expected repository (empty = refused, with the input named on stderr)
  local out err rc; out=$(sg_resolve_repo "$2" 2>"$T/err"); rc=$?; err=$(cat "$T/err")
  if [ -z "$3" ]; then
    { [ $rc -ne 0 ] && [ -z "$out" ] && case "$err" in *"$2"*) true;; *) false;; esac; } && ok "$1: refused by name" || ko "$1: '$2' not refused by name (rc $rc, out '$out', err '$err')"
  else
    [ $rc -eq 0 ] && [ "$out" = "$3" ] && ok "$1: $2 -> $3" || ko "$1: '$2' resolved to '$out' (rc $rc), expected $3"
  fi
}
row "address"         "acme/tool#12"  acme/tool
row "address, other"  "zed/else#3"    zed/else
row "bare number"     "12"            acme/emu
row "bare #number"    "#12"           acme/emu
row "profile name"    "emu"           acme/emu
row "checkout https"  "$T/co"         acme/tool
row "checkout ssh"    "$T/co2"        acme/emu
row "short, one match" "tool#7"       acme/tool
row "short, no match" "nowhere#7"     ""
SG_REPOS="acme/tool x/tool" row "short, two matches" "tool#7" ""
row "empty"           ""              ""
sg_same_repo acme/tool acme/tool && ok "same: owner and name match" || ko "same: acme/tool != acme/tool"
sg_same_repo acme/tool other/tool && ko "same: other/tool equals acme/tool on the name alone" || ok "same: another owner is another repository"
sg_same_repo acme/tool acme/emu && ko "same: acme/emu equals acme/tool" || ok "same: another name is another repository"
sg_same_repo tool acme/tool && ok "same: the short name resolves to its one match" || ko "same: short 'tool' did not resolve to acme/tool"
SG_REPOS="acme/tool x/tool" sg_same_repo tool acme/tool 2>/dev/null && ko "same: an ambiguous short name was taken as acme/tool" || ok "same: an ambiguous short name is refused"
sg_same_repo "" "" && ko "same: two empty names are one repository" || ok "same: an empty name is no repository"
# --- The context: every input shape crossed with the context it must produce. Two fixture profiles, run as the CLI.
src=$d/address.sh; P=$T/cprofiles; B=$T/state; mkdir -p "$P" "$T/co-alpha" "$T/co-beta" "$T/plain"
cat > "$P/alpha.sh" <<EOF
: "\${SG_REPO:=x/alpha}"; : "\${SG_OWNER:=x}"; : "\${SG_PROJECT:=4}"; : "\${SG_CHECKOUT:=$T/co-alpha}"; : "\${SG_BASE:=main}"
: "\${SG_FORK_DIR:=$T/fork-alpha}"
EOF
cat > "$P/Beta.sh" <<EOF
: "\${SG_REPO:=Y/Beta}"; : "\${SG_OWNER:=Y}"; : "\${SG_PROJECT:=9}"; : "\${SG_CHECKOUT:=$T/co-beta}"; : "\${SG_BASE:=develop}"
EOF
printf ': "${SG_REPO:=owner/repo}"\n' > "$P/example.sh"
printf ': "${SG_REPO:=x/legacy}"; SG_TOOLING_REPO=x/tool\n' > "$P/legacy.sh"   # names the retired second repository
git init -q "$T/clone" && git -C "$T/clone" remote add origin git@github.com:Y/Beta.git
cli() { (cd "${CWD:-$T/plain}" && env -u SG_REPO -u SG_REPOS -u SG_CHECKOUT -u SG_STATE SG_PROFILES_DIR="$P" SG_STATE_BASE="$B" \
  SG_PROFILE="${PROF:-}" bash "$src" "$@" 2>&1); }
# ctx <field=value ...> -- <input> [profile in force]: every named field of the printed context must match
ctx() { local want=() a out rc; while [ "$1" != -- ]; do want+=("$1"); shift; done; shift
  out=$(PROF=${2:-} cli "$1"); rc=$?
  [ $rc = 0 ] || { ko "context: '$1' (profile '${2:-}') was refused: $out"; return; }
  for a in "${want[@]}"; do printf '%s\n' "$out" | /usr/bin/grep -qxF "$(printf '%s=%q' "${a%%=*}" "${a#*=}")" \
    || { ko "context: '$1' (profile '${2:-}'): want ${a}, got: $(echo $out)"; return; }; done
  ok "context: '$1' (profile '${2:-}') -> ${want[*]}"; }
refused() { local out rc; out=$(PROF=${3:-} cli "$1"); rc=$?
  [ $rc = 2 ] && printf '%s' "$out" | /usr/bin/grep -qF -- "$2" && ok "refusal: '$1' is refused, naming $2" || ko "refusal: '$1' was not refused by name (rc $rc): $out"; }
all="SG_PROFILE=alpha SG_REPO=x/alpha SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT=$T/co-alpha SG_BASE=main SG_FORK_DIR=$T/fork-alpha SG_STATE=$B/alpha SG_HUB_PATH=/alpha"
ctx $all SG_ISSUE= -- alpha                                        # a profile name
ctx $all SG_ISSUE=12 -- 'x/alpha#12'                               # an issue address
ctx $all SG_ISSUE= -- 'x/alpha'                                    # an address with no issue
ctx SG_PROFILE=alpha SG_ISSUE=12 -- 'X/Alpha#12'                   # an address, owner and name in another case
ctx $all SG_ISSUE= -- "$T/co-alpha"                                # a checkout a profile names
ctx SG_PROFILE=Beta SG_REPO=Y/Beta SG_OWNER=Y SG_PROJECT=9 SG_CHECKOUT=$T/co-beta SG_BASE=develop SG_FORK_DIR= \
    SG_STATE=$B/Beta SG_HUB_PATH=/Beta SG_ISSUE= -- "$T/clone"     # a checkout whose origin a profile matches
ctx $all SG_ISSUE=12 -- 12 alpha                                   # a bare number: the profile in force
ctx SG_PROFILE=Beta SG_REPO=Y/Beta SG_ISSUE=12 -- '#12' Beta       # a bare #number: the profile in force
# a bare number is the profile in force and nothing else: never the profile the cwd's origin happens to match
out=$(CWD=$T/clone cli 7); rc=$?
[ $rc = 2 ] && printf '%s' "$out" | /usr/bin/grep -qF 'no profile in force' \
  && ok "bare number: with no profile in force it is refused, not read from the cwd" || ko "bare number with no profile in force, in a matched checkout (rc $rc): $out"
out=$(CWD=$T/clone PROF=alpha cli 7 | sed -n 's/^SG_REPO=//p')
[ "$out" = x/alpha ] && ok "bare number: the profile in force wins over the cwd's checkout" || ko "bare number in Beta's checkout with alpha in force: '$out'"
# --- The refusal: an unknown repository is refused by name, with the list of known ones; nothing falls back.
for u in 'z/gone#3' z/gone; do out=$(PROF=alpha cli "$u"); rc=$?
  { [ $rc = 2 ] && printf '%s' "$out" | /usr/bin/grep -qF z/gone && printf '%s' "$out" | /usr/bin/grep -qF 'known: Y/Beta x/alpha' \
    && ! printf '%s' "$out" | /usr/bin/grep -q '^SG_'; } \
    && ok "refusal: '$u' is refused by name, listing the known repositories" || ko "refusal: '$u' (rc $rc): $out"; done
refused nope nope; refused example example; refused 12 'no profile in force'; refused 12 gone gone; refused "$T/plain" "$T/plain"
out=$(cli --list); [ "$out" = "$(printf 'Beta\tY/Beta\nalpha\tx/alpha\nlegacy\tx/legacy')" ] \
  && ok "list: --list names every known profile and its repository, the example excluded" || ko "--list: $out"
out=$(cd "$T/plain" && SG_PROFILES_DIR=$P SG_STATE_BASE=$B bash -c '. "$1"; sg_repo_ctx "y/beta#5" && echo "$SG_REPO $SG_ISSUE $SG_STATE"' _ "$src")
[ "$out" = "Y/Beta 5 $B/Beta" ] && ok "sourced: sg_repo_ctx sets the context in the caller" || ko "sourced form: '$out'"
# the identity resolver and the context read ONE profile reader: a profile name resolves to the repository it names
out=$(SG_PROFILES_DIR=$P bash -c '. "$1"; sg_resolve_repo Beta' _ "$src"); [ "$out" = Y/Beta ] && ok "one reader: sg_resolve_repo Beta -> Y/Beta" || ko "sg_resolve_repo Beta: '$out'"
# --- The guard: nothing reaches GitHub before a repository is resolved. A stub gh records every call.
mkdir -p "$T/bin"; printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s"\nexit 0\n' "$T/ghcalls" > "$T/bin/gh"; chmod +x "$T/bin/gh"
for c in claude codex curl; do printf '#!/bin/bash\nexit 0\n' > "$T/bin/$c"; chmod +x "$T/bin/$c"; done
guard() { (cd "$T/plain" && env -u SG_REPO -u SG_REPOS -u SG_CHECKOUT -u SG_STATE PATH="$T/bin:$PATH" HOME="$T/home" \
  SG_PROFILES_DIR="$P" SG_STATE_BASE="$B" SG_SQUAD=/dev/null perl -e 'alarm 20; exec @ARGV' "$@" </dev/null 2>&1); }
: > "$T/ghcalls"; out=$(SG_PROFILE=nope guard bash -c '. "$1/env.sh"; gh api "repos/$SG_REPO/issues"; echo reached' _ "$d"); rc=$?
envok=0; { [ $rc != 0 ] && [ ! -s "$T/ghcalls" ] && printf '%s' "$out" | /usr/bin/grep -qF nope; } && envok=1 \
  && ok "guard: env.sh stops on an unknown profile, naming it, before gh" || ko "guard: env.sh let an unknown profile through to gh (rc $rc, gh: $(cat "$T/ghcalls")): $out"
: > "$T/ghcalls"; out=$(SG_PROFILE= guard bash -c '. "$1/env.sh"; gh api "repos/$SG_REPO/issues"; echo reached' _ "$d"); rc=$?
[ $rc != 0 ] && [ ! -s "$T/ghcalls" ] && ok "guard: env.sh stops with no profile in force and no profile's checkout, before gh" || ko "guard: no profile reached gh (rc $rc): $out"
# every script that calls gh resolves its repository first: its first command sources env.sh, and with an unknown
# profile it stops there with no call recorded. Run only once env.sh is shown to stop, so no script runs unresolved.
for s in "$d"/*.sh; do b=$(basename "$s")
  # gh wrappers take the repository as an argument; startability.sh is a library; cqflush.sh and mkreq.sh resolve per
  # address and run below. check-tdd-red.sh runs from prepush's already-resolved env and reaches gh only when SG_REPO is
  # non-empty, so it has no env.sh to source and cannot start with one.
  case "$b" in test-*|env.sh|address.sh|ghcall.sh|check-gh-interface.sh|startability.sh|cqflush.sh|mkreq.sh|check-tdd-red.sh) continue;; esac
  /usr/bin/grep -qE '(^|[^a-z_-])gh (api|issue|pr|repo|project|run|label)' "$s" || continue
  first=$(/usr/bin/grep -nvE '^[[:space:]]*(#|$)' "$s" | head -1); e=$(/usr/bin/grep -n 'env\.sh"' "$s" | head -1 | cut -d: -f1)
  [ "${first%%:*}" = "$e" ] || { ko "guard: $b calls gh but does not resolve its repository first (first command: ${first#*:})"; continue; }
  [ $envok = 1 ] || continue
  : > "$T/ghcalls"; out=$(SG_PROFILE=nope guard bash "$s"); rc=$?
  [ $rc != 0 ] && [ ! -s "$T/ghcalls" ] && ok "guard: $b stops before gh on an unknown profile" || ko "guard: $b reached gh unresolved (rc $rc, gh: $(head -3 "$T/ghcalls")): $(echo $out | cut -c1-200)"
done
# the request builder reads the open pull requests of a resolved repository only: with an unknown profile it reads none
printf 'IMPORTANT: your first and only action\n' > "$T/body"; : > "$T/ghcalls"
out=$(SG_PROFILE=nope guard bash "$d/mkreq.sh" g24 "$T/body"); [ ! -s "$T/ghcalls" ] \
  && ok "guard: mkreq.sh reaches no gh on an unknown profile" || ko "guard: mkreq.sh reached gh unresolved (gh: $(head -3 "$T/ghcalls"))"
# the retired two-repository key no longer stops env.sh: it warns, naming it, and the profile goes on (test-retired-settings.sh)
out=$(SG_PROFILE=legacy guard bash -c '. "$1/env.sh"; echo reached' _ "$d"); rc=$?
[ $rc = 0 ] && printf '%s' "$out" | /usr/bin/grep -q SG_TOOLING_REPO && printf '%s' "$out" | /usr/bin/grep -q reached \
  && ok "guard: env.sh warns on a profile that sets SG_TOOLING_REPO, naming it, and goes on" || ko "env.sh with SG_TOOLING_REPO set (rc $rc): $out"
# the queue flusher resolves each comment's repository from the state directory it sits in: one no profile owns is set
# aside unposted, never posted to a repository it was not filed for
mkdir -p "$B/nope/cq"; echo hi > "$B/nope/cq/12-x.md"; touch -t 202001010000 "$B/nope/cq/12-x.md"; : > "$T/ghcalls"
out=$(guard bash "$d/cqflush.sh" --once); [ ! -s "$T/ghcalls" ] && [ -e "$B/nope/cq/12-x.md.dead" ] \
  && ok "guard: cqflush.sh sets aside a queue no profile owns, before gh" || ko "guard: cqflush.sh with an unowned queue (gh: $(cat "$T/ghcalls")): $out"
# --- The readers: the loop, the hub and the flusher read this seam, and no second copy of the rule exists.
[ ! -e "$d/repoctx.sh" ] && ok "readers: no second resolver beside the seam" || ko "readers: repoctx.sh still holds a second copy of the rule"
for r in env.sh hubctl.sh cqflush.sh hubmux.py; do
  /usr/bin/grep -q 'address\.sh' "$d/$r" && ! /usr/bin/grep -q 'repoctx\.sh' "$d/$r" && ok "readers: $r reads the seam" || ko "readers: $r does not read address.sh alone"; done
# The hub is one program: hub.py compiles its hub_theme_*.py modules into its own namespace (#2742), so the hub's
# readers are the entry point and every theme module.
/usr/bin/grep -q 'address\.sh' "$d"/hub.py "$d"/hub_theme_*.py && ! /usr/bin/grep -q 'repoctx\.sh' "$d"/hub.py "$d"/hub_theme_*.py \
  && ok "readers: hub.py reads the seam" || ko "readers: hub.py does not read address.sh alone"
! /usr/bin/grep -q 'getcwd' "$d"/hub.py "$d"/hub_theme_*.py && ok "readers: hub.py never takes its checkout from its own working directory" || ko "readers: hub.py falls back to os.getcwd()"
# --- The scripts run on the loop's macOS and on the CI's Linux: a file's mtime is read portably, BSD stat with a GNU fallback.
bad=""; for s in "$d"/*.sh; do
  /usr/bin/grep -q 'stat -f' "$s" && ! /usr/bin/grep -q 'stat -c' "$s" && bad="$bad $(basename "$s")"; done
[ -z "$bad" ] && ok "portable: every script that reads mtime names the GNU stat form beside the BSD one" \
  || ko "portable: these scripts read mtime with BSD stat alone, which GNU stat does not answer:$bad"
# --- (#757) a profile's := default wins over env.sh's: env.sh assigns its defaults after the profile.
printf ': "${SG_REPO:=x/gates}"; : "${SG_WAVE_GATES:=make clean all}"; : "${SG_FRONTIER_SKIP_LABELS:=spec}"; : "${SG_RUN_REVIEW:=/bin/true}"\n' > "$P/gates.sh"
out=$(SG_PROFILE=gates guard bash -c '. "$1/env.sh"; echo "[$SG_WAVE_GATES|$SG_FRONTIER_SKIP_LABELS|$SG_RUN_REVIEW]"' _ "$d" | tail -1)
[ "$out" = "[make clean all|spec|/bin/true]" ] && ok "defaults: a profile's := default wins over env.sh's" || ko "defaults: profile := lost to env.sh: $out"
out=$(SG_PROFILE=alpha guard bash -c '. "$1/env.sh"; echo "[$SG_WAVE_GATES]"' _ "$d" | tail -1)
case "$out" in *fast-gate.sh*) ok "defaults: with no profile value env.sh's gate holds";; *) ko "defaults: no default gate: $out";; esac
# --- (#761) a profile that does not parse is refused, naming the file and line, and --list reports it.
P2=$T/profiles2; mkdir -p "$P2"; printf ': "${SG_REPO:=x/ok}"\n' > "$P2/ok.sh"
printf ': "${SG_REPO:=x/broken}"\n: "${SG_REVIEW_RULES:=the repo'"'"'s rules}"\n' > "$P2/broken.sh"
out=$(cd "$T/plain" && SG_PROFILES_DIR=$P2 SG_STATE_BASE=$B bash "$src" broken 2>&1); rc=$?
[ $rc = 2 ] && printf '%s' "$out" | /usr/bin/grep -qF "$P2/broken.sh" && printf '%s' "$out" | /usr/bin/grep -q 'line [0-9]' \
  && ok "parse: a profile that does not parse is refused, naming the file and line" || ko "parse: broken profile (rc $rc): $out"
out=$(cd "$T/plain" && SG_PROFILES_DIR=$P2 SG_STATE_BASE=$B bash "$src" --list 2>&1)
printf '%s' "$out" | /usr/bin/grep -q '^broken	.*does not parse' && printf '%s' "$out" | /usr/bin/grep -qx 'ok	x/ok' \
  && ok "parse: --list reports a profile that does not parse" || ko "parse: --list: $out"
if command -v zsh >/dev/null; then out=$(cd "$T/plain" && SG_PROFILE=alpha SG_PROFILES_DIR=$P SG_STATE_BASE=$B zsh -c '. "$1/env.sh"; echo "rc=$?"' _ "$d" 2>&1)
  printf '%s' "$out" | /usr/bin/grep -q 'source it from bash' && ! printf '%s' "$out" | /usr/bin/grep -q 'address.sh' \
    && ok "zsh: env.sh sourced from zsh says it needs bash" || ko "zsh: env.sh from zsh: $out"; fi
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
