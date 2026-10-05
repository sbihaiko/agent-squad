// PROTOTYPE. Pure module: no DOM, no I/O. Validates a graph, walks it under
// given facts and scripted outcomes, and renders Mermaid.
//
// Walk rules this prototype assumes (each one is a finding for the schema):
//  R1 Control is order-based. A loop runs its children in order. The root is a loop.
//  R2 `when` is read when a child is reached in order. False: the child is skipped.
//  R3 `next.<outcome>` names a later sibling, or a reserved word. A jump to a
//     sibling enters it even when its `when` is false.
//  R4 Reserved words: `repeat` (loop again from the first child), `continue`
//     (treat as pass), `exit:pass`, `exit:fail` (leave the enclosing loop now).
//  R5 Defaults: pass -> next sibling; fail -> repeat; deadline and needs-human
//     -> the loop ends with that outcome.
//  R6 A loop passes when its last child is done. It fails when a repeat would
//     exceed `max.value`.

export const KINDS = ['agent', 'gate', 'wait', 'loop'];
export const OUTCOMES = ['pass', 'fail', 'deadline', 'needs-human'];
const OPS = ['eq', 'ne', 'gt', 'lt'];
const RESERVED = ['repeat', 'continue', 'exit:pass', 'exit:fail'];

export function byName(graph) {
  const m = new Map();
  for (const n of graph.nodes) m.set(n.name, n);
  return m;
}

export function validate(graph) {
  const errors = [];
  const nodes = byName(graph);
  const factNames = new Set((graph.facts || []).map(f => f.name));
  const gateIds = new Set((graph.gates || []).map(g => g.id));
  if (graph.schema_version === undefined) errors.push('graph: schema_version missing');
  if (!nodes.has(graph.root)) errors.push(`graph: root "${graph.root}" is not a node`);
  else if (nodes.get(graph.root).kind !== 'loop') errors.push('graph: root must be a loop (R1)');
  if (nodes.size !== graph.nodes.length) errors.push('graph: node names are not unique');

  const parentOf = new Map();
  for (const n of graph.nodes) {
    if (!KINDS.includes(n.kind)) errors.push(`${n.name}: kind "${n.kind}" is not one of ${KINDS.join(', ')}`);
    if (n.kind === 'gate') {
      if (n.when) errors.push(`${n.name}: a gate never carries a when (ADR-0100)`);
      if (!gateIds.has(n.check)) errors.push(`${n.name}: check "${n.check}" is not a registered gate`);
    }
    if (n.kind === 'agent') {
      for (const k of ['model', 'tools', 'budget', 'deadline']) if (n[k] === undefined) errors.push(`${n.name}: agent needs ${k}`);
      if (n.budget && (!n.budget.turns || !n.budget.spend)) errors.push(`${n.name}: budget needs turns and spend`);
    }
    if (n.kind === 'wait') {
      for (const k of ['owner', 'deadline', 'default']) if (n[k] === undefined) errors.push(`${n.name}: wait needs ${k}`);
    }
    if (n.kind === 'loop') {
      if (!Array.isArray(n.children) || n.children.length === 0) errors.push(`${n.name}: loop needs children`);
      if (!n.max || typeof n.max.value !== 'number') errors.push(`${n.name}: loop needs max.value`);
      for (const c of n.children || []) {
        if (!nodes.has(c)) errors.push(`${n.name}: child "${c}" is not a node`);
        else if (parentOf.has(c)) errors.push(`${c}: listed as a child of two loops`);
        else parentOf.set(c, n.name);
      }
    }
    if (n.when) {
      const w = n.when;
      const factOk = factNames.has(w.fact) || (typeof w.fact === 'string' && w.fact.startsWith('outcome:') && nodes.has(w.fact.slice(8)));
      if (!factOk) errors.push(`${n.name}: when.fact "${w.fact}" is not a declared fact`);
      if (!OPS.includes(w.op)) errors.push(`${n.name}: when.op "${w.op}" is not one of ${OPS.join(', ')}`);
    }
    for (const key of ['_GAP']) if (n[key]) errors.push(`${n.name}: GAP ${n[key]}`);
  }
  for (const n of graph.nodes) {
    if (n.name !== graph.root && !parentOf.has(n.name)) errors.push(`${n.name}: reachable from no loop`);
    for (const [oc, target] of Object.entries(n.next || {})) {
      if (!OUTCOMES.includes(oc)) errors.push(`${n.name}: next.${oc} is not an outcome`);
      if (RESERVED.includes(target)) continue;
      const p = parentOf.get(n.name);
      const sibs = p ? nodes.get(p).children : [];
      const i = sibs.indexOf(n.name), j = sibs.indexOf(target);
      if (j < 0) errors.push(`${n.name}: next.${oc} "${target}" is not a sibling (R3)`);
      else if (j <= i) errors.push(`${n.name}: next.${oc} "${target}" is not a later sibling (R3)`);
    }
  }
  return errors;
}

