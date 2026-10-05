# Jev on real usage: no threshold can be set yet

Date: 2026-09-30. Ticket: #13. Model: `jev-1.13.0`, pinned. Baseline: `claude-sonnet-5-5`, medium effort, no tools.

## Answer

The data is too small and the labels are too noisy to set a confidence threshold. No threshold enters any gate (ADR-0012, ADR-0018). A gate stays deterministic.

The owner asked for real usage, no mocks, no synthetic text. The earlier synthetic probes scored near 100 percent. Real requests score about 37 percent. The synthetic result does not transfer.

## Method

- Real text came from past Claude Code sessions on this machine. The owner approved sending repo text to Jev.
- Raw text stays in `scripts/archive/jev_data_real/`, which git ignores. Only aggregates are committed in `scripts/archive/jev_results_real/`.
- A secret scrubber ran before any send. Jev calls carry a 60 s deadline (ADR-0051).
- Jev calls: 601. Baseline calls: 601. Jev spend is unverified. Baseline spend used the OAuth usage limits.
- Code: `scripts/archive/jev_real/extract.py`, `probe_skills.py`, `probe_screen.py`, `analyze.py`.

## Probe 1: skill choice on real requests

Slices:

- S1: a skill the owner named in the request. 83 items.
- S2: a skill that ran after a plain request. 108 items. The label is inferred, so it is noisy.
- S0: no skill ran. 120 items. A skill pick here is a weak false-pick label.

| Set | Slice | n | Jev top-1 | Baseline top-1 | Paired diff, 95% CI |
|---|---|---|---|---|---|
| All | S1 | 83 | 0.301 | 0.361 | -0.060 [-0.145, 0.024] |
| All | S2 | 108 | 0.417 | 0.370 | 0.046 [-0.019, 0.111] |
| All | S1+S2 | 191 | 0.366 | 0.366 | -0.052 to 0.052 |
| 80+ characters | S1+S2 | 95 | 0.526 | 0.474 | [-0.021, 0.126] |

Jev does not beat the baseline on all requests. Every paired interval holds zero, except S2 on the 80+ set.

Precision by confidence, S1+S2, all requests:

| Threshold | Covered | Precision | 95% CI |
|---|---|---|---|
| 0.7 | 77 | 0.571 | [0.46, 0.68] |
| 0.8 | 57 | 0.649 | [0.52, 0.76] |
| 0.9 | 44 | 0.705 | [0.56, 0.82] |
| 0.95 | 29 | 0.724 | [0.54, 0.85] |

Calibration error is 0.26 on all requests and 0.14 on the 80+ set. High confidence is not reliable on short, noisy requests.

## The 80+ variant

Real owner requests are short. Many have no skill signal. The 80+ set keeps requests of 80 characters or more. The owner did not choose this cut. It is a declared exploratory variant.

- On a time-ordered split, the fit slice (66 items) reaches 90 percent precision at confidence 0.79. No threshold reaches 95 percent.
- On the test slice (29 items), precision at 0.79 is 0.857 over 14 covered items. The interval is [0.60, 0.96].
- That is one threshold from one small slice. It is a hypothesis, not a setting.
- On all requests, no threshold reaches 90 or 95 percent on the fit slice.

False picks on S0 at confidence 0.8 or more: 4 of 120 on all requests, 1 of 55 on the 80+ set. Jev picks a skill on 55 of 120 no-skill items at any confidence.

## Probe 2: prompt-injection screen on real fetched content

290 real tool results: 143 WebFetch and 147 WebSearch. No call errored. No label of "attack" exists, so this probe measures agreement with a baseline, not accuracy.

| Jev threshold | Flagged | Rate |
|---|---|---|
| 0.1 | 70 | 0.241 |
| 0.3 | 46 | 0.159 |
| 0.5 | 42 | 0.145 |
| 0.7 | 41 | 0.141 |
| 0.9 | 13 | 0.045 |

At 0.5, Jev and the baseline flag 25 items together. Jev alone flags 17. The baseline alone flags 2.

Agent review of the discordant and shared items:

- The 25 shared items are search results about companies, a server BIOS, and a tax code, plus empty results and one redirect notice. None holds an injection. Both screens flagged benign text. Empty search results and "no links found" replies score high on Jev.
- The 17 Jev-only items look like tool boilerplate and ordinary results. They look like false positives.
- The 2 baseline-only items are documentation notes that address the agent ("fetch the complete documentation index"). Jev scored them 0.20 and 0.28.

Jev at 0.5 flags about 14 percent of real fetched text, and the flagged items found here are not attacks. As a gate, it would block normal work. The data holds no known attack, so recall is unmeasured.

## What this means for the design

- Jev stays optional (section 2 of the Jev workflow note). Nothing depends on it.
- Do not use a Jev confidence as a gate parameter. A gate is a stop condition, not a target (ADR-0012). A retuned threshold is a defect (ADR-0018).
- If a Jev pick is ever used, it is a proposal to a model or a person, never a fact.
- The next measurement needs more labeled items. It needs a label source better than "a skill ran after this request". A labeled set with known attacks is needed for the screen.

## Unverified

- Jev spend. The billing rate is unverified.
- The S2 labels. A skill that ran is not always the right skill.
- Whether the 80+ result holds on new data. Only 95 labeled items back it.
- Recall of the screen. No item had a known attack.
