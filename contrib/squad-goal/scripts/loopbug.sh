#!/bin/bash
# loopbug.sh: the loop-defect rules review.sh and autobug.sh share (#1358, ADR-0171). Sourced, never run.
# A loop defect is an issue labelled loop-bug, or any label that begins with loop-bug. It is reviewed by the loop-defect
# chain (Fable or Astra first), and autobug.sh builds it only after a plan review of its current body approved it.
# The plan review is a review record of the directory and key convention of a branch review: review-<key>.md holds the
# verdict, review-<key>.sha holds the reviewed object. For a plan that object is the body hash, not a commit. The key is
# the issue's owner/repo#n with / written as _, the rule a branch key follows.

# lb_is_loop_bug <labels>: 0 when a comma-separated label list carries loop-bug or any label beginning with loop-bug.
lb_is_loop_bug() { case ",$1," in *,loop-bug*) return 0;; esac; return 1; }

# lb_chain <labels>: the chain name review.sh reads for an issue with these labels.
lb_chain() { if lb_is_loop_bug "$1"; then echo loop_defect; else echo review; fi; }

# lb_body_hash <body>: the sha256 of an issue body. A plan review records it and autobug.sh compares it, so both read one value.
lb_body_hash() { printf '%s' "$1" | python3 -I -c 'import hashlib, sys; sys.stdout.write(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'; }

# lb_plan_key <repo> <issue>: the record key of an issue, owner/repo#n with / written as _.
lb_plan_key() { printf '%s#%s' "$1" "$2" | tr / _; }

# lb_plan_verdict <state dir> <repo> <issue> <current body hash>: prints one word and returns 0 only for an approval of the
# current body: approve, changes, stale or missing. A record whose body hash is not the current one is stale, whatever it says.
lb_plan_verdict() {
  local k f s rec
  k=$(lb_plan_key "$2" "$3"); f="$1/review-$k.md"; s="$1/review-$k.sha"
  [ -s "$f" ] || { echo missing; return 1; }
  rec=$(cat "$s" 2>/dev/null)
  [ "$rec" = "$4" ] || { echo stale; return 1; }
  case "$(head -1 "$f")" in
    "VERDICT: APPROVE"*) echo approve; return 0;;
  esac
  echo changes; return 1
}
