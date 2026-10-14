# ADR-0138: A profile works a list of repositories, and one seam resolves every address

## Status

Accepted. Amended by the owner decision ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md)), verbatim: "three long-lived branches (`dev`, `stg`, `main`); a work branch lands in `dev` by a fast-forward push, with no pull request; the loop promotes `dev` to `stg` when the CI of the `dev` tip is green; the owner promotes `stg` to `main` with one command; the live copy publishes from `stg`; a card is Done when its branch is in `dev`."

## Context

The squad-goal loop modeled two repositories: the profile's own repository
(`SG_REPO`) and, as a special case, the squad's tooling repository
(`SG_TOOLING_REPO`). The code that decides which repository an issue belongs
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
     to. Its branch merges into `SG_FORK_BASE` in that checkout.
   - `pr`: the profile's repository. Its land is the merge of the pull request
     into `SG_BASE`, so no branch is named. A repository that lands by a fast-forward push
     to `dev` ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md)) has no pull request,
     and its `SG_BASE` is `dev`.
   - Any other repository: refused, with the address named on stderr.
     `sg_land_base` reads the same table.
   - A bare number names no repository. One that came with a board row is a
     legacy row and keeps the route it had, `local`, because the fork branch
     carries the bare number; a line on stderr names the row. One that came
     with no board row is the profile in force and lands by `pr`, and a line
     says so. A legacy row with no local land to keep is refused, never
     turned into a pull request in the profile's repository.
5. **Retired setting names.** `SG_TOOLING_REPO` is retired: its repository is
   one more entry of `SG_REPOS`. The `SG_TOOLING_REPO:SG_FORK_DIR` entry of
   `SG_REQUIRED_SETTINGS` retires with it. The seam reads a profile that
   still names `SG_TOOLING_REPO` as one more list entry after `SG_REPO`. The
   seam never routes on it. A route comes from the fork checkout, not from a
   key that names the tooling repository.

6. **The same seam gives the context of a repository.** `sg_repo_ctx`
   (and `bash address.sh <input>`) takes a profile name, a checkout, an
   address or a bare number. It gives the owner, the repository, the project,
   the checkout, the base branch, the fork directory, the state directory and
   the hub path. A bare number is the profile in force (`SG_PROFILE`), never
   the profile that the origin of the working directory matches. A repository
   that no profile names is refused by name, with the list of known
   repositories. Nothing falls back to another repository.
7. **Nothing reaches GitHub before a repository is resolved.** `env.sh` asks
   the seam first and stops on a profile that the seam refuses, so no script
   runs a command with no repository. No probe stays: no script reads GitHub
   to find which repository an issue lives in.
8. **Every script and the hub read the seam.** `env.sh` (the loop),
   `hub.py`, `hubmux.py`, `hubctl.sh` and `cqflush.sh` read it. No script and
   no hub keeps a copy of the rule. A script that starts with no profile in
   force gives its start directory to the seam as a checkout. It does not read
   that checkout's origin itself. The hub takes its checkout from the context,
   never from its working directory.
9. **A queued comment carries the repository it is for.** Its file is named
   `<owner>__<repo>#<n>-<tag>.md`, and `sg_cq_file` in the seam makes that
   name for the repository in force. `cqflush.sh` posts the comment to the
   repository in the name, whichever process flushes it and whichever queue
   directory holds it. The queue directory only isolates; it is never the write
   target. A name that no profile knows is refused by name and retired as
   `.dead`. It is never retried and never posted to another repository, the
   flusher's own included. A bare `<n>-<tag>.md` from before this rule is read
   as the repository of the profile whose state directory holds it.

## Consequences

- Each caller that must know a repository asks `address.sh`. A stage that
  parses an address itself is a second grammar that can disagree.
- `forkonly.sh` loads `address.sh` from the same directory. A copy of
  `forkonly.sh` without `address.sh` loads with no seam.
- Every writer of a queued comment names its file through `sg_cq_file`, or in
  Python through the same `<owner>__<repo>#<n>` prefix. A writer that names a
  bare issue number is a defect that a guard finds.
- Callers outside the seam that still read `SG_TOOLING_REPO` move to
  `SG_REPOS` with the rest of the multi-repository work. This ADR does not
  change them.

## Guard

`contrib/squad-goal/scripts/test-address.sh` (the resolver table, the
owner-and-name equality, the refusal of a short name, the context table, the
refusal of an unknown repository with its known list, a stub `gh` that fails a
script which reaches GitHub before it resolves a repository, and the readers) and
`contrib/squad-goal/scripts/test-land-target.sh` (one case per route, a bare number with a board
row and one with none, the log line of each, and the refusal) and
`contrib/squad-goal/scripts/test-cqflush-address.sh` (a comment for one repository, flushed by
another repository's process, is posted to the repository it names; a comment moved to another
directory still is; an unknown repository is retired and never posted; and no script writes a bare
issue number).
