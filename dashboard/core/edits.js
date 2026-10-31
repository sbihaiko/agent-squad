// Dashboard core module: edit operations: node edits, field groups, add, remove, rename, change kind, connect, disconnect. A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function editsModule({ SCHEMA, KINDS, isObj, same, clone, resolve }) {
  // ---- What a person may write ----
  // A node edit may not change a gate's check id or its params, a node's name, or its kind.
  function applyNodeEdit(doc, name, edited) {
    const i = doc.nodes.findIndex((n) => n.name === name);
    if (i < 0) return { error: `No node named ${name}.` };
    const old = doc.nodes[i];
    if (!isObj(edited)) return { error: "A node is one JSON object." };
    if (edited.name !== old.name) return { error: "The JSON edit keeps the name: use Rename, which updates every reference." };
    if (edited.kind !== old.kind) return { error: "The JSON edit keeps the kind: use Change kind." };
    if (old.kind === "gate" && (edited.check !== old.check || !same(edited.params, old.params))) return { error: "A gate's check id and its thresholds are read-only." };
    const nodes = doc.nodes.slice();
    nodes[i] = edited;
    return { doc: { ...doc, nodes } };
  }

  // ---- Fields grouped by purpose ----
  // The fields of a kind come from the engine's own schema, so a new schema field shows up with no edit here.
  // Every key a node holds sits in exactly one group; a key the schema does not know sits in "unknown" and is kept as written.
  const GROUPS = [
    { id: "identity", title: "Identity", open: true, keys: ["name", "kind"] },
    { id: "settings", title: "Settings", open: true, keys: ["model", "effort", "skill", "tools", "workspace", "output_key", "owner", "default", "effect", "items", "mode", "join", "entry", "checks", "review", "may_propose", "may_spawn"] },
    { id: "instructions", title: "Instructions", open: true, keys: ["instructions"] },
    { id: "limits", title: "Limits", open: true, keys: ["budget", "deadline", "max", "max_by", "stall_after", "max_parallel", "max_depth", "max_children"] },
    { id: "control", title: "Control", open: true, keys: ["when", "next", "children", "adapt"] },
    { id: "files", title: "Files and envelope", open: false, keys: ["reads", "writes", "allow"] },
    { id: "gate", title: "Gate (read-only)", open: true, keys: ["check", "params"] },
  ];
  const GROUPED = new Set(GROUPS.flatMap((g) => g.keys));
  const STRUCTURED = new Set(["budget", "next", "adapt", "when"]);
  const SUGGEST = { model: "models", effort: "efforts", tools: "tools", skill: "skills" };

  function propsOf(kind) {
    const def = SCHEMA.$defs && SCHEMA.$defs[kind];
    return def && def.properties ? def.properties : {};
  }

  // The editor of one field: text, a number, a switch, a choice, a comma list of words, a text area, or JSON for a nested value.
  function fieldType(key, prop0) {
    if (key === "instructions") return "area";
    if (key === "name" || key === "kind") return "text";
    if (STRUCTURED.has(key)) return "structured";
    const prop = prop0 && prop0.$ref ? resolve(prop0.$ref, SCHEMA.$defs) : prop0;
    if (!prop) return "json";
    if (prop.enum) return "enum";
    if (prop.type === "boolean") return "bool";
    if (prop.type === "integer") return "number";
    if (prop.type === "string") return "text";
    if (prop.type === "array" && prop.items && prop.items.type === "string") return "list";
    return "json";
  }

  function shown(type, v) {
    if (v === undefined) return "";
    if (type === "list" && Array.isArray(v) && v.every((x) => typeof x === "string")) return v.join(", ");
    if (type === "text" || type === "area" || type === "enum") return typeof v === "string" ? v : JSON.stringify(v);
    if (type === "bool" || type === "number") return typeof v === "number" || typeof v === "boolean" ? String(v) : JSON.stringify(v);
    return JSON.stringify(v, null, 2);
  }

  // The parts of a nested field, read from the schema: each part is one input at a path in the field's object.
  function partsOf(key, prop0) {
    const prop = prop0.$ref ? resolve(prop0.$ref, SCHEMA.$defs) : prop0;
    const sub = (q) => (q.$ref ? resolve(q.$ref, SCHEMA.$defs) : q);
    const parts = [];
    if (key === "budget") {
      for (const cap of Object.keys(prop.properties))
        for (const [k, q0] of Object.entries(sub(prop.properties[cap]).properties)) {
          const q = sub(q0);
          parts.push({ path: `${cap}.${k}`, label: `${cap} ${k}`, input: q.type === "integer" ? "int" : "num" });
        }
    } else if (key === "next") {
      for (const o of Object.keys(prop.properties)) parts.push({ path: o, label: o, input: "select", choices: sub(prop.properties[o]).enum });
    } else if (key === "adapt") {
      for (const [k, q0] of Object.entries(prop.properties)) {
        const q = sub(q0);
        if (q.type === "array" && q.items.enum) parts.push({ path: k, label: k, input: "check", choices: q.items.enum });
        else if (q.type === "array") parts.push({ path: k, label: k, input: "list" });
      }
    } else if (key === "when") {
      for (const [k, q0] of Object.entries(prop.properties)) {
        const q = sub(q0);
        if (q.enum) parts.push({ path: k, label: k, input: "select", choices: q.enum });
        else parts.push({ path: k, label: k, input: k === "value" ? "any" : "text" });
      }
    }
    return parts;
  }

  function fieldGroups(n) {
    const props = propsOf(n.kind);
    const out = [];
    const placed = new Set();
    for (const g of GROUPS) {
      const fields = g.keys.filter((k) => k in props).map((k) => {
        placed.add(k);
        const type = fieldType(k, props[k]);
        const f = { key: k, type, present: k in n, value: shown(type, n[k]) };
        if (type === "enum") f.enum = (props[k].$ref ? resolve(props[k].$ref, SCHEMA.$defs) : props[k]).enum;
        if (type === "structured") f.parts = partsOf(k, props[k]);
        if (SUGGEST[k]) f.suggest = SUGGEST[k];
        if (k === "name" || k === "kind" || (n.kind === "gate" && (k === "check" || k === "params"))) f.readonly = true;
        return f;
      });
      if (fields.length) out.push({ id: g.id, title: g.title, open: g.open || fields.some((f) => f.present), fields });
    }
    const unknown = Object.keys(n).filter((k) => !placed.has(k)).map((k) => ({ key: k, type: "json", present: true, value: shown("json", n[k]), unknown: true }));
    if (unknown.length) out.push({ id: "unknown", title: "Unknown fields (kept on save)", open: true, fields: unknown });
    return out;
  }

  // One field edit. Blank removes the field. A value the type cannot read is refused with no change; any other value goes in, and the rules of validate() judge it.
  function setField(doc, name, key, text) {
    const list = Array.isArray(doc.nodes) ? doc.nodes : [];
    const old = list.find((n) => isObj(n) && n.name === name);
    if (!old) return { error: `No node named ${name}.` };
    if (key === "name") return { error: "Use Rename: it updates every reference." };
    if (key === "kind") return { error: "Use Change kind." };
    const props = propsOf(old.kind);
    // A key in no group is shown as JSON in "unknown", so it is read back as JSON, whatever its schema type.
    const type = GROUPED.has(key) ? fieldType(key, props[key]) : "json";
    const t = String(text);
    let value;
    if (t.trim() === "") value = undefined;
    else if (type === "text" || type === "area" || type === "enum") value = t;
    else if (type === "number") {
      value = Number(t);
      if (!Number.isInteger(value)) return { error: `${key} takes a whole number, not ${JSON.stringify(t)}.` };
    } else if (type === "bool") {
      if (t.trim() !== "true" && t.trim() !== "false") return { error: `${key} is true or false.` };
      value = t.trim() === "true";
    } else if (type === "list") value = t.split(",").map((x) => x.trim()).filter(Boolean);
    else {
      try { value = JSON.parse(t); } catch (e) { return { error: `${key} is not valid JSON: ${e.message}` }; }
    }
    const edited = {};
    for (const k of Object.keys(old)) if (k !== key || value !== undefined) edited[k] = k === key ? value : old[k];
    if (value !== undefined && !(key in old)) edited[key] = value;
    if (value === undefined && !(key in old)) return { doc };
    return applyNodeEdit(doc, name, edited);
  }

  // ---- Add and remove a node ----
  // Both are pure: they return a new document. The rules of validate() judge the result, so a gap shows on the node.
  const HOLDS_CHILDREN = ["loop", "each"];

  function newNodeBody(doc, kind, name, opts) {
    const dispatch = doc.nodes.find((n) => isObj(n) && n.kind === "dispatch");
    const budget = () => ({ stalls: { min: 1, max: 5 }, spend: { min: 0.01, max: 1 } });
    if (kind === "agent") return { name, kind, model: "sonnet", tools: [], budget: budget() };
    if (kind === "wait") return { name, kind, owner: "owner", deadline: 3600, default: "fail" };
    if (kind === "action") return { name, kind, effect: "commit" };
    if (kind === "loop") return { name, kind, max: { value: 3, min: 1 }, children: [] };
    if (kind === "each") return { name, kind, items: ((doc.facts || [])[0] || {}).name || "items", mode: "sequence", join: "all", children: [], deadline: 3600 };
    if (kind === "dispatch") {
      // A dispatch names an entry agent and checks that exist; the person connects them. Until then the engine rule shows on the node.
      const keep = ["max_depth", "max_children", "max_parallel", "deadline", "join", "allow", "budget"];
      const base = dispatch ? clone(Object.fromEntries(keep.map((k) => [k, dispatch[k]]))) : { max_depth: 2, max_children: 4, max_parallel: 1, deadline: 3600, join: "all", budget: { stalls: { min: 2, max: 5 }, spend: { min: 0.05, max: 4 } },
        allow: { models: ["sonnet"], efforts: ["medium"], tools: [], skills: [{ name: "tdd", version: "1" }], workspaces: ["worktrees/child"] } };
      return { ...base, name, kind, entry: "todo_entry", checks: ["todo_check"] };
    }
    // A gate picks a registered check id and never adds one. Its thresholds are not authored here: copy a gate that already holds them, else leave each one null.
    const reg = (doc.gates || []).find((g) => g.id === opts.check);
    const twin = doc.nodes.find((n) => isObj(n) && n.kind === "gate" && n.check === opts.check);
    return { name, kind, check: reg.id, params: twin ? clone(twin.params) : Object.fromEntries((reg.params || []).map((p) => [p, null])) };
  }

  // A gate picks a check id the registry holds, and the dashboard never adds one.
  function gateOpts(doc, opts) {
    if (!(doc.gates || []).length) return { error: "The gates registry is empty: a gate needs a registered check id." };
    const check = opts.check === undefined ? doc.gates[0].id : opts.check;
    if (!doc.gates.some((g) => g.id === check)) return { error: `${check} is not in the gates registry. The dashboard picks a registered check and never adds one.` };
    return { opts: { ...opts, check } };
  }

  function addNode(doc, kind, opts) {
    opts = opts || {};
    if (!KINDS.includes(kind)) return { error: `${kind} is not one of the seven kinds: ${KINDS.join(", ")}.` };
    const byName = Object.fromEntries(doc.nodes.filter(isObj).map((n) => [n.name, n]));
    const parent = opts.parent !== undefined ? opts.parent : (byName[doc.root] && HOLDS_CHILDREN.includes(byName[doc.root].kind) ? doc.root : null);
    if (opts.parent !== undefined && !(byName[parent] && HOLDS_CHILDREN.includes(byName[parent].kind))) return { error: `${parent} is not a loop or an each, so it holds no child.` };
    if (kind === "gate") {
      const g = gateOpts(doc, opts);
      if (g.error) return g;
      opts = g.opts;
    }
    let n = 1;
    while (byName[`${kind}_${n}`]) n++;
    const name = `${kind}_${n}`;
    const body = newNodeBody(doc, kind, name, opts);
    const nodes = doc.nodes.map((x) => (isObj(x) && x.name === parent ? { ...x, children: [...(x.children || []), name] } : x));
    nodes.push(body);
    return { doc: { ...doc, nodes }, name };
  }

  // The nodes a dispatch names: its entry, its checks, and its review when it has one.
  const dispatchLinks = (n) => [n.entry, ...(Array.isArray(n.checks) ? n.checks : []), ...("review" in n ? [n.review] : [])];

  // The review gate a dispatch names, as the editor shows it: the gate, its command and its deadline. Null when the dispatch names none.
  // A name with no gate behind it shows with no command, so the page says so and the rules name the error.
  function reviewGate(doc, name) {
    const d = doc.nodes.find((n) => isObj(n) && n.name === name);
    if (!d || d.kind !== "dispatch" || !("review" in d)) return null;
    const g = doc.nodes.find((n) => isObj(n) && n.name === d.review);
    const p = g && g.kind === "gate" && isObj(g.params) ? g.params : null;
    return { gate: d.review, command: p && Array.isArray(p.argv) ? p.argv.join(" ") : null, deadline: p && typeof p.deadline === "number" ? p.deadline : null };
  }

  function removeNode(doc, name) {
    const gone = doc.nodes.find((n) => isObj(n) && n.name === name);
    if (!gone) return { error: `No node named ${name}.` };
    if (name === doc.root) return { error: "The root loop is the start of the graph and is not removed." };
    const warnings = [];
    const nodes = doc.nodes.filter((n) => n !== gone).map((n) => {
      if (!isObj(n)) return n;
      if (Array.isArray(n.children) && n.children.includes(name)) {
        warnings.push({ node: n.name, message: `${n.name} no longer lists child ${name}` });
        return { ...n, children: n.children.filter((c) => c !== name) };
      }
      if (n.kind === "dispatch" && dispatchLinks(n).includes(name)) {
        warnings.push({ node: n.name, message: `${n.name} still names ${name} as its ${n.entry === name ? "entry" : n.review === name ? "review" : "check"}: choose another` });
      }
      return n;
    });
    const orphans = [...(gone.children || []), ...(gone.kind === "dispatch" ? dispatchLinks(gone) : [])];
    for (const c of orphans) if (nodes.some((n) => isObj(n) && n.name === c)) warnings.push({ node: c, message: `${c} has no parent now: it is an orphan until you give it one` });
    return { doc: { ...doc, nodes }, warnings };
  }

  // The model, effort, tools, and skills a node names that the served snapshot does not hold. Allowed and never blocked: the side panel only notes them.
  function unseenValues(n, served) {
    const skills = Array.isArray(n.skills) ? n.skills.map((s) => (isObj(s) && s.name !== undefined ? s.name : s)) : [];
    return [["model", "models", [n.model]], ["effort", "efforts", [n.effort]], ["tools", "tools", n.tools], ["skills", "skills", skills]]
      .flatMap(([label, key, vs]) => (Array.isArray(vs) ? vs : []).filter((v) => typeof v === "string" && !(served[key] || []).includes(v)).map((v) => `${label} ${v}`));
  }

  // ---- Rename a node and change its kind ----
  // Both are pure and return a new document. The rules of validate() judge the result, so a gap shows on the node.
  const NAME_RE = new RegExp(SCHEMA.$defs.name.pattern);

  // The places a node name is written: root, children, a dispatch's entry, checks and review, a `when` or a `sees` entry named outcome:<node>.
  function renameNode(doc, old, neu) {
    const list = Array.isArray(doc.nodes) ? doc.nodes : [];
    if (!list.some((n) => isObj(n) && n.name === old)) return { error: `No node named ${old}.` };
    if (typeof neu !== "string" || !NAME_RE.test(neu)) return { error: `${JSON.stringify(neu)} is not a legal name: it starts with a lowercase letter and holds lowercase letters, digits, and underscores.` };
    if (neu === old) return { error: `${old} already has that name.` };
    if (list.some((n) => isObj(n) && n.name === neu)) return { error: `A node named ${neu} exists already.` };
    const swap = (v) => (v === old ? neu : v);
    const swapFact = (f) => (f === `outcome:${old}` ? `outcome:${neu}` : f);
    const nodes = list.map((n) => {
      if (!isObj(n)) return n;
      const m = { ...n };
      if (n.name === old) m.name = neu;
      if (Array.isArray(n.children)) m.children = n.children.map(swap);
      if (n.entry === old) m.entry = neu;
      if (Array.isArray(n.checks)) m.checks = n.checks.map(swap);
      if (n.review === old) m.review = neu;
      if (isObj(n.when) && typeof n.when.fact === "string") m.when = { ...n.when, fact: swapFact(n.when.fact) };
      if (isObj(n.adapt) && Array.isArray(n.adapt.sees)) m.adapt = { ...n.adapt, sees: n.adapt.sees.map(swapFact) };
      return m;
    });
    return { doc: { ...doc, nodes, ...(doc.root === old ? { root: neu } : {}) } };
  }

  function layoutRename(layout, old, neu) {
    const out = {};
    for (const [k, v] of Object.entries(layout)) out[k === old ? neu : k] = v;
    return out;
  }

  // The name, the place in the graph, and every field stay. A field the new kind does not allow stays too: its schema error shows on the node.
  // A gate is never a source: its check id and thresholds are read-only, so changing it would write what a person may not write.
  function changeKind(doc, name, kind, opts) {
    opts = opts || {};
    if (!KINDS.includes(kind)) return { error: `${kind} is not one of the seven kinds: ${KINDS.join(", ")}.` };
    const old = (Array.isArray(doc.nodes) ? doc.nodes : []).find((n) => isObj(n) && n.name === name);
    if (!old) return { error: `No node named ${name}.` };
    if (old.kind === kind) return { error: `${name} is already a ${kind}.` };
    if (old.kind === "gate") return { error: `${name} is a gate: its check id and thresholds are read-only. Remove it and add the node you want.` };
    if (kind === "gate") {
      const g = gateOpts(doc, opts);
      if (g.error) return g;
      opts = g.opts;
    }
    const body = { name, kind };
    for (const [k, v] of Object.entries(newNodeBody(doc, kind, name, opts))) if (k !== "name" && k !== "kind") body[k] = v;
    for (const [k, v] of Object.entries(old)) if (k !== "name" && k !== "kind") body[k] = clone(v);
    return { doc: { ...doc, nodes: doc.nodes.map((n) => (n === old ? body : n)) }, name };
  }

  // ---- Connect and disconnect ----
  // A link is a loop or each child, a dispatch entry, or a dispatch check. `next` holds reserved words, never a node name, so no link sets it.
  // Both functions are pure. A second parent or an orphan is allowed: validate() shows it on the node, and a save is never blocked.
  function reaches(byName, from, to, seen) {
    if (from === to) return true;
    seen = seen || new Set();
    if (seen.has(from) || !byName[from]) return false;
    seen.add(from);
    const n = byName[from];
    const kids = [...(Array.isArray(n.children) ? n.children : []), ...(n.kind === "dispatch" ? dispatchLinks(n) : [])];
    return kids.some((c) => reaches(byName, c, to, seen));
  }
  const hasParent = (nodes, name) => nodes.some((n) => isObj(n) && ((Array.isArray(n.children) && n.children.includes(name)) || (n.kind === "dispatch" && dispatchLinks(n).includes(name))));

  function connect(doc, from, to) {
    const byName = Object.fromEntries(doc.nodes.filter(isObj).map((n) => [n.name, n]));
    const a = byName[from], b = byName[to];
    if (!a) return { error: `No node named ${from}.` };
    if (!b) return { error: `No node named ${to}.` };
    if (from === to) return { error: `${from} cannot hold itself.` };
    if (to === doc.root) return { error: `${to} is the root loop: it has no parent.` };
    if (reaches(byName, to, from)) return { error: `${to} already holds ${from}: this link would make a cycle.` };
    const warnings = [];
    let patch;
    if (HOLDS_CHILDREN.includes(a.kind)) {
      if ((a.children || []).includes(to)) return { error: `${to} is already a child of ${from}.` };
      patch = { children: [...(a.children || []), to] };
    } else if (a.kind === "dispatch") {
      if (b.kind === "gate" && b.check === "review-command") {
        // A review-command gate runs only as the dispatch's review, never as a check (ADR-0113).
        if (a.review === to) return { error: `${to} is already the review of ${from}.` };
        if ("review" in a) return { error: `${from} has the review ${a.review}: disconnect it first.` };
        patch = { review: to };
      } else if (b.kind === "gate") {
        if ((a.checks || []).includes(to)) return { error: `${to} is already a check of ${from}.` };
        patch = { checks: [...(a.checks || []), to] };
      } else {
        if (a.entry === to) return { error: `${to} is already the entry of ${from}.` };
        patch = { entry: to };
      }
    } else return { error: `${from} is ${/^[aeiou]/.test(a.kind) ? "an" : "a"} ${a.kind}: only a loop, an each, or a dispatch holds a link.` };
    const nodes = doc.nodes.map((n) => (n === a ? { ...a, ...patch } : n));
    if (patch.entry) {
      const old = byName[a.entry];
      if (old && !hasParent(nodes, old.name)) warnings.push({ node: old.name, message: `${old.name} has no parent now: it is an orphan until you give it one` });
    }
    return { doc: { ...doc, nodes }, warnings };
  }

  function disconnect(doc, from, to) {
    const a = doc.nodes.find((n) => isObj(n) && n.name === from);
    if (!a) return { error: `No node named ${from}.` };
    let patch;
    if ((a.children || []).includes(to)) patch = { children: a.children.filter((c) => c !== to) };
    else if (a.kind === "dispatch" && (a.checks || []).includes(to)) patch = { checks: a.checks.filter((c) => c !== to) };
    else if (a.kind === "dispatch" && a.review === to) {
      const { review, ...rest } = a;
      const nodes = doc.nodes.map((n) => (n === a ? rest : n));
      return { doc: { ...doc, nodes }, warnings: hasParent(nodes, to) ? [] : [{ node: to, message: `${to} has no parent now: it is an orphan until you give it one` }] };
    } else if (a.kind === "dispatch" && a.entry === to) return { error: `${to} is the entry of ${from}: connect another agent to replace it.` };
    else return { error: `${from} has no link to ${to}.` };
    const nodes = doc.nodes.map((n) => (n === a ? { ...a, ...patch } : n));
    const warnings = [];
    if (nodes.some((n) => isObj(n) && n.name === to) && !hasParent(nodes, to)) warnings.push({ node: to, message: `${to} has no parent now: it is an orphan until you give it one` });
    return { doc: { ...doc, nodes }, warnings };
  }


  // The top-level fields a person may not change: the two registries.
  const READ_ONLY_TOP = ["facts", "gates"];

  // Every kind, with the node's own kind selected, so the change-kind select shows what the node is now.
  const kindChoices = (current) => KINDS.map((kind) => ({ kind, selected: kind === current }));

  return { applyNodeEdit, fieldGroups, setField, addNode, removeNode, reviewGate, connect, disconnect, renameNode, layoutRename, changeKind, kindChoices, unseenValues, READ_ONLY_TOP };
}
