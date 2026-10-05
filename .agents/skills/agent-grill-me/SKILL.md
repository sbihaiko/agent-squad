---
name: agent-grill-me
description: "Agent-invocable door to the global /grill-me skill, in this repo only. A relentless interview to sharpen a plan or design."
---

# agent-grill-me

The Owner lets agents run `/grill-me` in this repo (2026-09-30). The global skill sets `disable-model-invocation`, and a personal skill wins over a project skill of the same name, so this door has its own name.

1. Find the skill by name: run `python3 -m squad.skillpath grill-me` from the repository root. It prints the path of `SKILL.md`: the personal copy first, else the trusted plugin's current install (ADR-0129). Read that file. Its base directory is the folder it sits in; read its other files from there when it points at them.
   - Exit 1 means the skill was removed or renamed upstream. Do not improvise its steps. Stop that skill, open an issue that names it, and carry on with other work.
2. Follow it exactly, as if the Owner had typed `/grill-me`, with the arguments you were given.
3. Where it would interview the Owner and the Owner is away, use the panel skill (`autonomy:panel`, from the `autonomy` plugin), as `/autonomy` says.
