// Dashboard core module: the JSON Schema check and the engine rules (the port that tests/test_dashboard.py compares with squad.graph.validate) (ticket #117). A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function schemaModule({ ENGINE, isObj, canonText, same, pyFloat }) {
  const SCHEMA = ENGINE.schema;
  const KINDS = ENGINE.kinds;
  const BUILTIN = new Set(ENGINE.builtin_facts);
  const SEES_FORBIDDEN = new Set(ENGINE.sees_forbidden);
  const WRITE_TOOLS = new Set(ENGINE.write_tools);
  const FORBIDDEN_NAME = new RegExp(ENGINE.forbidden_name);
  const FORBIDDEN_INSTRUMENT = new RegExp(ENGINE.forbidden_instrument, "i");
  const GATES = ENGINE.gate_registry;

  // ---- JSON Schema (the keywords the graph schema uses) ----
  const typeOk = (v, t) => t === "object" ? isObj(v) : t === "array" ? Array.isArray(v) : t === "string" ? typeof v === "string"
    : t === "boolean" ? typeof v === "boolean" : t === "null" ? v === null : t === "integer" ? typeof v === "number" && Number.isInteger(v)
    : t === "number" ? typeof v === "number" : false;

  function resolve(ref, defs) {
    const m = /^#\/\$defs\/(.+)$/.exec(ref);
    if (!m || !defs[m[1]]) throw new Error("unresolved $ref " + ref);
    return defs[m[1]];
  }

  function check(v, s, defs, path) {
    const out = [];
    if (s.$ref) return check(v, resolve(s.$ref, defs), defs, path);
    const at = path.join("/") || "<root>";
    if (s.type !== undefined) {
      const ts = Array.isArray(s.type) ? s.type : [s.type];
      if (!ts.some((t) => typeOk(v, t))) return [{ path: at, message: `${JSON.stringify(v)} is not of type ${ts.join(", ")}` }];
    }
    if (s.const !== undefined && !same(v, s.const)) out.push({ path: at, message: `${JSON.stringify(s.const)} was expected` });
    if (s.enum && !s.enum.some((e) => same(v, e))) out.push({ path: at, message: `${JSON.stringify(v)} is not one of ${JSON.stringify(s.enum)}` });
    if (typeof v === "number") {
      if (s.minimum !== undefined && v < s.minimum) out.push({ path: at, message: `${v} is less than the minimum of ${s.minimum}` });
      if (s.exclusiveMinimum !== undefined && v <= s.exclusiveMinimum) out.push({ path: at, message: `${v} is not greater than ${s.exclusiveMinimum}` });
    }
    if (typeof v === "string") {
      if (s.minLength !== undefined && [...v].length < s.minLength) out.push({ path: at, message: `${JSON.stringify(v)} is too short` });
      if (s.pattern !== undefined && !new RegExp(s.pattern).test(v)) out.push({ path: at, message: `${JSON.stringify(v)} does not match ${JSON.stringify(s.pattern)}` });
    }
    if (Array.isArray(v)) {
      if (s.minItems !== undefined && v.length < s.minItems) out.push({ path: at, message: `${JSON.stringify(v)} is too short` });
      if (s.uniqueItems && new Set(v.map(canonText)).size !== v.length) out.push({ path: at, message: `${JSON.stringify(v)} has non-unique elements` });
      if (s.items) v.forEach((x, i) => out.push(...check(x, s.items, defs, [...path, i])));
    }
    if (isObj(v)) {
      for (const k of s.required || []) if (!(k in v)) out.push({ path: at, message: `${JSON.stringify(k)} is a required property` });
      const props = s.properties || {};
      for (const k of Object.keys(v)) {
        if (k in props) out.push(...check(v[k], props[k], defs, [...path, k]));
        else if (s.additionalProperties === false) out.push({ path: at, message: `Additional properties are not allowed (${JSON.stringify(k)} was unexpected)` });
        else if (isObj(s.additionalProperties)) out.push(...check(v[k], s.additionalProperties, defs, [...path, k]));
      }
    }
    if (s.oneOf) {
      const n = s.oneOf.filter((x) => check(v, x, defs, path).length === 0).length;
      if (n !== 1) out.push({ path: at, message: `${JSON.stringify(v)} is valid under ${n} of the given schemas, not exactly one` });
    }
    if (s.if && check(v, s.if, defs, path).length === 0 && s.then) out.push(...check(v, s.then, defs, path));
    return out;
  }

  // Python repr of a JSON value, for the engine's {x!r} messages.
  function pyRepr(v) {
    if (v === null || v === undefined) return "None";
    if (typeof v === "boolean") return v ? "True" : "False";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : pyFloat(String(v));
    if (typeof v === "string") {
      const q = v.includes("'") && !v.includes('"') ? '"' : "'";
      let o = q;
      for (const ch of v) {
        const c = ch.codePointAt(0);
        if (ch === "\\") o += "\\\\";
        else if (ch === q) o += "\\" + q;
        else if (ch === "\n") o += "\\n";
        else if (ch === "\r") o += "\\r";
        else if (ch === "\t") o += "\\t";
        else if (c < 0x20 || c === 0x7f) o += "\\x" + c.toString(16).padStart(2, "0");
        else o += ch;
      }
      return o + q;
    }
    if (Array.isArray(v)) return "[" + v.map(pyRepr).join(", ") + "]";
    return "{" + Object.entries(v).map(([k, x]) => pyRepr(k) + ": " + pyRepr(x)).join(", ") + "}";
  }

  const E = (node, message) => ({ node, message });

  function nodeSchemaErrors(node) {
    const kind = node.kind;
    const name = typeof node.name === "string" ? node.name : null;
    if (!KINDS.includes(kind)) return [E(name, `kind ${pyRepr(kind)} is not one of the seven kinds: ${KINDS.join(", ")} (ADR-0100 rule 1)`)];
    const explicit = [];
    const s = { ...node };
    if (kind === "gate") {
      for (const [key, msg] of [["when", "a gate never carries a when (ADR-0100 rule 3)"], ["adapt", "a gate never adapts (ADR-0103 rule 1)"],
        ["deadline", "a gate carries no deadline (ADR-0102 rule 3)"]]) {
        if (key in s) { explicit.push(E(name, msg)); delete s[key]; }
      }
      const nxt = s.next || {};
      if (isObj(nxt) && nxt.fail !== undefined && nxt.fail !== null && !["repeat", "exit:fail"].includes(nxt.fail)) {
        explicit.push(E(name, `a gate's fail never names a node; got ${pyRepr(nxt.fail)} (ADR-0101 rule 2)`));
        const { fail, ...rest } = nxt;
        s.next = rest;
      }
    }
    if (kind === "loop" && "deadline" in s) { explicit.push(E(name, "a loop carries no deadline (ADR-0102 rule 3)")); delete s.deadline; }
    const errs = check(s, { $ref: `#/$defs/${kind}` }, SCHEMA.$defs, []).map((e) => E(name, `${e.path === "<root>" ? kind : e.path}: ${e.message}`));
    return [...explicit, ...errs];
  }

  function whenErrors(nm, when, nodes, known, factDefs) {
    const f = when.fact;
    if (f.startsWith("outcome:")) return nodes[f.slice(8)] ? [] : [E(nm, `when names unknown node ${f.slice(8)}`)];
    if (!known.has(f)) return [E(nm, `when reads unregistered fact ${f} (ADR-0104 rule 1)`)];
    const d = factDefs[f], v = when.value;
    if (d && d.type === "enum" && !(d.values || []).some((x) => same(x, v))) return [E(nm, `when value ${pyRepr(v)} is not a value of fact ${f}`)];
    if (d && d.type === "bool" && typeof v !== "boolean") return [E(nm, `when value for bool fact ${f} must be a bool`)];
    if (d && (d.type === "int" || d.type === "number") && typeof v !== "number") return [E(nm, `when value for numeric fact ${f} must be a number`)];
    return [];
  }

  function factErrors(doc) {
    const errs = [], seen = new Set();
    for (const f of doc.facts) {
      const n = f.name;
      if (seen.has(n)) errs.push(E(null, `fact ${n}: registered twice`));
      seen.add(n);
      if (FORBIDDEN_NAME.test(n)) errs.push(E(null, `fact ${n}: a gate rate, a finding count or trend, a self-report, or a judge score never triggers (ADR-0104 rule 5)`));
      if (!f.instrument.startsWith("code:")) errs.push(E(null, `fact ${n}: instrument must be code, not a model (ADR-0104 rule 4)`));
      else if (FORBIDDEN_INSTRUMENT.test(f.instrument)) errs.push(E(null, `fact ${n}: instrument names a model, a judge, a cross-run source, or the adaptation report (ADR-0104 rule 4, ADR-0109, ADR-0110)`));
    }
    return errs;
  }

  function subtree(nodes, name) {
    const out = [], stack = [name];
    while (stack.length) {
      const n = nodes[stack.pop()];
      for (const c of n.children || []) if (nodes[c]) { out.push(c); stack.push(c); }
    }
    return out;
  }

  function eachWorkspaceErrors(nm, each, nodes, run) {
    const mp = Math.min(each.max_parallel !== undefined ? each.max_parallel : run.max_parallel, run.max_parallel);
    if (each.mode !== "parallel" || mp < 2) return [];
    const sub = subtree(nodes, nm).map((c) => nodes[c]);
    const created = new Map();
    for (const n of sub) if (n.kind === "action" && n.effect === "worktree_create") {
      const w = n.workspace === undefined ? null : n.workspace;
      created.set(w, [...(created.get(w) || []), n.name]);
    }
    const errs = [];
    for (const [w, ns] of created) if (ns.length > 1) errs.push(E(nm, `parallel items share workspace ${pyRepr(w)}: created by ${ns.join(", ")} (ADR-0102 rule 2)`));
    for (const n of sub) {
      if (!["agent", "action"].includes(n.kind)) continue;
      const writes = (n.kind === "action" && n.effect !== "worktree_create") || (n.kind === "agent" && n.tools.some((t) => WRITE_TOOLS.has(t)));
      if (!writes) continue;
      const ws = n.workspace === undefined ? null : n.workspace;
      if (ws === null || ws === "." || !created.has(ws)) errs.push(E(nm, `parallel items share workspace ${pyRepr(ws)} at ${n.name}: no worktree_create makes one per item (ADR-0102 rule 2)`));
    }
    return errs;
  }

  function fileErrors(nodes) {
    const writers = new Map();
    for (const n of Object.values(nodes)) for (const f of n.writes || []) writers.set(f, [...(writers.get(f) || []), n.name]);
    const errs = [];
    for (const n of Object.values(nodes)) for (const f of n.reads || []) {
      const w = (writers.get(f) || []).filter((x) => x !== n.name);
      if (w.length) errs.push(E(n.name, `reads ${pyRepr(f)}, which ${w.join(", ")} writes: a declared input is never written by the graph (ADR-0110 rule 2)`));
    }
    return errs;
  }

  function dispatchErrors(doc, n, nodes) {
    const nm = n.name, errs = [];
    const e = nodes[n.entry];
    if (e) {
      if (e.kind !== "agent") errs.push(E(nm, `entry ${n.entry} is not an agent`));
      else if (!e.may_spawn) errs.push(E(nm, `entry ${n.entry} lacks may_spawn`));
      if ("when" in e) errs.push(E(nm, `entry ${n.entry} carries a when, which would bypass the dispatch`));
    }
    for (const c of n.checks) if (nodes[c] && nodes[c].kind !== "gate") errs.push(E(nm, `check ${c} is not a gate (ADR-0113 rule 2)`));
    for (const key of ["turns", "spend"]) if (n.budget[key].min > n.budget[key].max) errs.push(E(nm, `child ${key} min above max`));
    if (n.max_parallel > doc.run.max_parallel) errs.push(E(nm, "max_parallel above the run block"));
    const names = n.allow.skills.map((x) => x.name);
    if (names.length !== new Set(names).size) errs.push(E(nm, "duplicate skill in allow"));
    return errs;
  }

  function adaptErrors(nm, n, a, known) {
    const k = n.kind, errs = [];
    if (!["agent", "loop"].includes(k)) errs.push(E(nm, `adapt on a ${k}`));
    for (const x of a.kinds) {
      if (x === "shrink_loop_max" && k !== "loop") errs.push(E(nm, "shrink_loop_max on a non-loop"));
      if (x !== "shrink_loop_max" && k !== "agent") errs.push(E(nm, `${x} on a non-agent`));
    }
    for (const [kind, key] of [["set_model", "models"], ["set_effort", "efforts"], ["narrow_tools", "tools"]]) {
      if (a.kinds.includes(kind) && !(a[key] && a[key].length)) errs.push(E(nm, `${kind} needs a non-empty ${key} list in adapt`));
    }
    if (a.tools !== undefined && a.tools !== null) {
      const own = new Set(n.tools || []);
      if (!a.tools.every((t) => own.has(t))) errs.push(E(nm, "adapt tools widen the node's tools"));
    }
    if (a.models && a.models.length && !a.models.includes(n.model)) errs.push(E(nm, "adapt models exclude the node's own model"));
    if (a.efforts && a.efforts.length && n.effort && !a.efforts.includes(n.effort)) errs.push(E(nm, "adapt efforts exclude the node's own effort"));
    for (const s of a.sees || []) {
      if (SEES_FORBIDDEN.has(s)) errs.push(E(nm, `sees lists ${s}: a proposer may not see it (ADR-0104 rule 3)`));
      else if (!known.has(s) && !s.startsWith("outcome:")) errs.push(E(nm, `sees unregistered fact ${s} (ADR-0104 rule 2)`));
    }
    return errs;
  }

  // The engine's validate(): every error of a graph document. [] means valid.
  function validate(doc) {
    if (!isObj(doc)) return [E(null, "a graph document is one JSON object")];
    const top = JSON.parse(JSON.stringify(SCHEMA));
    top.properties.nodes = { type: "array", minItems: 1 };
    let errs = check(doc, top, SCHEMA.$defs, []).map((e) => E(null, `schema: ${e.path}: ${e.message}`));
    if (!Array.isArray(doc.nodes)) return errs;
    for (const n of doc.nodes) errs.push(...(isObj(n) ? nodeSchemaErrors(n) : [E(null, "a node is one JSON object")]));
    if (errs.length) return errs;
    const nodes = {}, parents = {};
    for (const n of doc.nodes) {
      if (nodes[n.name]) errs.push(E(n.name, "duplicate node name"));
      nodes[n.name] = n;
    }
    const factDefs = Object.fromEntries(doc.facts.map((f) => [f.name, f]));
    const known = new Set([...Object.keys(factDefs), ...BUILTIN]);
    errs.push(...factErrors(doc));
    const gateIds = Object.fromEntries(doc.gates.map((g) => [g.id, new Set(g.params)]));
    for (const [gid, params] of Object.entries(gateIds)) {
      if (!(gid in GATES)) errs.push(E(null, `gates registry: check id ${gid} is outside the engine registry (ADR-0111 rule 7)`));
      else if (params.size !== GATES[gid].length || !GATES[gid].every((p) => params.has(p))) errs.push(E(null, `gates registry: params of ${gid} differ from the engine registry`));
    }
    if (!nodes[doc.root] || nodes[doc.root].kind !== "loop") errs.push(E(doc.root, "root must name a loop"));
    for (const n of Object.values(nodes)) for (const c of n.children || []) {
      if (!nodes[c]) errs.push(E(n.name, `unknown child ${c}`));
      else if (c in parents) errs.push(E(c, "two parents"));
      parents[c] = n.name;
    }
    const entries = {};
    for (const n of Object.values(nodes)) {
      if (n.kind !== "dispatch") continue;
      for (const c of [n.entry, ...n.checks]) {
        if (!nodes[c]) { errs.push(E(n.name, `unknown entry or check ${c}`)); continue; }
        if (c in parents) errs.push(E(c, "two parents"));
        parents[c] = n.name;
      }
      entries[n.entry] = n.name;
    }
    const run = doc.run;
    const minSpend = Object.values(nodes).filter((n) => ["agent", "dispatch"].includes(n.kind)).map((n) => n.budget.spend.min);
    if (minSpend.length && run.spend_cap < Math.max(...minSpend)) errs.push(E(null, `run spend cap ${run.spend_cap} is below an agent's spend minimum ${Math.max(...minSpend)} (ADR-0108 rule 9)`));
    for (const n of Object.values(nodes)) {
      const nm = n.name, k = n.kind;
      if (nm !== doc.root && !(nm in parents)) errs.push(E(nm, "orphan: no parent"));
      if ("deadline" in n && k !== "wait" && run.deadline !== null && n.deadline > run.deadline) errs.push(E(nm, "deadline above the run deadline"));
      const par = nodes[parents[nm]];
      if (n.next && Object.values(n.next).includes("repeat") && !(par && par.kind === "loop")) errs.push(E(nm, "repeat is valid only on a child of a loop"));
      if ("when" in n) errs.push(...whenErrors(nm, n.when, nodes, known, factDefs));
      if (k === "gate") {
        if (!(n.check in gateIds)) errs.push(E(nm, `unknown check ${n.check} (ADR-0111 rule 7)`));
        else {
          const ps = new Set(Object.keys(n.params));
          if (ps.size !== gateIds[n.check].size || ![...ps].every((p) => gateIds[n.check].has(p))) errs.push(E(nm, "params differ from the gates registry"));
        }
      } else if (k === "each") {
        if (!(n.items in factDefs) && !BUILTIN.has(n.items)) errs.push(E(nm, "items is not a registered fact"));
        errs.push(...eachWorkspaceErrors(nm, n, nodes, run));
      } else if (k === "agent") {
        for (const key of ["turns", "spend"]) if (n.budget[key].min > n.budget[key].max) errs.push(E(nm, `${key} min above max`));
        if (n.may_spawn && !(nm in entries)) errs.push(E(nm, "may_spawn on a node that is not a dispatch entry"));
        if (n.may_propose && !("adapt" in n)) errs.push(E(nm, "may_propose without an adapt block"));
      } else if (k === "loop") {
        if (n.max.min > n.max.value) errs.push(E(nm, "max min above value"));
        if ("when" in n && !n.children.some((c) => nodes[c] && ["agent", "each", "action", "dispatch"].includes(nodes[c].kind))) errs.push(E(nm, "a loop with a when must hold an agent, each, action, or dispatch child (ADR-0101 rule 3)"));
      } else if (k === "dispatch") errs.push(...dispatchErrors(doc, n, nodes));
      if (n.adapt) errs.push(...adaptErrors(nm, n, n.adapt, known));
    }
    return [...errs, ...fileErrors(nodes)];
  }

  return { SCHEMA, KINDS, resolve, validate };
}