function holds(when, facts, lastOutcome) {
  const v = when.fact.startsWith('outcome:') ? lastOutcome.get(when.fact.slice(8)) : facts[when.fact];
  switch (when.op) {
    case 'eq': return v === when.value;
    case 'ne': return v !== when.value;
    case 'gt': return v > when.value;
    case 'lt': return v < when.value;
  }
  return false;
}

// script: { nodeName: [outcome, outcome, ...] } consumed in order; default pass.
export function walk(graph, facts, script = {}) {
  const nodes = byName(graph);
  const queue = Object.fromEntries(Object.entries(script).map(([k, v]) => [k, [...v]]));
  const events = [];
  const lastOutcome = new Map();
  const counts = {};

  const scripted = (n) => (queue[n.name] && queue[n.name].length ? queue[n.name].shift() : 'pass');

  function runLeaf(n, how) {
    let outcome = scripted(n);
    if (n.kind === 'wait' && outcome === 'deadline') {
      events.push({ node: n.name, kind: n.kind, how, note: `deadline expired; default "${n.default}" applied (ADR-0029 rule 7)` });
      outcome = n.default;
    }
    lastOutcome.set(n.name, outcome);
    events.push({ node: n.name, kind: n.kind, how, outcome });
    return outcome;
  }

  function runLoop(loop, how) {
    const times = loop.for_each ? Math.max(1, facts[loop.for_each] ?? 1) : 1;
    let outcome = 'pass';
    for (let item = 0; item < times; item++) {
      let count = 1;
      counts[loop.name] = count;
      events.push({ node: loop.name, kind: 'loop', how, note: `enter${loop.for_each ? ` (item ${item + 1} of ${times})` : ''}, count 1 of ${loop.max.value}` });
      let i = 0, forced = false;
      outcome = 'pass';
      while (i < loop.children.length) {
        const child = nodes.get(loop.children[i]);
        if (!forced && child.when && !holds(child.when, facts, lastOutcome)) {
          events.push({ node: child.name, kind: child.kind, how: 'order', skipped: true, why: `when ${child.when.fact} ${child.when.op} ${JSON.stringify(child.when.value)} is false` });
          i++; continue;
        }
        const entry = forced ? 'jump' : 'order';
        forced = false;
        const oc = child.kind === 'loop' ? runLoop(child, entry) : runLeaf(child, entry);
        lastOutcome.set(child.name, oc);
        const target = (child.next || {})[oc];
        let action;
        if (target === undefined) action = oc === 'pass' ? 'continue' : oc === 'fail' ? 'repeat' : 'exit:' + oc;
        else action = target;
        if (action === 'continue') { i++; continue; }
        if (action === 'repeat') {
          if (count >= loop.max.value) {
            events.push({ node: loop.name, kind: 'loop', note: `max ${loop.max.value} reached; loop fails (R6)` });
            outcome = 'fail'; break;
          }
          count++; counts[loop.name] = count;
          events.push({ node: loop.name, kind: 'loop', note: `repeat, count ${count} of ${loop.max.value}` });
          i = 0; continue;
        }
        if (action.startsWith('exit:')) { outcome = action.slice(5); events.push({ node: loop.name, kind: 'loop', note: `exit ${outcome}` }); break; }
        // jump to a later sibling, bypassing its when (R3)
        i = loop.children.indexOf(action); forced = true;
        if (i < 0) throw new Error(`${child.name}: next.${oc} "${action}" is not a sibling`);
        events.push({ node: loop.name, kind: 'loop', note: `${child.name} ${oc} -> jump to ${action} (when bypassed)` });
      }
      events.push({ node: loop.name, kind: 'loop', note: `leave with ${outcome}` });
      lastOutcome.set(loop.name, outcome);
      if (outcome !== 'pass') break;
    }
    return outcome;
  }

  const outcome = runLoop(nodes.get(graph.root), 'root');
  const ran = new Set(events.filter(e => e.outcome).map(e => e.node));
  const skipped = new Set(events.filter(e => e.skipped).map(e => e.node));
  return { outcome, events, ran: [...ran], skipped: [...skipped].filter(n => !ran.has(n)) };
}

