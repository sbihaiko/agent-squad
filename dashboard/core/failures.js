// Dashboard core module: Why a node failed, and a node's label. (ticket #116). Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function failuresModule({ orNull }) {
  // ---- Failures and dispatch children (ticket #91) ----
  // A reason is `<check id>:<code>:<detail>` from a gate, `check:<gate>:<code>:<detail>` from a dispatch check, or a bare code.
  // A check id has no underscore, a code has no hyphen: that is how `after_failed:route.router.1` stays a code.
  function reasonParts(text) {
    const raw = String(text), seg = raw.split(":");
    const isGate = (x) => /^[a-z][a-z0-9-]*$/.test(x) && !x.includes("_");
    let gate = null, rest = seg;
    if (seg[0] === "check" && seg.length > 1) { gate = seg[1]; rest = seg.slice(2); }
    else if (seg.length > 1 && isGate(seg[0])) { gate = seg[0]; rest = seg.slice(1); }
    const code = rest.length ? rest[0] : seg[0], detail = rest.slice(1).join(":");
    // The subject names what failed: the first python file of an acceptance command (the issue's test), else the detail cut short.
    const py = code === "ac_failed" ? /(?:^|[\s/])([\w.-]+\.py)\b/.exec(detail) : null;
    const subject = py ? py[1] : detail.length > 60 ? detail.slice(0, 59) + "…" : detail;
    return { raw, gate, code, detail, subject };
  }

  // One short line for a card: each code once, with a count when it repeats.
  function causeSummary(reasons) {
    const rs = (Array.isArray(reasons) ? reasons : []).map(reasonParts);
    if (!rs.length) return null;
    const by = new Map();
    for (const r of rs) by.set(r.code, (by.get(r.code) || 0) + 1);
    return [...by].map(([c, n]) => n > 1 ? `${c} ×${n}` : c).join(", ");
  }

  // Every failure the record shows, in order: a node that did not pass, a landing that failed, a request that code rejected.
  // An outcome whose reasons repeat an earlier one is a fold of it (a loop that carries its child's reasons), so it is skipped.
  function failureCauses(events) {
    const seen = new Set(), out = [];
    const add = (e, kind, node, outcome, reasons, evidence) => {
      const key = JSON.stringify(reasons);
      if (!reasons.length || seen.has(key)) return;
      seen.add(key);
      out.push({ kind, node, outcome, pos: orNull(e.pos), reasons: reasons.map((r) => ({ ...reasonParts(r), evidence: evidence && evidence[r] !== undefined ? evidence[r] : null })) });
    };
    for (const e of events) {
      if (e.event === "outcome" && e.outcome !== "pass") add(e, "node", e.node, e.outcome, e.reasons || [], e.evidence);
      else if (e.event === "land" && e.outcome !== "pass") add(e, "land", e.node, e.outcome, e.reasons || [], e.effect && e.effect.stderr ? Object.fromEntries((e.reasons || []).map((r) => [r, e.effect.stderr])) : null);
      else if (e.event === "spawn_rejected") add(e, "rejected", `${e.envelope} request ${e.request_key}`, "rejected", e.reasons || [e.reason], null);
    }
    return out;
  }

  // The bare skill name of a node or a dispatch child: a skill field is a string, or a {name, version} object already
  // resolved by squad/envelope.py (issue #99). The label drops the version; the detail panel keeps it.
  const skillName = (s) => (typeof s === "string" && s ? s : s && typeof s.name === "string" && s.name ? s.name : null);

  // The role label on a node card (issue #99): what the node IS, derived from the graph node, never its bare name when a
  // role is known. Display only: it never writes the graph and never enters the graph hash (ADR-0111). A node the rule does
  // not cover falls back to its name, so the label is never empty. A loop, an each, and a dispatch carry no role field of
  // their own (their bound and their kind sit on the detail line), and two loops would collide on the bare kind, so they
  // keep their own name, the only token that tells them apart.
  function nodeLabel(node) {
    const n = node || {};
    const name = typeof n.name === "string" && n.name ? n.name : null;
    const field = (k) => (typeof n[k] === "string" && n[k] ? n[k] : null);
    if (n.kind === "gate") return field("check") || name || "gate";
    if (n.kind === "action") return field("effect") || name || "action";
    if (n.kind === "wait") return field("owner") || name || "wait";
    if (n.kind === "agent") return skillName(n.skill) || name || "agent";
    return name || field("kind") || "node";
  }

  return { reasonParts, causeSummary, failureCauses, skillName, nodeLabel };
}
