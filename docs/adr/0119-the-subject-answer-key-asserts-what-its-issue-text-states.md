# ADR-0119 — The subject's answer key asserts what its issue text states, and the 24-issue set can pass together

## Status

Accepted, 2026-10-02, `AGREED`. This version reverses decisions 1 and 2 of the first ADR-0119, written the same day from a panel that split `AGREED 2–1`. Owner direction before this panel: "use o panel pra decidir, eu nao quero ADR travando o projeto". Lens A: Codex `gpt-6-sol`, Lens B: Fable 5.1, both blind on one brief. They concurred, so no third lens judged. ADR-0000 licenses this rewrite: an outgrown ADR is rewritten in the change that outgrows it, and the owner's words outrank every ADR. Corrects the justification in ADR-0113 rule 4.

## Context

Run `runs/router-01-02-...-24-20261001-234426/` ran all 24 subject issues through the dynamic router graph. Its verdict was `failed`: the final gate re-ran issue 11's held-out command and issue 13's, and both went red on the final tree. The two held-out files had passed at their own landings.

The held-out tests in `bench/subject/acceptance/` were authored one issue at a time, so ten assertions contradict each other, in five groups:

1. **The fourth element of a tax rate row.** `test_issue_16.py:149-151` requires `TaxTable().load([("US-CA","8.5","groceries",True)])` to raise `ValueError`. `test_issue_07.py:467` requires `load([("US-TX", RATE, None, api.Rounding("half_up","0.05"))])` to succeed. `test_issue_02.py:206,235-241` carries `inclusive` the same way. The two demand different fields in one slot, so no single four-element contract exists.
2. **The refusal message's field list.** `test_issue_16.py:60-64` writes the accepted shapes out as one literal string: `(jurisdiction, rate[, category]) or {jurisdiction, rate[, category]}`. Issues 02 and 07 add `inclusive` and `rounding` to `TaxRate`, and the skeleton renders the shapes it is given, so the literal fails on a tree where those issues landed. Measured on run `...20261001-181137`: five failures, all in the shapes half.
3. **Issue 11's pinned field lists.** `test_issue_11.py:204,211` pin the product spec to 6 names and the coupon spec to 8. Issue 03 adds `state` as the seventh product field; issue 05 adds `Coupon.stack_group`. `test_issue_11.py:597-598` also pins the tax table's old arity wording, which issue 16 replaces.
4. **The declaring rule's missing-key exception.** `test_issue_13.py:177-178` requires `KeyError` through the discount path. `test_issue_06.py:273` requires `MissingContextKeys` for the same case.
5. **Placing an over-limit order.** `test_issue_24.py:531-560` places `Money('312.30')` for a customer with a `Money('150.00')` limit. Issue 04 makes `billing/sales/orders.py:489` raise `CreditLimitExceeded` for exactly that order.

Three more groups surfaced on 2026-10-03, in run `router-01-...-24-20261002-184720`, which ended `failed` on both:

6. **Issue 11's row fixtures.** `test_issue_11.py` hardcoded `"state": "active"` (issue 03) and `"stack_group": None` (issue 05) in its product and coupon rows. Issue 11 has no blocker, and 03 and 05 are blocked by 11, so four of its 20 tests failed on any tree where 03 and 05 had not landed. The rows now come from the record type's own declared fields.
7. **Issue 20's shapes.** `test_issue_20.py` demanded exception classes (`NoOpenRestock`, `UnknownProduct`), ledger row attributes (`.outstanding`), `()` result shapes, a frozen and validated `RestockReceipt`, and an unchanged request quantity. The text states none of them. It now asserts that a refusal raises and names the sku, reads a row as the pair the text names (a request and its receipt or `None`), and reads a remainder by what it does.
8. **Issue 21's shapes** (2026-10-03). `test_issue_21.py` read `row.outstanding`, `row.is_closed`, `row.lead_time_days` and `row.request`, pinned `api.NoBackorder`, `ValueError` for a negative `n`, `(sku, mean)` tuple entries, a literal projected date, and `SKU-3` present in the report. Issues 19 to 21 state none of them. It now reads a row as the pair, asks that refusals raise, reads a ranking entry as a sku or a `(sku, mean)` pair, and checks `expected_fill` by how it moves with the mean. `test_issue_17.py` named `IndeterminateCurrency`, which 17-*.md does not name; it now asks only that the refusal raises, and the class-hierarchy test is dropped. Other files checked by grep for unstated exception classes: none found. Not checked: unstated attributes and keyword names outside exception classes (for example `Cart(..., currency=)` in 17).

The first ADR-0119 read these as a property of the benchmark and kept the key fixed, on the ground that rewriting an assertion would move the key toward what the squad produced.

