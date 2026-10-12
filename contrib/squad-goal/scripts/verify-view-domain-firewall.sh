#!/usr/bin/env bash
# usage: verify-view-domain-firewall.sh [root]        root defaults to the current directory
#
# ADR-0137 sections 3 and 4, half two (#1849): a view is markup and wiring - it constructs, binds and delegates to its view
# model. It does not read a file, open a client, mutate application state or instantiate a domain service. The other half of
# that guard is the host-free compile of the rule layer (ADR-0123, the host's own firewall script); this is the half that
# refuses the view itself.
#
# The rule is the direction; the paths and the domain names belong to the host, handed over through the environment:
#   SG_VIEW_GLOBS    whitespace-separated globs selecting the host's views under root      (default: *.axaml.cs)
#   SG_VIEW_SKIP     ERE of the directory prefixes no view lives under - build output, a
#                    checkout of the same tree, vendored code      (default: obj, bin, node_modules, .git)
#   SG_VIEW_DOMAIN   newline-separated EREs: the host's services and namespaces a view
#                    may not name                                                          (kind: service)
#   SG_VIEW_STATE    newline-separated EREs: the host's application state a view may not
#                    touch                                                                 (kind: state)
# Nothing here names a host's path or type: the two reaches that are not a host's to define - reading a file and opening a
# client - are generic and always on. A host that declares no domain gets exactly those two, and no guess about the rest.
#
# Run it as a gate, not as advice: wire it beside the host's own rule-layer firewall in make/CI, and put the same command in
# the host profile's $SG_PREPUSH_CHECKS so the loop runs it in the tree before every push. Exit 0 when clean, 1 on any offence.
set -uo pipefail
root=${1:-.}
if [ ! -d "$root" ]; then
	echo "ERROR: no such root: $root" >&2
	exit 1
fi

# The two reaches no host has to declare: the usual file and client entry points, their construction, and a fully qualified
# System.IO / System.Net type actually used. What is refused is the reach, not the mention - a `using System.IO;` or a bare
# Path.Combine is not reading a file, and a rule that flags them would only teach a host to carve out exceptions.
IO_TYPES='File|Directory|FileStream|StreamReader|StreamWriter|FileInfo|DirectoryInfo'
NET_TYPES='HttpClient|WebClient|HttpWebRequest|TcpClient|UdpClient|Socket'
FILE_RE="(^|[^A-Za-z0-9_.])($IO_TYPES)[[:space:]]*\\.|new[[:space:]]+($IO_TYPES)\\b|System\\.IO\\.[A-Z][A-Za-z]*\\."
CLIENT_RE="(^|[^A-Za-z0-9_.])($NET_TYPES)\\b|System\\.Net\\.[A-Z][A-Za-z]*\\."

# view_files: every view under root, one per line, oldest bash included (no case inside a command substitution).
view_files() {
	local g
	while IFS= read -r g; do
		[ -n "$g" ] || continue
		if [[ "$g" == */* ]]; then find "$root" -path "$g" -type f; else find "$root" -name "$g" -type f; fi
	done <<<"${SG_VIEW_GLOBS:-*.axaml.cs}"
}
files=$(view_files | /usr/bin/grep -vE "${SG_VIEW_SKIP:-/(obj|bin|node_modules|\.git)/}" | LC_ALL=C sort -u)
count=$(printf '%s' "$files" | /usr/bin/grep -c . || true)

# hits <file> <kind> <ere> : print every line of the file reaching through that ere; non-zero when there was one
hits() {
	sed -E 's|//.*$||' "$1" | /usr/bin/grep -nE "$3" | sed -E "s|^([0-9]+):[[:space:]]*|ERROR: $1:\1: $2 - |" >&2 && return 0
	return 1
}

fail=0
while IFS= read -r f; do
	[ -n "$f" ] || continue
	hits "$f" file "$FILE_RE" && fail=1
	hits "$f" client "$CLIENT_RE" && fail=1
	while IFS= read -r re; do
		[ -n "$re" ] || continue
		hits "$f" service "$re" && fail=1
	done <<<"${SG_VIEW_DOMAIN:-}"
	while IFS= read -r re; do
		[ -n "$re" ] || continue
		hits "$f" state "$re" && fail=1
	done <<<"${SG_VIEW_STATE:-}"
done <<<"$files"

if [ "$fail" -ne 0 ]; then
	echo "ERROR: the views above reach the domain instead of delegating to their view model (ADR-0137): a view constructs, binds and delegates, and the decision lives where no host is needed to test it." >&2
	exit 1
fi

echo "OK: view firewall holds ($count view file(s) under $root name no file, no client, no declared application state and no declared domain service)."
