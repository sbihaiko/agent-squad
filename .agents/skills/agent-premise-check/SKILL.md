---
name: agent-premise-check
description: "Agent-invocable door to this repository's own /premise-check skill. Run one round of a premise check on a published spec: prove what reading proves, send what only running proves to a probe, ask the owner at most five questions."
---

# agent-premise-check

`/premise-check` is this repository's own skill, not a borrowed one, so the door names the file in the repository
rather than a skill found by name (ADR-0129 covers the borrowed ones).

1. Read `.claude/skills/premise-check/SKILL.md` from the repository root and follow it exactly, as if the Owner had
   typed `/premise-check`, with the arguments you were given. It is in the checkout under your feet; no name lookup is
   needed, and nothing under `~` is read.
2. The skill names two forms of one command: `uv run squad premise-check <spec> --findings findings.json --restatement
   restatement.json --reviewer <model>` for a plain round, and the same command with `--answers answers.json` when the
   owner answered the last round. The reviewer has no conversation history: it writes the `Restated goal:` lines from
   the spec body and the repository alone, before any gap. Use the form the skill's step 6 names for the round you are
   in; do not reimplement a rule in the chat.
3. Where a question would reach the Owner and the Owner is away, use the panel skill (`autonomy:panel`, from the
   `autonomy` plugin), as `/autonomy` says. Only the owner answers a decision a round asks; the panel stands in for
   the owner, and its answer is recorded in `answered_by`. Name that proxy on the command with `--proxy <registry model
   or panel>`, and it records as `proxy: <model id>`; the skill's rules on `--proxy`, `confident`, `owner_only` and
   `breakdown` apply.
