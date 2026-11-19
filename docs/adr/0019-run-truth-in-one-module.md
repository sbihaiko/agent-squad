# ADR-0019: Run truth lives in one module; the exit code is a pure function of one verdict

## Status

Accepted.

## Context

"Did this run succeed?" can have several answers that disagree. The agent's narration says one thing. The logs say another. The git remote and the stored outputs say a third. A hand-written state file can carry false claims while a generated one beside it is correct. A generator that reads a local mirror instead of the source can be wrong too.

Wrappers hide more. A model call can exit zero and still not do the work. The model can answer without acting. The session can resume into the wrong state. An agent report such as "done" can be false and can be disproved from outside.

## Decision

1. **One module owns run truth.** In code, that module is a script, not prose in a document. Its verdict answers one question: continue, needs a human, failed, or the bug did not reproduce. Every non-continue verdict carries a **list** of reasons, never one collapsed reason. A run that reports the end class `does-not-reproduce` and made no commit is its own verdict, never a failure: the code is sound and the premise of its issue is wrong (ADR-0029).
2. **The wrapper's exit code is a pure function of that verdict.** Use zero for continue. Use one dedicated code for needs-human, one for failed, and one for no-repro. No step invents its own code. No step discards the code of the step it called.
3. **A zero exit from a model call is not success.** The wrapper reads the artifact the run had to produce, such as the output file, the commit, or the report. It derives the verdict from that artifact.
4. **State the precedence once.** The artifact is authoritative. The exit code is diagnostic. It is the fallback only for a step that wrote nothing. The agent's own prose is never evidence. An agent saying "done" is a claim to check, not a record.
5. **Healing takes machine evidence only, within the same lineage.** A stale failure is healed when a later record of the same project, request, and retry lineage is clean. A success from a different request never erases an earlier failure.
6. **Every override is scoped and carries a recorded reason.** There is no blanket force flag.
7. **Surface by-design pauses. They never block.** Waiting on Rafael is a named state keyed by the step. A reader can then tell "never ran" from "failed".
8. **Generate derived truth. Never write it by hand.** The generator reads the source, not a local mirror or a cache. Reconstruction is read-only. The script must give a correct verdict for an old run with none of today's artifacts present.

## Consequences

One read in one place answers "did this run succeed?". A class of silent passes becomes a real failure with a distinct exit code. A needs-human verdict is no longer mistaken for success.

Cost: the artifact shape becomes load-bearing. Every reader must tolerate a missing new key. Each new kind of step must be folded into the verdict by name, not guessed.

## Guard

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
