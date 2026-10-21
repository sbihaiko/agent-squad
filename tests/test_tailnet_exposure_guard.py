"""The shipped code binds only the loopback address and never changes Tailscale serve, funnel or ACL state.

Static only: the guard reads source text and parses it; it opens no socket and runs no command."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMED_SERVERS = {"squad/dashboard_server.py", "contrib/squad-goal/scripts/hub.py", "contrib/squad-goal/scripts/hubmux.py"}
SHIPPED_ROOTS = ("squad", "contrib/squad-goal/scripts", "scripts", "commands", ".claude-plugin")
SCANNED_SUFFIXES = {".py", ".sh", ".md", ".json"}
SERVER_CLASSES = {"HTTPServer", "ThreadingHTTPServer"}
LOOPBACK = "127.0.0.1"
SHELL_CALLS = {"run", "Popen", "call", "check_call", "check_output", "system", "popen", "getoutput", "getstatusoutput"}
SERVE_OR_FUNNEL = re.compile(r"(?:^|[\s;&|(`'\"/])tailscale\s+(?:serve|funnel)\b(?!\s+status\b)")
ACL_URL = re.compile(r"api\.tailscale\.com\S*/acl\b")
ACL_WRITE = re.compile(r"-X\s*(?:POST|PUT|PATCH|DELETE)\b|--request[\s=]+(?:POST|PUT|PATCH|DELETE)\b|\s(?:-d|--data(?:-\w+)?)(?=[\s=])")
PRINTS = re.compile(r"(?:echo|printf)\b")


def shipped_files(root):
    found = []
    for rel in SHIPPED_ROOTS:
        base = root / rel
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SCANNED_SUFFIXES:
                continue
            if p.name.startswith(("test_", "test-")) or {"fixtures", "__pycache__"} & set(p.parts):
                continue
            found.append(p)
    return sorted(found)


def _callee(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _module_strings(tree):
    consts = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            consts[node.targets[0].id] = node.value.value
    return consts


def _bind_host(address, consts):
    if not isinstance(address, (ast.Tuple, ast.List)) or not address.elts:
        return None
    host = address.elts[0]
    if isinstance(host, ast.Constant) and isinstance(host.value, str):
        return host.value
    if isinstance(host, ast.Name):
        return consts.get(host.id)
    return None


def _is_super_init(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "__init__"
            and isinstance(node.func.value, ast.Call) and _callee(node.func.value.func) == "super")


def check_loopback_binds(source, name="<source>"):
    if name.endswith(".sh"):
        return [f"{name}:{n}: an HTTP server call without the literal {LOOPBACK}"
                for n, line in enumerate(source.splitlines(), 1) if "HTTPServer(" in line and f'"{LOOPBACK}"' not in line]
    if "HTTPServer" not in source:
        return []
    tree = ast.parse(source)
    consts = _module_strings(tree)
    nodes = list(ast.walk(tree))
    classes = [n for n in nodes if isinstance(n, ast.ClassDef)]
    subclasses = set()
    changed = True
    while changed:
        changed = False
        for cls in classes:
            if cls.name not in SERVER_CLASSES | subclasses and any(_callee(b) in SERVER_CLASSES | subclasses for b in cls.bases):
                subclasses.add(cls.name)
                changed = True
    spans = [(cls.lineno, cls.end_lineno) for cls in classes if cls.name in subclasses]
    binds = [n for n in nodes if isinstance(n, ast.Call) and _callee(n.func) in SERVER_CLASSES]
    binds += [n for n in nodes if _is_super_init(n) and any(lo <= n.lineno <= hi for lo, hi in spans)]
    findings = []
    for call in binds:
        address = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "server_address"), None)
        host = _bind_host(address, consts)
        if host != LOOPBACK:
            shown = repr(host) if host is not None else "a host that is not a fixed string"
            findings.append(f"{name}:{call.lineno}: binds {shown}; a shipped server binds the literal {LOOPBACK}")
    return findings


def _changes_tailscale(command):
    if SERVE_OR_FUNNEL.search(command):
        return True
    return bool(ACL_URL.search(command) and ACL_WRITE.search(command))


def check_no_tailscale_change(source, name="<source>"):
    if "tailscale" not in source.lower():
        return []
    if name.endswith(".py"):
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node.func) in SHELL_CALLS:
                for arg in [*node.args, *(k.value for k in node.keywords)]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _changes_tailscale(arg.value):
                        found.append(f"{name}:{node.lineno}: runs a tailscale serve, funnel or ACL change")
            elif isinstance(node, (ast.List, ast.Tuple)):
                words = " ".join(e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
                if _changes_tailscale(words):
                    found.append(f"{name}:{node.lineno}: runs a tailscale serve, funnel or ACL change")
        return found
    found = []
    for n, line in enumerate(source.splitlines(), 1):
        s = line.strip()
        if s.startswith("#") or (PRINTS.match(s) and not re.search(r"\$\(|`|[|;&]", s)):
            continue
        if _changes_tailscale(s):
            found.append(f"{name}:{n}: runs a tailscale serve, funnel or ACL change")
    return found


def test_guard_fails_on_a_fixture_that_binds_all_interfaces(tmp_path):
    mod = tmp_path / "server.py"
    mod.write_text('from http.server import ThreadingHTTPServer\nServer = ThreadingHTTPServer(("0.0.0.0", 8080), object)\n')
    assert check_loopback_binds(mod.read_text(), mod.name)


def test_guard_fails_on_a_bind_address_read_from_a_setting():
    src = 'from http.server import ThreadingHTTPServer\nHOST = cfg["tailnet"]["bind"]\nServer = ThreadingHTTPServer((HOST, 8080), object)\n'
    assert check_loopback_binds(src, "server.py")


def test_guard_fails_on_a_subclass_whose_init_takes_its_host_from_an_argument():
    src = (
        "from http.server import ThreadingHTTPServer\n"
        "class S(ThreadingHTTPServer):\n"
        "    def __init__(self, host, port):\n"
        "        super().__init__((host, port), object)\n"
    )
    assert check_loopback_binds(src, "server.py")


def test_guard_passes_a_fixture_that_binds_loopback_through_a_constant():
    src = (
        "from http.server import ThreadingHTTPServer\n"
        'LOOPBACK = "127.0.0.1"\n'
        "class S(ThreadingHTTPServer):\n"
        "    def __init__(self, port):\n"
        "        super().__init__((LOOPBACK, port), object)\n"
    )
    assert check_loopback_binds(src, "server.py") == []


def test_shipped_tree_binds_only_loopback():
    found = []
    for path in shipped_files(ROOT):
        found += check_loopback_binds(path.read_text(), str(path.relative_to(ROOT)))
    assert found == []


def test_shipped_scan_covers_every_named_server():
    scanned = {str(p.relative_to(ROOT)) for p in shipped_files(ROOT)}
    assert NAMED_SERVERS <= scanned


def test_shipped_scan_skips_tests_and_fixtures():
    scanned = {p.name for p in shipped_files(ROOT)}
    assert not any(n.startswith(("test_", "test-")) for n in scanned)


def test_guard_fails_on_a_fixture_script_that_runs_tailscale_funnel(tmp_path):
    mod = tmp_path / "expose.py"
    mod.write_text('import subprocess\nsubprocess.run(["tailscale", "funnel", "8080"], check=True)\n')
    assert check_no_tailscale_change(mod.read_text(), mod.name)


def test_guard_fails_on_a_fixture_script_that_runs_tailscale_serve_on_a_port(tmp_path):
    mod = tmp_path / "expose.py"
    mod.write_text('import subprocess\nsubprocess.run("tailscale serve 7700", shell=True)\n')
    assert check_no_tailscale_change(mod.read_text(), mod.name)


def test_guard_fails_on_a_shell_script_that_runs_tailscale_serve(tmp_path):
    sh = tmp_path / "expose.sh"
    sh.write_text("#!/bin/bash\ntailscale serve --bg 7700\n")
    assert check_no_tailscale_change(sh.read_text(), sh.name)


def test_guard_fails_on_a_shell_script_that_changes_the_acl(tmp_path):
    sh = tmp_path / "acl.sh"
    sh.write_text('curl -X PUT -d @acl.json "https://api.tailscale.com/api/v2/tailnet/-/acl"\n')
    assert check_no_tailscale_change(sh.read_text(), sh.name)


def test_guard_passes_a_fixture_that_only_prints_the_command(tmp_path):
    mod = tmp_path / "hint.py"
    mod.write_text('print("run: tailscale funnel 8080 to expose it")\n')
    assert check_no_tailscale_change(mod.read_text(), mod.name) == []
    sh = tmp_path / "hint.sh"
    sh.write_text('echo "tailscale serve --bg 7700"\nprintf "%s\\n" "tailscale funnel 8080"\n')
    assert check_no_tailscale_change(sh.read_text(), sh.name) == []


def test_guard_passes_a_read_of_serve_state_and_of_the_acl(tmp_path):
    sh = tmp_path / "read.sh"
    sh.write_text('tailscale serve status\ncurl -s "https://api.tailscale.com/api/v2/tailnet/-/acl"\n')
    assert check_no_tailscale_change(sh.read_text(), sh.name) == []
    mod = tmp_path / "read.py"
    mod.write_text('import subprocess\nsubprocess.run(["tailscale", "serve", "status"], capture_output=True)\n')
    assert check_no_tailscale_change(mod.read_text(), mod.name) == []


def test_shipped_tree_runs_no_tailscale_state_change():
    found = []
    for path in shipped_files(ROOT):
        found += check_no_tailscale_change(path.read_text(), str(path.relative_to(ROOT)))
    assert found == []
