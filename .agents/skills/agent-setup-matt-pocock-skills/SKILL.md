---
name: agent-setup-matt-pocock-skills
description: "Agent-invocable door to the global /setup-matt-pocock-skills skill, in this repo only. Sets up the issue tracker, triage labels and domain docs a project's skills need."
---

# agent-setup-matt-pocock-skills

The Owner lets agents run `/setup-matt-pocock-skills` in this repo. The global skill may set `disable-model-invocation`, and a personal skill wins over a project skill of the same name, so this door has its own name.

1. Find the skill by name: run `python3 -m squad.skillpath setup-matt-pocock-skills` from the repository root. It prints the path of `SKILL.md`: the personal copy first, else the trusted plugin's current install. Read that file. Its base directory is the folder it sits in; read its other files from there when it points at them.
   - Exit 1 means the skill was removed or renamed upstream. Do not improvise its steps. Stop that skill, open an issue that names it, and carry on with other work.
2. Follow it exactly, as if the Owner had typed `/setup-matt-pocock-skills`, with the arguments you were given.
3. Where it would interview the Owner and the Owner is away, use the panel skill (`autonomy:panel`, from the `autonomy` plugin), as `/autonomy` says.
