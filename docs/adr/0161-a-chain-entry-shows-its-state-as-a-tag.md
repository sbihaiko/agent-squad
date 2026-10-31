# ADR-0161: A chain entry shows its live state as a tag, never by disappearing

## Status

Accepted. Owner instruction: configuration and state come from one source, a state change shows in the configuration
panel without removing a parameter, and both stay visible and editable.

## Context

A chain entry has two facts. The configuration says where the model stands: its rank, its route, its effort, its
hours, whether it is switched on. The state says what happened to it in the last hours: a timeout streak that took
it out of the chains, a spent quota, a provider window.

The two live in different files and were read by different code. `models.json` holds the configuration and the
chain panel renders it. `review-timeouts.json` holds a timeout streak, and `review.sh` writes and reads it. The
panel's state reader (`chainhealth.chain_state`) never opened that file: it read the provider marker, the quota
guard and the hours window, so an omitted model still sat in the list, in its old rank, with nothing to say why the
loop skipped it. An operator moving a model to reviewer read the panel as truth and could not see that the loop was
passing over it.

Two sources, two truths, and the one the operator reads is the wrong one.

## Decision

One read assembles a chain entry from its configuration and its state, and the panel renders both.

- The state is a tag on the entry, never a removal. A model that is omitted, out of quota or outside its hours keeps
  its rank, its effort, its hours and its switch, and the tag names the cause and the end of the window.
- The tag is read from the same file the loop writes, through that file's one writer, so the panel and the loop can
  never disagree about a model's state.
- The state stays editable where it is shown: clearing a tag calls the same door the recovery uses
  ([ADR-0160](0160-a-model-omitted-is-investigated-and-recovered.md)) and writes the state file through its one
  writer, never by editing the file by hand.
- A configuration change never erases a state, and a state change never rewrites the configuration: the two are
  joined at read time and written at their own doors.

## Consequences

- The panel answers "why is this model not used" on the row itself, with its cause and its end.
- An omitted model stays visible and keeps its place, so restoring it costs one press and no re-ranking.
- A reader that adds a new state source adds it to the one read, so no source can quietly become invisible again.

## Guard

`tests/test_chain_health.py::test_a_timeout_omission_shows_on_its_entry_and_the_configuration_is_untouched`: an
omitted model appears in the chain state with its reason and its end, its configuration list is unchanged, and a
streak whose window has ended is no reason at all.
