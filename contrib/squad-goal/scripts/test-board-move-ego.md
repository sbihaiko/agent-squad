# Runbook: the board move to Todo (ego-browser e2e, #1300)

This runbook is the planned e2e for issue #1300. Run it in ego-browser against a hub on a throwaway
profile and its own port. It never touches the owner's default browser profile or the loop's state
directory.

The move under test: a Backlog card, then the arrow `→ Todo`. The page must draw the card in Todo at
once with a `pending` mark. The writer confirms in the background. The page clears the mark when the
writer answers. A refusal clears the mark and shows the reason on the card.

## Before you start

1. Use a test GitHub Project, never the live board. Steps 1 and 2 move one real card on that project.
   A move on the live board is a consequential write and needs the owner's grant (ADR-0024).
2. Set these placeholders for the test project: `<test-owner>`, `<test-repo>` and `<test-project-number>`.
3. Pick a free port, for example 7711. Pick an empty directory for the profile, `$P`.
4. Start the hub with a throwaway profile:

   ```bash
   mkdir -p "$P/home" "$P/state" "$P/hub"
   HOME="$P/home" SG_STATE="$P/state" SG_HUB_DIR="$P/hub" SG_REPO=<test-owner>/<test-repo> \
     SG_OWNER=<test-owner> SG_PROJECT=<test-project-number> python3 contrib/squad-goal/scripts/hub.py 7711
   ```

5. Open `http://127.0.0.1:7711/` in ego-browser. Open the board view.
6. Find one card in the Backlog column that carries the `→ Todo` arrow. Note its issue number `N`.

## Step 1: the move is drawn at once

1. Click `→ Todo` on card `N`.
2. Before the writer answers, read the card. Assert all of these:
   - The card is in the Todo column.
   - The card carries the `pending` tag.
   - The card no longer carries the `→ Todo` arrow.
3. Take a screenshot for the run record.

The hub answers the click at once (HTTP 200 with `ok` and `pending`). The page does not wait for the
writer.

## Step 2: the pending mark clears on a proved move

1. Wait up to 30 seconds. The board polls every 5 seconds.
2. Assert all of these:
   - The card is still in the Todo column.
   - The `pending` tag is gone.
   - The page did not reload. Note the page's load time in ego-browser before and after.

## Step 3: a refusal shows its reason

1. Stop the hub from Step 1. Start it again on the same port and with the same state directory, and
   with a stub `gh` first on the path. The stub refuses every PATCH and passes every other call to the
   real `gh`:

   ```bash
   REAL_GH=$(command -v gh)
   mkdir -p "$P/stub"
   cat > "$P/stub/gh" <<EOF
   #!/bin/bash
   case "\$*" in *"-X PATCH"*) echo "forced refusal for the e2e" >&2; exit 1;; esac
   exec "$REAL_GH" "\$@"
   EOF
   chmod +x "$P/stub/gh"
   PATH="$P/stub:$PATH" HOME="$P/home" SG_STATE="$P/state" SG_HUB_DIR="$P/hub" SG_REPO=<test-owner>/<test-repo> \
     SG_OWNER=<test-owner> SG_PROJECT=<test-project-number> python3 contrib/squad-goal/scripts/hub.py 7711
   ```

2. Reload the board in ego-browser.
3. Click `→ Todo` on a different Backlog card `M`.
4. Wait up to 30 seconds.
5. Assert all of these:
   - Card `M` is still in the Backlog column.
   - The `pending` tag is gone from card `M`.
   - The card shows the writer's reason, which names `forced refusal for the e2e`.
   - The page never shows the text `the writer did not answer in 120 s`.

## Step 4: the cleanup

1. Stop the hub. Return any test card to its column. Remove the throwaway directory: `rm -rf "$P"`.
2. Record the result of each step as pass or fail. Attach the screenshots.

## Pass rule

The run passes when Steps 1 to 3 all pass. Any failed assertion fails the run. A failed assertion is a
defect in the hub: fix it in `contrib/squad-goal/scripts/hub.py`, never by changing these steps.
