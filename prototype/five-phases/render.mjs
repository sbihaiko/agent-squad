// PROTOTYPE. Run: node prototype/five-phases/render.mjs
// Validates graph.json, walks every scenario, writes five-phases.mmd and index.html.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validate, walk, toMermaid } from './graph-logic.mjs';
import { SCENARIOS, BASE_FACTS, check } from './scenarios.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const graph = JSON.parse(readFileSync(join(here, 'graph.json'), 'utf8'));

const errors = validate(graph);
console.log(`validate: ${errors.length} finding(s)`);
for (const e of errors) console.log('  - ' + e);

let bad = 0;
for (const s of SCENARIOS) {
  const r = walk(graph, { ...BASE_FACTS, ...s.facts }, s.script);
  const counts = {};
  for (const e of r.events) { const m = /count (\d+) of/.exec(e.note || ''); if (m) counts[e.node] = Number(m[1]); }
  const problems = check(r, s.expect, counts);
  bad += problems.length ? 1 : 0;
  console.log(`${problems.length ? 'FAIL' : 'ok  '} ${s.id}: outcome ${r.outcome}; ran ${r.ran.length}, skipped ${r.skipped.join(',') || '-'}${problems.length ? '\n     ' + problems.join('; ') : ''}`);
}

writeFileSync(join(here, 'five-phases.mmd'), toMermaid(graph) + '\n');

const logic = readFileSync(join(here, 'graph-logic.mjs'), 'utf8').replace(/^export /gm, '');
const scen = readFileSync(join(here, 'scenarios.mjs'), 'utf8').replace(/^export /gm, '');
const html = readFileSync(join(here, 'index.template.html'), 'utf8')
  .replace('/*GRAPH*/', JSON.stringify(graph, null, 1))
  .replace('/*LOGIC*/', logic)
  .replace('/*SCENARIOS*/', scen);
writeFileSync(join(here, 'index.html'), html);
console.log(`wrote five-phases.mmd and index.html; ${bad} scenario(s) off expectation`);
process.exit(bad ? 1 : 0);
