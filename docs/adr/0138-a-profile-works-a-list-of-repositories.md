# ADR-0138: A profile works a list of repositories, and one seam resolves every address

## Status

Accepted.

## Context

The squad-goal loop modeled two repositories: the profile's own repository
(`SG_REPO`) and, as a special case, the squad's tooling repository
(`SG_TOOLING_REPO`). Its state, its comment queue and its hub were also one per
machine, so two repositories that carry the same issue number shared markers,
live runs and queued comments. The code that decides which repository an issue belongs
to, and where its work lands, existed only in the live copy of the skill. The
repository the loop ships from did not have it, so a child that worked in the
repository could not see the code its work builds on.

That code also called two repository names the same when only their short
names matched, so `other/tool` and `owner/tool` were one repository. A land is
a merge, and a revert does not undo a merge cheaply. A wrong match is a merge
into the wrong repository.

The owner wants several repositories worked at once. Adding a repository must
be adding a profile, not adding a special case.

## Decision

1. **A profile works a list of repositories.** The profile names them in
   `SG_REPOS`, the primary first. `sg_repos` (in
   `contrib/squad-goal/scripts/address.sh`) prints that list, each repository
   once.
2. **One function resolves every spelling of a repository.**
   `sg_resolve_repo` takes an address `owner/repo#N`, a bare `N` or `#N`, a
   profile name, or a checkout directory, and prints one `owner/name`:
   - an address gives the repository it names;
   - a bare number gives the profile's repository (`SG_REPO`);
   - a profile name gives the repository that profile names;
   - a checkout gives the repository of its `origin` remote.
3. **Two names are the same repository only when owner and name both
   match.** `sg_same_repo` resolves both names first. A short name with no
   owner resolves only when exactly one repository in the list has that name.
   Else the resolver refuses it and names it on stderr.
4. **The land rule answers in the repository that the address names, and in no
   other.** `sg_land_target` prints the checkout, the base, the branch and the
   route of an addressed issue:
   - `local`: the repository that the fork checkout (`SG_FORK_DIR`) resolves
     to. Its branch merges into `SG_FORK_BASE` in that checkout. A bare
     number is a legacy address and uses this row, because the fork branch
     carries the bare number.
   - `pr`: the profile's repository. Its land is the merge of the pull request
     into `SG_BASE`, so no branch is named.
   - Any other repository: refused, with the address named on stderr.
     `sg_land_base` reads the same table.
5. **One seam turns a repository identity into a context.**
   `contrib/squad-goal/scripts/repoctx.sh` takes a profile slug, a checkout, an
   address `owner/repo#N` (or `owner/repo`) or a bare `N`. It prints the
   context: profile, repository, owner, project, checkout, base branch, fork
   directory, state directory, shared directory, hub path, issue, and the
   repository list. A bare `N` resolves to the profile in force (`SG_PROFILE`,
   else the profile that matches the `origin` remote of the current
   directory). A repository that no profile names is refused by name; nothing
   falls back to another repository. Repositories compare by owner and name,
   lower-case. A profile is read on its own, so it cannot borrow a setting from
   another profile.
6. **One state directory per repository.** `SG_STATE` is
   `<base>/<profile>`; `<base>` is `SG_STATE_BASE`, else the per-user cache
   directory. A profile that sets `SG_STATE` keeps its directory. Markers,
   locks, `active-runs.txt`, `auto/`, the comment queue, the request queue,
   tests, alerts, `watch.log` and `health.json` live there. The per-user cache
   directory is named in the seam only (`env.sh`, `repoctx.sh`, `statedir.py`).
7. **What is machine-wide lives in one shared directory.** `SG_SHARED` is
   `<base>/_shared`. It holds the provider quota markers (`codex-limited`,
   `agy-limited`, `quota-guard`) and the project-level board cache (the owner
   kind and the Status field of a project), one per owner and project. The
   map from issue to board card holds one repository's issues, so it stays in
   that repository's state directory. The model policy (`models.json`) ships
   with the scripts and is shared by construction.
8. **The address is the carrier of a queued comment.** A comment file is
   `<owner>__<repo>#<N>-<tag>.md`; `cqflush.sh` posts it to that address
   whatever directory it sits in. An address that no profile names goes to
   `.dead` and is reported by name; it is never posted to the repository of the
   flusher or of the directory. A file named `<N>-<tag>.md` is addressed by the
   directory it sits in: the repository of the profile that owns that state
   directory. A queue under a state directory that no profile owns goes to
   `.dead`.
9. **Retired settings map into the list and warn; they do not stop a run.**
   `SG_TOOLING_REPO` and `SG_FORK_DIR` no longer name a second repository.
   When a profile sets them, the repository of each becomes one entry of the
   list (`SG_REPOS`, own repository first), and the seam prints one warning per
   setting for each state directory. `SG_FORK_DIR` stays the checkout of the
   legacy land route in decision 4 until a profile gives that repository its
   own profile. No route reads `SG_TOOLING_REPO`. The loop and the hub read the
   profile's own repository; the list is for the resolver and for the profile
   that migrates.
10. **One hub, one port, one path per profile.** `hubmux.py` serves
    `/<profile>` for each profile, lists the known repositories and their live
    workers at `/`, and answers an unknown path with 404 and that list.
    `hubctl.sh` accepts a running hub only when `/api/hub` names the squad hub
    and serves the profile of the current directory; it never reports one
    repository's hub as the hub of another.
11. **One health agent per repository.** The launchd label is
    `com.squad-goal.health.<profile>`, so two supervisors do not share one agent.

## Consequences

- Each caller that must know a repository asks `repoctx.sh` or `address.sh`. A
  stage that parses an address itself is a second grammar that can disagree.
- No script reads GitHub before it resolves a repository. `address.sh` and
  `repoctx.sh` read no GitHub state.
- `forkonly.sh` loads `address.sh` from the same directory. A copy of
  `forkonly.sh` without `address.sh` loads with no seam. A copy of `hub.py` or
  `board.py` without `statedir.py` does not start.
- A legacy comment, or a legacy bare-number board row, keeps its route. The
  old shared state directory is not migrated; each repository starts with an
  empty directory.

## Guard

`contrib/squad-goal/scripts/test-address.sh` (the resolver table, the
owner-and-name equality, the refusal of a short name) and
`contrib/squad-goal/scripts/test-land-target.sh` (one case per route, and the
refusal); `test-repoctx.sh` (the seam table, the refusal by name, the retired
settings); `test-state-isolation.sh` (state per repository, the comment
carrier); `test-review.sh` (the shared provider markers); `test-health-install.sh`
(the label per profile); `test-state-literal.sh` (the cache directory is named
in the seam only); `test-gh-resolves.sh` (no script reaches GitHub before it
resolves a repository); `tests/test_state_seam.py` (the Python half of the
state seam and the shared board cache); `tests/test_hub_mux.py` (one hub, a
path per profile).
