# A dispatch's card write is an event, and no run starts while it is pending

A dispatch moves its cards on the board before it launches a run. Each move is an event in the issue ledger, recorded
pending before it is written, and it carries the instant it happened. The launch waits for the write to land. A write
that did not land stays pending until a later pass writes it, so a crash between the event and the write loses nothing.

The table is the guide for the autobug pass. The ledger holds one board fact per issue, the latest event, identified by
its run and column. An older instant never replaces a newer one, and the same event twice changes nothing.

| Actor | Event | Instant | Column | Failure behavior |
|---|---|---|---|---|
| autobug dispatch | the pass requests a run for its picks | the time the pending event is recorded, before any write | Doing | the event is not recorded: nothing is written or launched, the claim is freed |
| autobug dispatch | the card write of that event | the event's own instant, never the write's | Doing (board name from the vocabulary) | refused or unread: nothing launches, the claim is freed, the event stays pending, the refusal is named and filed once |
| autobug dispatch | the card write lands | the event's own instant | Doing | the event is marked written, then the run launches |
| autobug dispatch | launch.py refuses the request | a new Todo event, instant after the Doing one | Todo | the claim is freed and the cards go back to Todo through the same door; the Todo event stays pending until written |
| autobug resume | a pass finds a pending Doing event whose request file exists | the recorded instant | Doing | the card is written again (idempotent), then the stored request launches once; a refusal follows the launch-refusal row |
| autobug resume | a pass finds a pending Doing event whose request file is gone | the recorded instant | Todo | a Todo event is recorded and written; nothing launches |
| autobug resume | a pass finds a pending Todo event | the recorded instant | Todo | the card is written again; nothing launches |
| any writer | an event older than the fact the ledger holds | the older instant | any | stale: nothing changes, the newer fact stands |
| any writer | the same event again (same run and column) | the same instant | any | duplicate: nothing changes |
| any writer | a write for an event the fact no longer holds | the older instant | any | superseded: nothing changes |

A dispatch never reads a pending issue as startable. The candidate pass skips an issue with a pending event, and the next
pass recovers it before it picks anything new.

A missing or corrupt ledger holds the issue (ADR-0149): the dispatch records nothing, writes nothing, and launches nothing.

Known limits, kept on purpose: the window between a card write that landed and its launch leaves a card in Doing with no
run until the janitor's rules read it; the bug board's write comes after the launch and is not gated; the frontier pass
does not yet read pending events.

Guard: `contrib/squad-goal/scripts/test-issue-ledger.sh`, `contrib/squad-goal/scripts/test-autobug.sh`.
