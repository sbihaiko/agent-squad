// Dashboard core module: canonical form, hash and import. The module the others depend on.
// A factory: it takes ENGINE and the modules it depends on, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function canonModule(ENGINE) {
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

  // ---- Canonical form and hash: json.dumps(doc, sort_keys=True, separators=(",", ":")) of the parsed document ----
  // JSON.parse loses the difference between 2 and 2.0, so a small parser reads the text and writes the canonical form.
  function pyFloat(raw) {
    const x = Number(raw);
    if (Object.is(x, -0)) return "-0.0";
    if (!Number.isFinite(x)) throw new Error("number out of range: " + raw);
    const [mant, ex] = x.toExponential().split("e");
    const e = parseInt(ex, 10);
    const neg = mant.startsWith("-");
    const digits = mant.replace("-", "").replace(".", "");
    let s;
    if (e >= -4 && e < 16) {
      if (e >= 0) {
        const ip = digits.slice(0, e + 1).padEnd(e + 1, "0");
        const fp = digits.slice(e + 1) || "0";
        s = `${ip}.${fp}`;
      } else s = `0.${"0".repeat(-e - 1)}${digits}`;
    } else {
      const m = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
      s = `${m}e${e < 0 ? "-" : "+"}${String(Math.abs(e)).padStart(2, "0")}`;
    }
    return (neg ? "-" : "") + s;
  }

  function pyInt(raw) {
    const b = BigInt(raw);
    return b.toString();
  }

  function pyString(s) {
    let out = '"';
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i), ch = s[i];
      if (ch === '"') out += '\\"';
      else if (ch === "\\") out += "\\\\";
      else if (ch === "\n") out += "\\n";
      else if (ch === "\r") out += "\\r";
      else if (ch === "\t") out += "\\t";
      else if (ch === "\b") out += "\\b";
      else if (ch === "\f") out += "\\f";
      else if (c < 0x20 || c > 0x7e) out += "\\u" + c.toString(16).padStart(4, "0");
      else out += ch;
    }
    return out + '"';
  }

  const cmpCodePoints = (a, b) => {
    const x = Array.from(a), y = Array.from(b);
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      const d = x[i].codePointAt(0) - y[i].codePointAt(0);
      if (d) return d;
    }
    return x.length - y.length;
  };

  function canonText(v) {
    if (v === null) return "null";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : pyFloat(String(v));
    if (typeof v === "string") return pyString(v);
    if (Array.isArray(v)) return "[" + v.map(canonText).join(",") + "]";
    return "{" + Object.keys(v).sort(cmpCodePoints).map((k) => pyString(k) + ":" + canonText(v[k])).join(",") + "}";
  }

  // Canonical form straight from the JSON text.
  function canonFromText(text) {
    let i = 0;
    const ws = () => { while (" \t\n\r".includes(text[i])) i++; };
    const fail = (m) => { throw new Error(`${m} at ${i}`); };
    function val() {
      ws();
      const c = text[i];
      if (c === "{") {
        i++; ws();
        const items = new Map();
        if (text[i] === "}") { i++; return "{}"; }
        for (;;) {
          ws(); if (text[i] !== '"') fail("expected a key");
          const k = str(); ws(); if (text[i++] !== ":") fail("expected :");
          items.set(k, val()); ws();
          if (text[i] === ",") { i++; continue; }
          if (text[i] === "}") { i++; break; }
          fail("expected , or }");
        }
        return "{" + [...items.keys()].sort(cmpCodePoints).map((k) => pyString(k) + ":" + items.get(k)).join(",") + "}";
      }
      if (c === "[") {
        i++; ws();
        const out = [];
        if (text[i] === "]") { i++; return "[]"; }
        for (;;) {
          out.push(val()); ws();
          if (text[i] === ",") { i++; continue; }
          if (text[i] === "]") { i++; break; }
          fail("expected , or ]");
        }
        return "[" + out.join(",") + "]";
      }
      if (c === '"') return pyString(str());
      for (const [w, v] of [["true", "true"], ["false", "false"], ["null", "null"]]) if (text.startsWith(w, i)) { i += w.length; return v; }
      const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i));
      if (!m) fail("unexpected token");
      i += m[0].length;
      return m[2] || m[3] ? pyFloat(m[0]) : pyInt(m[0]);
    }
    function str() {
      const start = i++;
      while (text[i] !== '"') { if (i >= text.length) fail("unterminated string"); if (text[i] === "\\") i++; i++; }
      i++;
      return JSON.parse(text.slice(start, i));
    }
    const out = val(); ws();
    if (i !== text.length) fail("trailing text");
    return out;
  }

  async function sha256Hex(s) {
    const buf = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  const graphHash = (text) => sha256Hex(canonFromText(text));

  // ---- Import: any parseable JSON that holds a schema_version ----
  function parseImport(text) {
    let doc;
    try { doc = JSON.parse(text); } catch (e) { return { error: "This file is not parseable JSON: " + e.message }; }
    if (!isObj(doc) || !("schema_version" in doc)) return { error: "This file has no schema_version, so it is not a graph document." };
    return { doc };
  }

  const same = (a, b) => canonText(a) === canonText(b);
  const clone = (v) => JSON.parse(JSON.stringify(v));

  return { isObj, same, clone, pyFloat, pyInt, pyString, cmpCodePoints, canonText, canonFromText, sha256Hex, graphHash, parseImport };
}
