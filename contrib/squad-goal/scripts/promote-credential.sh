#!/bin/bash
# git credential helper for the promotion push (#2126): on `get`, it answers with the gh token, read under this process's own
# environment. promote-main.sh and promote-main-pass.sh name it ahead of the fallback helper, and never inherit the owner's.
# The loop's process runs under launchd, whose PATH is /usr/bin:/bin:/usr/sbin:/sbin and holds no gh (#2137). The helper seeds
# the places gh installs to, so the default token command resolves there, and it never needs the caller's PATH.
PATH="${PATH:+$PATH:}$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"; export PATH
cat > /dev/null
[ "${1:-}" = get ] || exit 0
tok=$(${SG_PROMOTE_TOKEN_COMMAND:-gh auth token} 2>/dev/null) || exit 0
[ -n "$tok" ] || exit 0
printf 'username=x-access-token\npassword=%s\n' "$tok"
