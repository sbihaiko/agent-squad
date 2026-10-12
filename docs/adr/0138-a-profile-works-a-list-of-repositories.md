# ADR-0138: A profile works a list of repositories, and one seam resolves every address

## Status

Accepted.

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
     to. Its branch merges into `SG_FORK_BASE` in that checkout. A bare
     number is a legacy address and uses this row, because the fork branch
     carries the bare number.
   - `pr`: the profile's repository. Its land is the merge of the pull request
     into `SG_BASE`, so no branch is named.
   - Any other repository: refused, with the address named on stderr.
     `sg_land_base` reads the same table.
5. **Retired setting names.** `SG_TOOLING_REPO` is retired: its repository is
   one more entry of `SG_REPOS`. The `SG_TOOLING_REPO:SG_FORK_DIR` entry of
   `SG_REQUIRED_SETTINGS` retires with it. The seam reads a profile that
   still names `SG_TOOLING_REPO` as one more list entry after `SG_REPO`. The
   seam never routes on it. A route comes from the fork checkout, not from a
   key that names the tooling repository.

## Consequences

- Each caller that must know a repository asks `address.sh`. A stage that
  parses an address itself is a second grammar that can disagree.
- `forkonly.sh` loads `address.sh` from the same directory. A copy of
  `forkonly.sh` without `address.sh` loads with no seam.
- Callers outside the seam that still read `SG_TOOLING_REPO` move to
  `SG_REPOS` with the rest of the multi-repository work. This ADR does not
  change them.

## Guard

`contrib/squad-goal/scripts/test-address.sh` (the resolver table, the
owner-and-name equality, the refusal of a short name) and
`contrib/squad-goal/scripts/test-land-target.sh` (one case per route, and the
refusal).
