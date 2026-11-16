#!/usr/bin/env python3
"""Archive the terminal cards of closed issues once they are old enough, one paced write at a time (#1931)."""
import datetime as dt
import os
import time

TERMINAL_COLUMNS = ("Closed", "Abandoned")
ARCHIVE_QUERY = "mutation($item: ID!) { archiveProjectV2Item(input: {itemId: $item}) { item { id } } }"


class RateLimited(RuntimeError):
    """The carrier answered a rate limit: the pass stops and names it."""


def after_days(env=os.environ):
    return int(env.get("SG_ARCHIVE_AFTER_DAYS", "7"))


def pace_seconds(env=os.environ):
    return float(env.get("SG_ARCHIVE_PACE_SECONDS", "1"))


def _closed_at(stamp):
    return dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


def select(cards, *, repo, now, after_days, protected):
    cutoff = now - dt.timedelta(days=after_days)
    return [c for c in cards
            if c["repo"] == repo and c["column"] in TERMINAL_COLUMNS
            and c["state"] == "closed" and _closed_at(c["closed_at"]) < cutoff and c["issue"] not in protected]


def item_nodes(binding):
    out = binding.api("--paginate", f"{binding.base}/items?per_page=100", "--jq",
                      '.[]|select(.content.number and .archived_at == null)|[.content.number,.node_id,(.content.repository_url//"")]|@tsv')
    nodes = {}
    for line in out.splitlines():
        n, node, url = (line.split("\t") + ["", "", ""])[:3]
        nodes[("/".join(url.split("/")[-2:]), int(n))] = node
    return nodes


def archive_item(node_id, api):
    """Archive one card through the board binding's call site (`api`, issue_state.BoardBinding.api), never a `gh` of
    its own (ADR-0277): the binding logs each call and retries a server error."""
    try:
        api("graphql", "-f", f"query={ARCHIVE_QUERY}", "-f", f"item={node_id}")
    except RuntimeError as e:
        text = str(e).strip()
        if "rate limit" in text.lower() or "RATE_LIMITED" in text or "submitted too quickly" in text: raise RateLimited(text[:200])
        raise RuntimeError(text[:200])


def archive(cards, *, write, pace, sleep=time.sleep, say=print):
    done = []
    for k, c in enumerate(cards):
        if k and pace: sleep(pace)
        try: write(c["item"])
        except RateLimited as e:
            say(f"ARCHIVE STOPPED: {e}; {len(done)} archived, {len(cards) - len(done)} left for the next pass")
            break
        except RuntimeError as e:
            say(f"ARCHIVE REFUSED #{c['issue']}: {e}"); continue
        done.append(c["issue"]); say(f"ARCHIVED #{c['issue']}")
    return done
