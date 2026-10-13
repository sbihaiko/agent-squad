# Tooling tickets and learnings live here

**Superseded by #20.** `SG_TOOLING_REPO` is retired: `env.sh` stops, naming it, when it is set, and
`address.sh` refuses a profile that sets it. Each repository has its own profile, its own state
directory (`~/.cache/squad-goal/<profile>`) and its own path on the hub (`/<profile>`). The sections
below record the two-repository setup as it stood before.

Decided 2026-10-12: **every new ticket and every learning about the loop's own tooling is
recorded in this repository** (`sbihaiko/agent-squad`), not in the emulator repository.
The emulator repository's tracker stays for the emulator.

## What has to be true for the loop to read this repository

The loop reads the repositories named by `$SG_REPOS`, which `contrib/squad-goal/scripts/env.sh`
resolves as the profile's `SG_REPO` **plus `SG_TOOLING_REPO` when it names one**
(`autofrontier.sh` reads every repository in that list). The live profile —
`contrib/squad-goal/scripts/profiles/mesenai.sh` — ends with:

```
unset SG_TOOLING_REPO   # the key is dormant by spec
```

So with the key unset the loop reads `sbihaiko/MesenAI` alone, and **a ticket in this
repository is invisible to it**: no label, no wave and no blocker will change that. Until
the key names this repository, this tracker is a register a human works from, not a queue
the loop drains.

Three ways out, in the owner's hands:

1. **Set `SG_TOOLING_REPO=sbihaiko/agent-squad` in the profile.** The intended path, and
   the one the tooling is written for — but it wakes a dormant feature: the loop then reads
   a second repository for the frontier and carries it in `$SG_REPOS` through forkclose and
   autopilot, so every place that assumed one repository becomes a two-repository path.
2. **Mirror the tickets into `sbihaiko/MesenAI`** as fork-only tickets, each carrying its
   `Fork-only work:` line. The frontier already reads that repository; the cost is a second
   set of issue numbers for the same work.
3. **Leave it parked**, worked by hand. Then say so here, so nobody reads the silence as a
   bug.

Whichever is chosen, this file is the place to record it.

## The route is the address, not a label (#1869)

`forkonly.sh` carries the one rule: **an issue that lives in the tooling repository
(`SG_TOOLING_REPO`) is delivered there — a local commit on the branch the land reads, no
PR — and an issue in `SG_REPO` is delivered as a PR.** The `squad-only` label, the
`fork-only` spelling before it, the `Squad-only work` body line and the `FORK-ONLY: all`
request marker all retire with that rule; `check-squad-only-retired.sh` fails while a live
script still reads any of them.

Two consequences: with **no** `SG_TOOLING_REPO` named, *nothing* is fork-only — the
retired route never comes back as a fallback; and a bare issue number resolves against the
repositories the loop operates, the profile's first and the tooling one second.