The second panel found that reading wrong. In each group the issue **text** states a property, and the held-out assertion demands more than that property: a literal count, a row shape, a construction, or a behaviour a later issue's text explicitly changes. `11-*.md:16,32-33` says the spec owns the field order and reconciles against each record type's declared fields, and pins no count. `06-*.md:18,31-33,45` states the exception change as the criterion. `04-*.md:24` says `place` refuses an order over the limit, and `24-*.md` is about `over_exposed`, which compares a customer's summed exposure against the limit — two placed orders of 100.00 each give the same 200.00 exposure without either order being over.

Groups 1 and 2 are the exception, and it is one clause wide. `~/sources/subject/issues/16-*.md:22-24` states the tuple arity as an acceptance criterion: a 2- and a 3-tuple rate row parse, and a 1- or 4-element one raises. `02-*.md:12-13` says `inclusive` travels through `_parse_row` "for both row shapes", and `07-*.md:80-81` says `rounding` "defaults absent in both `_parse_row` shapes". Read literally, those two clauses ask for a fourth and a fifth tuple slot, and each original held-out test put a different field in the fourth. Only issue 16 states an arity as a criterion, so its clause wins and the two Description clauses are narrowed: `inclusive` and `rounding` travel by the mapping shape. That narrowing is the one place this ADR reads a text narrowly, it is why the two files that carry the fields were re-anchored, and it is one line to reverse.

## Decision

1. **Every held-out test asserts the property its issue text states, and no more.** The nine over-specified assertions are re-anchored to that property. No expected value moves toward what the squad produced: a value derived from the record type's own declared fields is a re-anchor, and a literal moved to whatever the run happened to print is not.
2. **The 24-issue set can pass together.** The all-pass bar comes back: a `failed` verdict from the router run on this set is again a defect to investigate, and ADR-0113 rules 12 and 13 have a target.
3. **Per-issue grading stays the instrument for tuning.** `squad/subject.py:90` grades one issue per run with `commands_from: "spec"`. The 24-issue run measures delivery into one tree.
4. **The issue texts and the fixture project do not change.** The benchmark's input is fixed. Only an assertion that demands more than its own text states moves.
   - *Amended 2026-10-03.* Two issue clauses are narrowed because the texts conflicted, and a re-anchored test cannot settle a conflict between two texts (run `...20261003-003608` stopped needs-human at issue 16). Group 1 is ruled: issue 16's arity wins, so `inclusive` and `rounding` travel by the mapping row shape only. Issue 02: "carry it through `TaxTable._parse_row` by the mapping row shape", and `_parse_row` accepts `inclusive` "in the dict shape only", with a positional row keeping its 2 or 3 elements. Issue 07: "through `_parse_row` by the mapping row shape". `test_issue_16.py` `RATE_ROW` and `TAX_LINE` no longer list `inclusive`/`rounding` on a tuple shape. Reversal is one line each: restore the issue 02 and issue 07 wording from the parent of commit `ad804ea` in `~/sources/subject`, and the pattern lines in `test_issue_16.py`.
5. **The key stays pinned.** `tests/test_subject_key.py` holds the hash of every held-out file, so any change to the key is a deliberate act in the same commit.
6. **Commit `0910992`'s reasoning was wrong.** It set `blocked_by: [11]` on issues 03 and 05 to fix the conflict by ordering. Ordering cannot fix a conflict: the final gate re-runs every landed child's command, so issue 11's test went red again the moment a later land added a field. The edges stay, they are harmless, and they rest on no argument.

## Considered options

- **Keep the first version: the key fixed as written, the run a stress test with no bar.** Rejected. It leaves the benchmark unwinnable, so a green run cannot distinguish a good flow from a bad one, and ADR-0113 rules 12 and 13 can only stop a landing from making things worse.
- **Partition the 24 into mutually consistent groups; each group its own set** (the earlier Lens A dissent). Rejected. A partition that keeps the literal assertions keeps the over-specification, and no partition had been proposed or measured.
- **Re-cut the issues themselves: restore the dropped `independence` field and re-derive the texts and the tests.** Rejected, and this is the panel's answer to "what would change my mind". Only one clause in the 24 texts conflicts, the tuple-arity clause that Context names, and the arity criterion settles it without a re-cut. A full re-cut would rebuild the benchmark to fix assertions, and it would move every issue text, which decision 4 forbids.

## Consequences

- `docs/adr/0113-...md` rules 12 and 13 gain a green target, and the final gate is no longer a check that can never pass.
- `bench/subject/README.md` no longer says the key is fixed as written. It says the key asserts its issue text, and points here.
- Lens B asked for the group-1 choice to be ratified rather than assumed. It is ratified here, in Context: issue 16's arity criterion governs the tuple shape, and the two Description clauses are narrowed to the mapping shape.
- The re-anchors are reversible individually. Each is one assertion group in one held-out file, and the pin in `tests/test_subject_key.py` says so in the same commit.

## Guard

Tests that guard this rule:

- `tests/test_subject_key.py::test_every_held_out_test_still_hashes_to_its_pin`
- `tests/test_subject_key.py::test_the_index_lists_the_same_24_issues_and_the_file_the_pins_name`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
