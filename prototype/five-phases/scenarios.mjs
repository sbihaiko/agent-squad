// PROTOTYPE. Guided walkthroughs. Each one sets facts and scripts node outcomes.
export const BASE_FACTS = { tier: 'large', sensitive: false, visual: false, git: true, work_items: 1, blocking_learnings: 0, architecture_issues: 0 };

export const SCENARIOS = [
  { id: 'trivial-clean', title: 'Trivial tier, clean change',
    watch: 'Spec, plan, and the critic are skipped by their when. The size backstop gate still runs.',
    facts: { tier: 'trivial' }, script: {},
    expect: { outcome: 'pass', skipped: ['spec', 'plan', 'critic'], ran: ['size_backstop', 'scope_gate', 'security_gate'] } },

  { id: 'trivial-backstop', title: 'Trivial tier, but the diff is too big',
    watch: 'The size backstop fails. Its fail edge jumps into the critic, whose when says trivial skips it. The jump wins (R3).',
    facts: { tier: 'trivial' }, script: { size_backstop: ['fail'] },
    expect: { outcome: 'pass', ran: ['critic'], skipped: ['spec', 'plan'] } },

  { id: 'medium-proxy-rejects', title: 'Medium tier, the spec proxy rejects once',
    watch: 'Spec runs and repeats once. Plan is skipped. The critic runs by order.',
    facts: { tier: 'medium' }, script: { spec_proxy: ['fail', 'pass'] },
    expect: { outcome: 'pass', ran: ['spec_draft', 'critic'], skipped: ['plan'], counts: { spec: 2 } } },

  { id: 'large-two-items', title: 'Large tier, two work items, scope gate fails once',
    watch: 'Execute runs the work loop once per work item (for_each, a GAP). A scope failure repeats the work loop without a critic call.',
    facts: { tier: 'large', work_items: 2 }, script: { scope_gate: ['fail'] },
    expect: { outcome: 'pass', ran: ['decompose', 'audit_c'] } },

  { id: 'merge-wait-expires', title: 'Merge fails, the owner never answers',
    watch: 'Merge fails into a wait node. Its deadline expires. The declared default (fail) applies and the run exits with fail.',
    facts: { tier: 'large' }, script: { merge: ['fail'], manual_merge: ['deadline'] },
    expect: { outcome: 'fail', ran: ['manual_merge'] } },

  { id: 'merge-wait-answered', title: 'Merge fails, the owner resolves it',
    watch: 'The wait node passes and the run continues to verify.',
    facts: { tier: 'large' }, script: { merge: ['fail'], manual_merge: ['pass'] },
    expect: { outcome: 'pass', ran: ['manual_merge', 'ac_verify'] } },

  { id: 'critic-never-approves', title: 'The critic never approves',
    watch: 'The work loop hits max 12 and fails. Execute fails, main fails. No verify.',
    facts: { tier: 'medium' }, script: { critic: Array(12).fill('fail') },
    expect: { outcome: 'fail', skipped: [], notRan: ['ac_verify'] } },

  { id: 'scout-deadline', title: 'Scout hits its deadline',
    watch: 'A deadline is its own outcome. The loop ends with deadline, not fail, and nothing repeats.',
    facts: { tier: 'large' }, script: { scout: ['deadline'] },
    expect: { outcome: 'deadline', notRan: ['spec_draft'] } },

  { id: 'visual-and-learnings', title: 'Web app change with a blocking learning',
    watch: 'The visual loop runs. The learning gate always runs (a gate has no when). Audit A runs because a learning blocks; its fail is advisory.',
    facts: { tier: 'medium', visual: true, blocking_learnings: 1 }, script: { audit_a: ['fail'] },
    expect: { outcome: 'pass', ran: ['visual_actor', 'learning_gate', 'audit_a'], skipped: ['audit_b', 'audit_c'] } },

  { id: 'no-git', title: 'No git in the target',
    watch: 'Commit is skipped by its when. dev_squad also drops the merge node and adds revert nodes here; this schema cannot say that (one knob, many effects).',
    facts: { tier: 'trivial', git: false }, script: {},
    expect: { outcome: 'pass', skipped: ['commit'] } },
];

export function check(result, expect, counts) {
  const problems = [];
  if (expect.outcome && result.outcome !== expect.outcome) problems.push(`outcome ${result.outcome}, expected ${expect.outcome}`);
  for (const n of expect.ran || []) if (!result.ran.includes(n)) problems.push(`${n} did not run`);
  for (const n of expect.skipped || []) if (!result.skipped.includes(n)) problems.push(`${n} was not skipped`);
  for (const n of expect.notRan || []) if (result.ran.includes(n)) problems.push(`${n} ran`);
  for (const [k, v] of Object.entries(expect.counts || {})) if (counts && counts[k] !== v) problems.push(`${k} count ${counts && counts[k]}, expected ${v}`);
  return problems;
}
