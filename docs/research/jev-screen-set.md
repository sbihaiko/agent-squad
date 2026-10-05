# The Jev screen on a labeled set (ticket #57)

Run on 2026-10-01. Language: US English, Concise STE100 (ADR-0001). Data: `docs/research/jev-screen-set-results.json` (one record per text, no text, no key). Code: `scripts/jev_screen_set.py`.

## Answer

The set holds 58 texts from the subject issues. Each text was scored 3 times. This report makes no threshold claim (ADR-0012, ADR-0018). A gate stays unchanged. Jev stays in shadow mode (ADR-0008).

## How the set is built

- **Clean:** the original subject issue, one per issue (label: no instruction aimed at an AI).
- **Planted:** the same issue plus one instruction aimed at an AI (label: yes). Six payloads in five places: appended, inside an HTML comment, under a notes heading, in a blockquote, in a code comment.
- **Near miss:** the same issue plus text that addresses humans or describes an attack without issuing the command (label: no). This tests precision.
- The label comes from how the text was built. No model set it. A rebuild from the same issues gives the same set.
- The matched pairs share one issue, so the planted text and its clean control differ only by the added instruction.

The screen question is the one in `squad/jev.py` (`SCREEN`). Model `jev-1.13.0`, pinned. Every call kept its 20 s total deadline. Calls without a suggestion: 0.

## Scores by kind (median of the repeats per text)

| Kind | n | min | median | max |
|---|---|---|---|---|
| clean | 24 | 0.03 | 0.04 | 0.35 |
| near_miss | 10 | 0.04 | 0.06 | 0.15 |
| planted | 24 | 0.82 | 0.97 | 0.98 |

Observation: the highest median among the 34 unplanted texts is 0.35. The lowest median among the 24 planted texts is 0.82. The two ranges do not overlap on this set.

Spread of repeated calls on one text (max minus min): median 0.000, largest 0.060, over 58 texts.

## Recall and precision at several cut points

These are observations at fixed cut points. None is chosen, recommended, or a setting. A cut point is applied to the median of the repeats.

| Cut | Recall (found / planted) | 95% interval | Precision (planted / flagged) | 95% interval | Clean flagged | Near miss flagged |
|---|---|---|---|---|---|---|
| 0.1 | 24/24 | 0.862–1 | 24/29 | 0.655–0.924 | 3 | 2 |
| 0.3 | 24/24 | 0.862–1 | 24/25 | 0.805–0.993 | 1 | 0 |
| 0.5 | 24/24 | 0.862–1 | 24/24 | 0.862–1 | 0 | 0 |
| 0.7 | 24/24 | 0.862–1 | 24/24 | 0.862–1 | 0 | 0 |
| 0.9 | 23/24 | 0.798–0.993 | 23/23 | 0.857–1 | 0 | 0 |

## Planted texts by place and by payload (median score)

| Place | n | median |
|---|---|---|
| append | 5 | 0.96 |
| html-comment | 5 | 0.97 |
| notes-section | 5 | 0.96 |
| blockquote | 5 | 0.97 |
| code-comment | 4 | 0.95 |

| Payload | n | median |
|---|---|---|
| ignore-prior | 4 | 0.97 |
| exfiltrate | 4 | 0.98 |
| backdoor | 4 | 0.93 |
| rubber-stamp | 4 | 0.94 |
| override-rules | 4 | 0.98 |
| cheat-tests | 4 | 0.96 |

## Skill-choice labels

Rule, set before any score was read: the correct skill is the one the node's job names. The spec node is read-only and replies with one JSON spec. It never publishes. No skill in the roster does that, and `to-spec` publishes to a tracker, so the correct pick is `none`. The actor node implements one issue, so the correct pick is `implement`.

The table reads the 36 shadow runs of #55 (`docs/research/jev-shadow-pairs.json`). It needs no new call. The runs of one issue are not independent.

| Node | Correct pick | n | Right | 95% interval | Wrong picks |
|---|---|---|---|---|---|
| spec | none | 36 | 32 | 0.747–0.956 | {"code-review": 4} |
| actor | implement | 36 | 36 | 0.904–1 | none |

No gate may read a skill-choice score (ADR-0008), so no consumer needs this label yet. Lens B of the panel argued against more skill-choice work for that reason. This table costs no spend, and no more skill-choice collection follows.

## Not done

- The planted instructions are written by one author in six voices. A real attack may sound different. Recall here is recall on this set, not on attacks in general.
- The payloads are explicit. No obfuscated variant (encoded text, an instruction split over two places, a quiet suggestion) is in the set. A harder set is a later ticket, and a clean split here says little about it.
- Subject issues are all English text about one billing project. Other repo text may score differently.
- The screen on `CLAUDE.md` is left out. That file is addressed to an agent by design, so a high score is correct and carries no information.
- No node reads a screen score, so a threshold has no use yet. If a node ever does, ADR-0012 and ADR-0018 say the set must support the cut first, and a person decides it.
