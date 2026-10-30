---
description: Reach the squad hub from the owner's phone over the tailnet. No argument checks each step; --apply (owner only) serves the hub and sets the tailnet config.
argument-hint: "[--apply]"
allowed-tools: Bash(bash:*)
---

Check, or set up, phone access to the hub over the tailnet (ADR-0152, `docs/quickstart.md` "Tailnet access"). The hub keeps its loopback bind; Tailscale Serve fronts it; Funnel stays off.

Personal and machine values live in the project's gitignored `.env`:

```
TAILNET_HOST=<MagicDNS name of this machine>
TAILNET_ALLOW=<tailnet login allowed to reach the hub>
TAILSCALE_BIN=<path to the tailscale CLI>
HUB_PORT=7700
```

Run from the project directory:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/start-mobile.sh" $ARGUMENTS
```

- `--apply` is an access change: run it only when the owner typed it. An agent runs the check only and relays the printed owner steps.
- The Serve approval and the HTTPS certificates are set in the Tailscale admin console; the script prints the link and the phone URL.