export function toMermaid(graph, result) {
  const nodes = byName(graph);
  const ran = new Set(result ? result.ran : []);
  const skipped = new Set(result ? result.skipped : []);
  const out = ['flowchart TD'];
  const shape = (n) => n.kind === 'gate' ? `{{"${n.name}<br/>gate: ${n.check}"}}`
    : n.kind === 'wait' ? `(["${n.name}<br/>wait: ${n.owner}, ${n.deadline}s"])`
    : `["${n.name}<br/>${n.model}${n.when ? '<br/>when ' + n.when.fact + ' ' + n.when.op + ' ' + String(n.when.value) : ''}"]`;
  const edges = [];
  function emit(loop, depth) {
    const pad = '  '.repeat(depth);
    const title = `${loop.name} loop max ${loop.max.value}${loop.for_each ? ' for each ' + loop.for_each : ''}${loop.when ? ' when ' + loop.when.fact + ' ' + loop.when.op + ' ' + String(loop.when.value) : ''}`;
    out.push(`${pad}subgraph ${loop.name}["${title}"]`);
    out.push(`${pad}  direction TB`);
    let prev = null;
    for (const c of loop.children) {
      const n = nodes.get(c);
      if (n.kind === 'loop') emit(n, depth + 1); else out.push(`${pad}  ${n.name}${shape(n)}`);
      if (prev) edges.push(`${prev} --> ${c}`);
      prev = c;
      for (const [oc, t] of Object.entries(n.next || {})) {
        if (t === 'repeat') edges.push(`${c} -. ${oc}: repeat .-> ${loop.children[0]}`);
        else if (t === 'continue') edges.push(`${c} -. ${oc}: continue .-> ${c}`);
        else if (t.startsWith('exit:')) edges.push(`${c} -. ${oc}: ${t} .-> ${loop.name}_exit`);
        else edges.push(`${c} -. ${oc} .-> ${t}`);
      }
      if (n.kind !== 'loop' && !(n.next || {}).fail && n.kind !== 'wait') edges.push(`${c} -. fail: repeat .-> ${loop.children[0]}`);
    }
    if (loop.children.some(c => Object.values(nodes.get(c).next || {}).some(t => t.startsWith('exit:')))) out.push(`${pad}  ${loop.name}_exit((exit))`);
    out.push(`${pad}end`);
  }
  emit(nodes.get(graph.root), 0);
  out.push(...edges.map(e => '  ' + e));
  out.push('  classDef gate fill:#eee,stroke:#333,stroke-width:2px');
  out.push('  classDef wait fill:#fff4d6,stroke:#333');
  out.push('  classDef cond stroke-dasharray: 5 5');
  out.push('  classDef ran fill:#cfe8cf,stroke:#2a6');
  out.push('  classDef skipped fill:#f6f6f6,stroke:#bbb,color:#999');
  const cls = (name, c) => out.push(`  class ${name} ${c}`);
  for (const n of graph.nodes) {
    if (n.kind === 'loop') continue;
    if (n.kind === 'gate') cls(n.name, 'gate');
    if (n.kind === 'wait') cls(n.name, 'wait');
    if (n.when) cls(n.name, 'cond');
    if (ran.has(n.name)) cls(n.name, 'ran');
    else if (skipped.has(n.name)) cls(n.name, 'skipped');
  }
  return out.join('\n');
}
