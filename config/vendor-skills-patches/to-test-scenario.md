## This repository writes no wave label (#1965, #2132)

This section overrides three upstream statements, and it is the only rule for a label: the statement that a case record
carries `wave:NN`, the statement that the parent carries `wave:01`, and the statement that a setup step puts the cases of
its batch one wave later. A case record carries `Test-Case` and no wave label. The scenario parent carries
`ready-for-agent` and no wave label. No area, container, record or run issue carries one either.

The order of a case comes from its `## Blocked by` edge, and the sub-issue link is native. The module writes no wave
label, so no label of that shape reaches GitHub. The vendored body keeps the upstream words for fidelity, and this patch
is the rule that stands: a vendor re-sync appends this section again, so the rule never drifts.
