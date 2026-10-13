#!/bin/bash
# usage: autorefuse.sh --refused <operation> <address> <error> [--exit <code>] [--context <run-or-card>]
#        autorefuse.sh --fingerprint <operation> <address> <error>   # the cause's key alone; nothing is filed, nothing is read
#
# One bug per CAUSE, never one per occurrence (#139). A write the loop could not prove was refused -- `gh_send` exits 2
# with GitHub's own words on its line (ghcall.sh, ADR-0277) -- and the pass that hit it asks this filer to file it. The
# fingerprint is the cause: the operation, the address and the error's own FIRST line. The run id, the timestamp and
# the cycle number are deliberately outside it, because they name the occurrence and an occurrence is not a defect.
#
# The index is `$SG_STATE/auto/refusal-<fingerprint>`, one line, `<issue> <state>`: a fingerprint whose issue is OPEN
# files nothing, and one whose issue is CLOSED files again, because a defect that came back is a defect. The state is
# read live, through the door, so an issue a person closed since the loop filed it is seen as closed; a state the loop
# cannot read files nothing, because a refusal is not an answer (ADR-0277). The index is a file in the loop's own state
# directory, so a restart reads it and never re-files the defects it already filed.
#
# The issue is opened by the loop's one filing primitive (`autohunt.sh --file`): the `bug` label, the startable label
# beside it, the `[SQUAD]` prefix and the `Filed by <pass>` line are the primitive's, never a second way to open an
# issue. This filer has no call site of its own to GitHub either -- the state of a filed issue goes through ghcall.sh.
#
# The caller -- the pass that hit the refusal, with the operation, the address and the guard that should have caught it
# -- is #145's half and is not here: this is the filer, on the seam that exists.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"; here="$(cd "$(dirname "$0")" && pwd)"
A="$SG_STATE/auto"; mkdir -p "$A"
export SG_GH_PASS=autorefuse   # the call log names the pass that spends the state read (ADR-0277)

# digest: the sha256 of stdin, whichever tool this machine has (GNU sha256sum, BSD shasum).
digest() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; }
# first_line <text>: the error's own first line, without its carriage return or trailing blanks.
first_line() { printf '%s\n' "$1" | head -n 1 | tr -d '\r' | sed 's/[[:space:]]*$//'; }
# refusal_fingerprint <operation> <address> <error>: the cause's key. Three lines, one per field, so no field's text can
# read as another's; the error enters as its first line alone. Nothing else is an input, so no occurrence can drift in.
refusal_fingerprint() { printf '%s\n%s\n%s\n' "$1" "$2" "$(first_line "$3")" | digest | cut -d' ' -f1 | cut -c1-16; }
# refusal_title <operation> <address> <error>: the issue's title, before the primitive prefixes it with [SQUAD].
refusal_title() { local line; line=$(first_line "$3"); printf 'refused write %s %s: %s' "$1" "$2" "${line:0:80}"; }
# refusal_body <operation> <address> <error> <exit> <context> <key>: the evidence, in one fence. GitHub's words are
# quoted here and never parsed: the cause they name is already the fingerprint.
refusal_body() {
  printf 'A write the loop could not prove was refused. The cause below is filed once: a repeat of it files nothing\n'
  printf 'while this issue is open, and files again once it is closed, because a defect that came back is a defect.\n\n'
  printf '```\n'
  printf 'operation:   %s\n' "$1"
  printf 'address:     %s\n' "$2"
  printf 'exit:        %s\n' "$4"
  printf 'concerns:    %s\n' "$5"
  printf 'fingerprint: %s\n' "$6"
  printf 'error:\n'
  printf '%s\n' "$3" | sed 's/^/  /'
  printf '```\n\n'
  printf 'The error text above is GitHub'"'"'s own words, quoted verbatim and never parsed. The run id, the timestamp and\n'
  printf 'the cycle number are outside the fingerprint on purpose: they name the occurrence, not the cause.\n'
}
# issue_state <n>: GitHub's own word for the issue's state, or nothing when the read did not answer. A refusal is kept
# in $sg_refusal_why so the caller names it, and a caller that only ever learns from an answer files nothing.
sg_refusal_why=""
issue_state() {
  local o rc; o=$(gh_api "repos/$SG_REPO/issues/$1" -q '.state' 2>&1); rc=$?
  [ "$rc" -eq 0 ] && { printf '%s' "$o"; return 0; }
  sg_refusal_why=$(first_line "$o"); return 1
}
# file_refusal <operation> <address> <error> <exit> <context>: file the cause unless it already holds an open issue.
file_refusal() {
  local op=$1 addr=$2 err=$3 code=${4:--} ctx=${5:--}
  local fp key idx n st out num body title
  fp=$(refusal_fingerprint "$op" "$addr" "$err"); key="refusal-$fp"; idx="$A/$key"
  if [ -f "$idx" ]; then
    read -r n st < "$idx"
    if ! st=$(issue_state "$n"); then
      echo "REFUSE unreadable #$n $key $op $addr: GitHub did not answer its state, so nothing is filed ($sg_refusal_why)"
      return 0
    fi
    [ "$st" = closed ] || { echo "REFUSE open #$n $key $op $addr: the issue is open, so nothing is filed"; return 0; }
    echo "REFUSE refile #$n $key $op $addr: the issue is closed, so the defect that came back is filed again"
  fi
  body=$(mktemp); title=$(refusal_title "$op" "$addr" "$err")
  refusal_body "$op" "$addr" "$err" "$code" "$ctx" "$key" > "$body"
  # The issue is the primitive's: it carries the bug kind, the startable label (this pass's own name, so the dispatch
  # picks the defect up like any other work), the [SQUAD] prefix and the `Filed by autorefuse` line.
  out=$(SG_FILED_BY=autorefuse SG_FILED_LABELS="${SG_FRONTIER_LABEL:-ready-for-agent}" \
        bash "$here/autohunt.sh" --file "$title" "$body" 2>&1); rm -f "$body"
  num=$(printf '%s' "$out" | sed -n 's/.*HUNT filed #\([0-9][0-9]*\).*/\1/p' | tail -1)
  if [ -z "$num" ]; then
    echo "REFUSE not-filed #- $key $op $addr: the filer opened no issue ($out)"
    return 1
  fi
  printf '%s open\n' "$num" > "$idx.tmp" && mv "$idx.tmp" "$idx"   # one file per cause: the index survives a restart
  echo "REFUSE filed #$num $key $op $addr: $(first_line "$err")"
}
mode=${1:-}; [ $# -gt 0 ] && shift
case $mode in
  --refused)
    [ $# -ge 3 ] || { echo "usage: autorefuse.sh --refused <operation> <address> <error> [--exit <code>] [--context <run-or-card>]" >&2; exit 2; }
    op=$1; addr=$2; err=$3; shift 3; code=-; ctx=-
    while [ $# -gt 0 ]; do
      case $1 in
        --exit) code=${2:?--exit needs a code}; shift 2;;
        --context) ctx=${2:?--context needs the run or card}; shift 2;;
        *) echo "autorefuse: unknown argument '$1'" >&2; exit 2;;
      esac
    done
    file_refusal "$op" "$addr" "$err" "$code" "$ctx"; exit $?;;
  --fingerprint)
    [ $# -eq 3 ] || { echo "usage: autorefuse.sh --fingerprint <operation> <address> <error>" >&2; exit 2; }
    refusal_fingerprint "$1" "$2" "$3"; echo; exit 0;;
  *)
    echo "usage: autorefuse.sh --refused <operation> <address> <error> [--exit <code>] [--context <run-or-card>]" >&2
    exit 2;;
esac
