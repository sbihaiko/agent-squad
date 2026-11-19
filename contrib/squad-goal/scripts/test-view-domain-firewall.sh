#!/bin/bash
# seconds: 0.37
# Regression test for the second half of the GUI rule (#1849, ADR-0137 sections 3 and 4): verify-view-domain-firewall.sh refuses
# a view whose code-behind reaches the domain - a file, a client, the host's own state, a domain service - instead of
# constructing, binding and delegating to its view model. The rule is the direction; the paths and the domain names are the
# host's, handed over through the environment.
# Offline and hermetic: throwaway trees under the temp dir, no network, no repo, no emulator. usage: bash test-view-domain-firewall.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
V="$d/verify-view-domain-firewall.sh"
[ -f "$V" ] || ko "the check exists at $V"

mkdir -p "$T/empty" "$T/host/Views"
# The host's domain, declared the way a host profile declares it: its namespaces and its application state.
DOMAIN='\bContoso\.Billing\.|\bILedgerWriter\b'
STATE='\bAppState\b'
want() { # <case> <want-rc> <root> <substring...> : run the check, assert the exit and that every substring is named
  local name=$1 rc=$2 root=$3; shift 3
  local out got; out=$(env SG_VIEW_DOMAIN="$DOMAIN" SG_VIEW_STATE="$STATE" bash "$V" "$root" 2>&1); got=$?
  if [ "$got" != "$rc" ]; then ko "$name: exit $got, wanted $rc ($(printf '%s' "$out" | head -3 | tr '\n' ' '))"; return; fi
  local s; for s in "$@"; do
    printf '%s' "$out" | /usr/bin/grep -qF -- "$s" || { ko "$name: output never names '$s' ($(printf '%s' "$out" | tr '\n' ' '))"; return; }
  done
  ok "$name"
}

# 1. no view files: the rule is the direction, so a tree that carries no view has nothing to refuse
want "no views: nothing to scan" 0 "$T/empty" "OK"

# 2. a code-behind that reads a file: it decides, instead of stating an intent to its view model
mkdir -p "$T/host/Views" "$T/file"
cat > "$T/file/PlayerView.axaml.cs" <<'CS'
using Avalonia.Controls;
namespace Host.Views;
public partial class PlayerView : UserControl {
	// File.ReadAllText(p) is what the rule keeps out of here - a comment is not a dependency.
	public string Load(string p) => System.IO.File.ReadAllText(p);
}
CS
want "a view reading a file is refused" 1 "$T/file" "PlayerView.axaml.cs" "file"

# 3. a code-behind that opens a client
mkdir -p "$T/client"
cat > "$T/client/NetView.axaml.cs" <<'CS'
using Avalonia.Controls;
namespace Host.Views;
public partial class NetView : UserControl {
	private readonly HttpClient _http = new HttpClient();
}
CS
want "a view opening a client is refused" 1 "$T/client" "NetView.axaml.cs" "client"

# 4. a code-behind that instantiates the host's own domain service
mkdir -p "$T/svc"
cat > "$T/svc/BillView.axaml.cs" <<'CS'
using Avalonia.Controls;
namespace Host.Views;
public partial class BillView : UserControl {
	private readonly ILedgerWriter _ledger = new Contoso.Billing.LedgerWriter();
}
CS
want "a view instantiating a domain service is refused" 1 "$T/svc" "BillView.axaml.cs" "service"

# 5. a code-behind that mutates application state
mkdir -p "$T/state"
cat > "$T/state/HomeView.axaml.cs" <<'CS'
using Avalonia.Controls;
namespace Host.Views;
public partial class HomeView : UserControl {
	public void OnEnter() => AppState.Current.Tile = "home";
}
CS
want "a view mutating application state is refused" 1 "$T/state" "HomeView.axaml.cs" "state"

# 6. the clean half: construct, bind, delegate - the same tree, the same host domain, no offence
mkdir -p "$T/clean"
cat > "$T/clean/CleanView.axaml.cs" <<'CS'
using Avalonia.Controls;
namespace Host.Views;
public partial class CleanView : UserControl {
	public CleanView() { InitializeComponent(); }
	public void OnPlay() => (DataContext as PlayViewModel)?.PlayCommand.Execute(null);
}
CS
want "the clean half passes" 0 "$T/clean" "OK"

# 7. every offence is named, not just the first, and the file is reported line by line
mkdir -p "$T/two"
cp "$T/file/PlayerView.axaml.cs" "$T/two/PlayerView.axaml.cs"
cp "$T/state/HomeView.axaml.cs" "$T/two/HomeView.axaml.cs"
want "both offenders are reported" 1 "$T/two" "PlayerView.axaml.cs" "HomeView.axaml.cs"

# 8. a mention inside a line comment is prose, not a dependency
mkdir -p "$T/comment"
cat > "$T/comment/NoteView.axaml.cs" <<'CS'
using Avalonia.Controls;
namespace Host.Views;
public partial class NoteView : UserControl {
	// Nothing here calls File.Exists or AppState: the view delegates and nothing else.
	public void OnGo() => (DataContext as NoteViewModel)?.GoCommand.Execute(null);
}
CS
want "a comment is not a dependency" 0 "$T/comment" "OK"

# 9. what is refused is the reach, not the mention: a namespace import with nothing reached through it is not an offence
mkdir -p "$T/using"
cat > "$T/using/PlainView.axaml.cs" <<'CS'
using System.IO;
using Avalonia.Controls;
namespace Host.Views;
public partial class PlainView : UserControl {
	public void OnGo() => (DataContext as PlainViewModel)?.GoCommand.Execute(null);
}
CS
want "a mere namespace import is not a reach" 0 "$T/using" "OK"

# 10. the rule carries no host: a host that declares no domain still gets the two generic reaches
out=$(env SG_VIEW_DOMAIN= SG_VIEW_STATE= bash "$V" "$T/file" 2>&1); rc=$?
if [ "$rc" = 1 ] && printf '%s' "$out" | /usr/bin/grep -qF "file"; then ok "the generic reaches stand without host config"; else ko "generic reaches: exit $rc ($out)"; fi
out=$(env SG_VIEW_DOMAIN= SG_VIEW_STATE= bash "$V" "$T/svc" 2>&1); rc=$?
if [ "$rc" = 0 ]; then ok "a host domain nobody declared is not guessed"; else ko "undeclared domain: exit $rc ($out)"; fi

[ "$fail" = 0 ] && echo "test-view-domain-firewall: all green" || echo "test-view-domain-firewall: FAILED"
exit "$fail"
