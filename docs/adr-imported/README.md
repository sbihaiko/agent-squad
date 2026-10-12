# Imported from the emulator's ADR register

These two decisions were written in `sbihaiko/MesenAI`'s register (`docs/adr/ci-build/`)
on 2026-10-10 and 2026-10-12, where they were never committed: the emulator's register had
no honest area for a decision about the squad loop. The tooling now has its own register in
`../adr/`, and the tickets that source these decisions (`#3`–`#8`) live in this repository,
so the files moved here with their numbers.

They are kept **verbatim**, evidence and all — the run ids, PR numbers and timestamps are
the argument they make. This register's `ADR-0000 §6` ("ADRs are never ledgers") governs
`../adr/`; a decision written *into* that register is written in that shape. These keep the
shape they were written in, and they keep their emulator-register numbers, which are not
this register's numbering.

The next decision about the same subject belongs in `../adr/`, in that register's shape,
citing what stands here as the reason.
