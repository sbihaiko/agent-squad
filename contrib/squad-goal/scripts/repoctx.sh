#!/bin/bash
# usage: repoctx.sh <input>   prints the repository context as KEY=value lines (eval-safe), exit 2 = refused
#        repoctx.sh --list    prints "<profile>\t<owner/repo>" for every known profile
#        . repoctx.sh; sg_repo_ctx <input>   sets the same keys in the caller's shell
# The ONE resolution of a repository (#20). <input> is a profile slug, a checkout (a path), an address owner/repo#n
# (owner/repo alone too) or a bare n / #n. A bare n resolves to the profile in force: SG_PROFILE, else the profile matched
# from the origin remote of the cwd. A repository no profile names is refused by name; nothing falls back to another one.
# The context: SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_FORK_DIR SG_STATE SG_HUB_PATH SG_ISSUE.
# A profile that sets the retired SG_TOOLING_REPO is refused by name: every repository has its own profile.
# A profile is read on its own (no inherited SG_* value reaches it), so one profile can never borrow another's settings.
# SG_PROFILES_DIR (default scripts/profiles) and SG_STATE_BASE (default ~/.cache/squad-goal) are the two inputs it reads.
_sg_ctx_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SG_CTX_KEYS="SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_FORK_DIR SG_STATE SG_HUB_PATH SG_ISSUE"
sg_profiles_dir() { echo "${SG_PROFILES_DIR:-$_sg_ctx_here/profiles}"; }
# Every known profile slug, one per line, in byte order; example.sh is a template, never a repository.
sg_profiles() { local f n; for f in "$(sg_profiles_dir)"/*.sh; do [ -f "$f" ] || continue; n=$(basename "$f" .sh)
  [ "$n" = example ] || echo "$n"; done | LC_ALL=C sort; }
# The profile an origin remote names: the profile whose name the remote URL contains, compared lower-case.
sg_profile_from_origin() { local url n hit=""; url=$(git -C "${1:-.}" remote get-url origin 2>/dev/null | tr A-Z a-z) || return 1
  [ -n "$url" ] || return 1
  for n in $(sg_profiles); do case "$url" in *"$(echo "$n" | tr A-Z a-z)"*) hit=$n;; esac; done
  [ -n "$hit" ] && echo "$hit"; }
# The context of one profile, KEY=value lines; the profile's own values only.
_sg_ctx_of() { local slug=$1 issue=$2 f; f="$(sg_profiles_dir)/$slug.sh"
  [ -f "$f" ] && [ "$slug" != example ] || { echo "repoctx: unknown profile: $slug (known: $(sg_profiles | tr '\n' ' '))" >&2; return 2; }
  env $(compgen -e | /usr/bin/grep '^SG_' | /usr/bin/grep -vx 'SG_PROFILES_DIR\|SG_STATE_BASE' | sed 's/^/-u /') \
    bash -c 'here=$1; . "$2" >/dev/null 2>&1
      [ -z "${SG_TOOLING_REPO:-}" ] || { echo "repoctx: profile $3 sets SG_TOOLING_REPO, which is retired: give $SG_TOOLING_REPO its own profile" >&2; exit 2; }
      printf "%s=%q\n" SG_PROFILE "$3" SG_REPO "$SG_REPO" SG_OWNER "${SG_OWNER:-${SG_REPO%%/*}}" SG_PROJECT "$SG_PROJECT" \
        SG_CHECKOUT "$SG_CHECKOUT" SG_BASE "${SG_BASE:-main}" SG_FORK_DIR "$SG_FORK_DIR" \
        SG_STATE "${SG_STATE_BASE:-$HOME/.cache/squad-goal}/$3" SG_HUB_PATH "/$3" SG_ISSUE "$4"' _ "$_sg_ctx_here" "$f" "$slug" "$issue"; }
_sg_repo_of() { _sg_ctx_of "$1" "" 2>/dev/null | sed -n 's/^SG_REPO=//p'; }
_sg_real() { (cd "$1" 2>/dev/null && pwd -P); }
# repoctx <input>: print the context, or refuse (exit 2) naming what it could not resolve.
repoctx() { local in=$1 slug="" issue="" n r want co
  case "$in" in
    ""|-*) echo "repoctx: no input (a profile, a checkout, owner/repo#n or n)" >&2; return 2;;
    /*|.*|~*) co=$(_sg_real "${in/#\~/$HOME}") || { echo "repoctx: no such checkout: $in" >&2; return 2; }
      for n in $(sg_profiles); do r=$(_sg_ctx_of "$n" "" | sed -n 's/^SG_CHECKOUT=//p'); r=$(eval "echo $r")
        [ -n "$r" ] && [ "$(_sg_real "$r")" = "$co" ] && { slug=$n; break; }; done
      [ -n "$slug" ] || slug=$(sg_profile_from_origin "$co") || { echo "repoctx: no profile knows the checkout $in" >&2; return 2; };;
    [0-9]*|"#"[0-9]*) issue=${in#\#}; case "$issue" in *[!0-9]*) echo "repoctx: not an issue number: $in" >&2; return 2;; esac
      slug=${SG_PROFILE:-$(sg_profile_from_origin .)} || slug=""
      [ -n "$slug" ] || { echo "repoctx: no profile in force for #$issue (set SG_PROFILE or run from a profile's checkout)" >&2; return 2; };;
    */*) want=${in%%#*}; [ "$want" != "$in" ] && issue=${in#*#}
      case "$want" in */*/*|/*|*/) echo "repoctx: not an address: $in" >&2; return 2;; esac
      case "$issue" in *[!0-9]*) echo "repoctx: not an issue number in $in" >&2; return 2;; esac
      want=$(echo "$want" | tr A-Z a-z)
      for n in $(sg_profiles); do [ "$(_sg_repo_of "$n" | tr A-Z a-z)" = "$want" ] && { slug=$n; break; }; done
      [ -n "$slug" ] || { echo "repoctx: unknown repository: ${in%%#*} (known: $(for n in $(sg_profiles); do _sg_repo_of "$n"; done | tr '\n' ' '))" >&2; return 2; };;
    *) slug=$in;;
  esac
  _sg_ctx_of "$slug" "$issue"; }
# sg_repo_ctx <input>: the sourced form; sets every context key in the caller's shell, or returns 2 and sets none.
sg_repo_ctx() { local out; out=$(repoctx "$1") || return 2; eval "$out"; }
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if [ "${1:-}" = --list ]; then for n in $(sg_profiles); do printf '%s\t%s\n' "$n" "$(_sg_repo_of "$n")"; done; exit 0; fi
  repoctx "${1:-}" || exit 2
fi
