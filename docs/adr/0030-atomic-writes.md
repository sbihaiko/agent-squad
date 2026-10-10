# ADR-0030: A write that can be interrupted is written atomically

## Status

Accepted.

## Context

Idempotence answers the question "what if this runs twice". It does not answer "what if this dies halfway through one run".

This project writes files that people and scripts read as truth. Examples are generated state files, run records, and workflow configuration. Processes die. A host can crash or restart at any time.

A generator that redirects output into the destination path truncates that path first. A kill between truncation and the last byte leaves a partial file or an empty file. A re-run does not restore the previous content. Every reader in between sees a file that is confidently wrong.

This risk is invisible in normal use. Over enough runs it is certain.

## Decision

1. **Write each file change to a temporary file, then rename.** Put the temporary file in the destination directory. A rename within one filesystem is atomic. A reader sees the old file or the new file, never a partial one.
2. **Form the full content before you touch the destination.** If the generator cannot reach its source, it fails and leaves the destination unchanged. A half file is worse than an old file. An old file is visibly old.
3. **Run each database mutation in one transaction.** A kill then leaves the store in its previous consistent state. An item is written or not written. It is never half written.
4. **An interruption between two steps is safe by construction.** Each step is atomic on its own. No cross-step transaction is needed. Read state from the target to cover the sequence.
5. **Tests kill the generator mid-run.** At least one test interrupts a write. It asserts that the destination is the original or the finished result, and nothing else. An untested atomicity claim is a comment.

## Consequences

A reboot cannot leave a reader with a truncated file. A crash cannot lose or duplicate a queued item.

The generator contract is simple. It either produced a complete file or changed nothing.

Cost: one temporary file per write, and some care about where it lands. Filesystems without atomic rename are out of scope. This is not a real limit on macOS and Linux.

## Guard

`tests/test_record.py`.
