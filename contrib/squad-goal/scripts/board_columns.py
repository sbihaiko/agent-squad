"""The board vocabulary, independent of the hub and GitHub clients."""

COLUMNS = (
    {"name": "Open PRs", "fact": "pr_open", "actor": "reviewer", "terminal": False, "order": 8, "flow": 0, "color": "#5aa9ff"},
    {"name": "Closed PRs", "fact": "pr_closed", "actor": "none", "terminal": True, "order": 9, "flow": 1, "color": "#8b9bb3"},
    {"name": "Open Specs", "fact": "spec_open", "actor": "owner", "terminal": False, "order": 4, "flow": 2, "color": "#f78fb3"},
    {"name": "No status", "fact": "missing_status", "actor": "owner", "terminal": False, "order": 6, "flow": 3, "color": "#f5b041"},
    {"name": "Blocked", "fact": "blocked", "actor": "owner", "terminal": False, "order": 5, "flow": 4, "color": "#ff6b6b"},
    {"name": "Todo", "fact": "unclaimed", "actor": "dispatch", "terminal": False, "order": 3, "flow": 5, "color": "#c9d1d9"},
    {"name": "In Progress", "fact": "claimed", "actor": "agent", "terminal": False, "order": 0, "flow": 6, "color": "#e8d44d"},
    {"name": "Code Complete", "fact": "open_pr", "actor": "tester", "terminal": False, "order": 1, "flow": 7, "color": "#b087f9"},
    {"name": "Testing", "fact": "merged", "actor": "tester", "terminal": False, "order": 2, "flow": 8, "color": "#2dd4bf"},
    {"name": "Done", "fact": "issue_closed", "actor": "none", "terminal": True, "order": 7, "flow": 9, "color": "#3ecf8e"},
)

BY_FACT = {column["fact"]: column["name"] for column in COLUMNS}
