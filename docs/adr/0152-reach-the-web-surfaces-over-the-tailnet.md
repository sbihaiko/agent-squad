# ADR-0152: Reach the web surfaces over the tailnet

## Status

Accepted.

## Context

The run dashboard and the hub listen on loopback. The owner reaches them from other devices on the tailnet, and must not expose them to the internet. Tailscale serve publishes one loopback port to the tailnet; Funnel would publish it to the internet. Each request needs an identity from the tailnet, and no credential may sit in the project.

## Decision

1. **Each server keeps its loopback bind.** The dashboard server and the hub bind `127.0.0.1` only. Guard: `tests/test_tailnet_exposure_guard.py` (`test_shipped_tree_binds_only_loopback`).
2. **The hub is the one tailnet entry, through one owner-set `tailscale serve`, never Funnel.** `squad tailnet serve-command` prints the command and starts nothing; the code runs no `tailscale serve`, no Funnel and no ACL change. A run dashboard is reached through the hub's `/dash/<run id>/` route. Guard: `tests/test_tailnet_check.py` (the serve command, and a Funnel hub is a problem), `tests/test_tailnet_exposure_guard.py` (`test_shipped_tree_runs_no_tailscale_state_change`), `tests/test_hub_dash_forward.py`, `tests/test_hub_mux_tailnet.py`.
3. **Host decides loopback or tailnet.** A Host of `127.0.0.1:<port>` or `localhost:<port>` is loopback. A Host equal to `tailnet.host` is tailnet. Any other Host is refused as `forbidden host`. Guard: `tests/test_tailnet_access.py`, `tests/test_hub_tailnet.py`, `tests/test_dashboard_tailnet.py`.
4. **A read or a write needs an allowed `Tailscale-User-Login`.** The login must equal one entry of `tailnet.allow`, ignoring case, and never by substring. A refusal names no listed login, and a display header never decides. Guard: `tests/test_tailnet_access.py`, `tests/test_dashboard_tailnet.py`, `tests/test_hub_tailnet.py`, `tests/test_hub_mux_tailnet.py`, `tests/test_hub_dash_forward.py`.
5. **A write also needs the tailnet Origin.** Any method other than `GET` or `HEAD` needs `Origin` equal to `https://<tailnet.host>`. Guard: `tests/test_tailnet_access.py`, `tests/test_dashboard_tailnet.py`, `tests/test_hub_tailnet.py`.
6. **The `tailnet` block is outside the resume hash.** A change only to that block keeps the hash that `run_start` records. A change to any other key changes it. Guard: `tests/test_tailnet_settings.py`.
7. **No credential is stored.** The project file refuses a credential-shaped `tailnet` key or value. Guard: `tests/test_tailnet_settings.py`.
8. **The editor stays a local `file://` page.** It is not served on the tailnet, and it carries no session overlay. Guard: `tests/test_hub_overlay.py` (the editor carries no overlay); the rest is a draft. `tests/test_quickstart_tailnet.py` checks only the quickstart sentence.
9. **A write that acts on the machine needs its own grant on the tailnet, off by default; loopback always sends.** A loopback send to a live session needs only the hub's own Origin and a JSON body, and no grant. A request that carries `Tailscale-User-Login` is a tailnet request whatever its Host: on a loopback Host the hub lets it read and refuses every write, so a send never passes as loopback. A tailnet write to a live session (`tailnet.terminals_send`, a `bool` in the project settings file, [ADR-0154](0154-the-phone-sends-to-a-session-by-the-wire-protocol.md)) or to the loop switch (`tailnet.loop_write`, set by `SQUAD_TAILNET_LOOP_WRITE`, [ADR-0162](0162-the-hub-writes-the-loop-switch-and-drains-by-default.md)) is refused, with its reason, until the owner sets its `bool` to true. Rules 4 and 5 still apply. The hub hosts no terminal, so the seam holds no `terminals_*` grant and the hub serves no terminal route; settings validation and the `.env` load ignore the three retired `terminals_*` keys, and every other unknown key is still refused. Where the tailnet send is off, the session overlay disables its field and names the setting. Guard: `tests/test_session_send.py`, `tests/test_hub_overlay.py`, `tests/test_hub_loop_switch_page.py`, `tests/test_tailnet_settings.py`, `tests/test_hub_no_terminal.py`.

### Header assumption

This assumption is unverified. The access seam (`squad/tailnet_access.py`) assumes that a tailnet request reaches the backend with Host equal to `tailnet.host`, and that its identity is the `Tailscale-User-Login` header set by `tailscale serve`. It also assumes that `tailscale serve` overwrites a client-supplied `Tailscale-User-Login`. If any part does not hold, only that seam changes. Guard: `tests/test_tailnet_access.py`.

## Consequences

A listed login reaches what a loopback caller reaches on the hub, limited to the profiles that list that login, except the tailnet writes rule 9 holds until the owner grants them. A loopback caller sends to a session with no grant. The owner's list is the only source of tailnet access, and the owner must keep it current.

A run dashboard server also answers a tailnet Host when its project file names `tailnet.host`. Rule 2 keeps it off the tailnet, because the owner's `tailscale serve` publishes only the hub port.

The `tailnet` block is personal: a machine name and logins. `squad config set tailnet.*` writes it to the project's gitignored `.env` (`SQUAD_TAILNET_HOST`, `SQUAD_TAILNET_ALLOW`, `SQUAD_TAILNET_LOOP_WRITE`, `SQUAD_TAILNET_PROFILER_WRITE`) and refuses when git would track that file. A value there wins over one in the project file. Guard: `tests/test_tailnet_env.py`.
