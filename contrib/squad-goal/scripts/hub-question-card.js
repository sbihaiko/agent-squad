// Question card rules (#1089). Pure: no DOM, no network, no file. The hub inlines this text into hub-mobile.html.
// A card is data with a fixed schema: questions, each with a header, a body and two to four options; an option has a label,
// a description and an optional recommended flag. The page draws the text it is given and nothing this module did not answer.
const CARD_OPTIONS_MIN = 2, CARD_OPTIONS_MAX = 4;
const CARD_KEYS = ["questions"], QUESTION_KEYS = ["header", "body", "options"], OPTION_KEYS = ["label", "description", "recommended"];
const CARD_CONTROLS = [
  { act: "up", label: "Up" }, { act: "down", label: "Down" },
  { act: "prev", label: "Previous question" }, { act: "next", label: "Next question" },
  { act: "select", label: "Select" }, { act: "cancel", label: "Cancel" }, { act: "submit", label: "Submit" },
];
const questionKey = ({ key, shiftKey }) =>
  key === "ArrowUp" ? "up" : key === "ArrowDown" ? "down" : key === "Tab" ? (shiftKey ? "prev" : "next") :
  key === "Enter" ? "select" : key === "Escape" ? "cancel" : null;
const qIsObject = v =>!!v && typeof v === "object" && !Array.isArray(v);
const qUnknownKeys = (o, allowed) =>Object.keys(o).filter(k => !allowed.includes(k)).map(k => `unknown key ${k}`);
const qClamp = (v, lo, hi) =>Math.min(hi, Math.max(lo, v));

function cardCheck(doc) {
  if (!qIsObject(doc)) return ["the card is not an object"];
  const errs = qUnknownKeys(doc, CARD_KEYS);
  if (!Array.isArray(doc.questions) || doc.questions.length < 1) return [...errs, "a card holds at least one question"];
  doc.questions.forEach((q, i) => {
    const at = `question ${i + 1}`;
    if (!qIsObject(q)) { errs.push(`${at} is not an object`); return; }
    errs.push(...qUnknownKeys(q, QUESTION_KEYS).map(e => `${at}: ${e}`));
    if (typeof q.header !== "string" || !q.header) errs.push(`${at}: header is missing`);
    if (typeof q.body !== "string") errs.push(`${at}: body is not text`);
    if (!Array.isArray(q.options) || q.options.length < CARD_OPTIONS_MIN || q.options.length > CARD_OPTIONS_MAX) {
      errs.push(`${at}: needs two to four options`);
      return;
    }
    let recommended = 0;
    q.options.forEach((o, j) => {
      const at2 = `${at}, option ${j + 1}`;
      if (!qIsObject(o)) { errs.push(`${at2} is not an object`); return; }
      errs.push(...qUnknownKeys(o, OPTION_KEYS).map(e => `${at2}: ${e}`));
      if (typeof o.label !== "string" || !o.label) errs.push(`${at2}: label is missing`);
      if (typeof o.description !== "string") errs.push(`${at2}: description is not text`);
      if (o.recommended !== undefined && typeof o.recommended !== "boolean") errs.push(`${at2}: recommended is not a flag`);
      if (o.recommended === true) recommended++;
    });
    if (recommended > 1) errs.push(`${at}: more than one recommended option`);
  });
  return errs;
}

function cardStart(doc) {
  const n = qIsObject(doc) && Array.isArray(doc.questions) ? doc.questions.length : 0;
  return { q: 0, focus: Array(n).fill(0), answers: Array(n).fill(null), cancelled: false, attempted: false, sent: false };
}

function cardStep(doc, state, act) {
  if (state.cancelled || state.sent) return state;
  const name = typeof act === "string" ? act : act.act;
  const q = state.q, count = doc.questions[q].options.length, focus = state.focus.slice(), answers = state.answers.slice();
  let next = q;
  if (name === "up") focus[q] = qClamp(focus[q] - 1, 0, count - 1);
  else if (name === "down") focus[q] = qClamp(focus[q] + 1, 0, count - 1);
  else if (name === "select") answers[q] = focus[q];
  else if (name === "pick" && Number.isInteger(act.index) && act.index >= 0 && act.index < count) focus[q] = answers[q] = act.index;
  else if (name === "next") next = qClamp(q + 1, 0, doc.questions.length - 1);
  else if (name === "prev") next = qClamp(q - 1, 0, doc.questions.length - 1);
  else if (name === "goto" && Number.isInteger(act.index)) next = qClamp(act.index, 0, doc.questions.length - 1);
  else if (name === "cancel") return { ...state, cancelled: true };
  else return state;
  return { ...state, q: next, focus, answers };
}

function cardSubmit(doc, state) {
  if (state.cancelled) return { ok: false, why: "cancelled", unanswered: [], answers: [], state };
  const unanswered = [];
  doc.questions.forEach((q, i) => { if (state.answers[i] === null) unanswered.push({ index: i, header: q.header }); });
  const next = { ...state, attempted: true, sent: state.sent || unanswered.length === 0 };
  if (unanswered.length) return { ok: false, why: "unanswered", unanswered, answers: [], state: next };
  const answers = doc.questions.map((q, i) => ({ header: q.header, label: q.options[state.answers[i]].label }));
  return { ok: true, why: "", unanswered: [], answers, state: next };
}

function cardView(doc, state = cardStart(doc)) {
  const errors = cardCheck(doc);
  if (errors.length) return { ok: false, errors };
  const Q = doc.questions.length, q = state.q, cur = doc.questions[q], locked = state.cancelled || state.sent;
  return {
    ok: true,
    tabs: Q > 1 ? doc.questions.map((x, i) => ({ index: i, header: x.header, answered: state.answers[i] !== null, active: i === q })) : [],
    active: q,
    header: cur.header,
    body: cur.body,
    options: cur.options.map((o, j) => ({
      label: o.label, description: o.description, recommended: o.recommended === true,
      badge: o.recommended === true ? "Recommended" : "",
      focused: state.focus[q] === j, selected: state.answers[q] === j,
    })),
    controls: locked ? [] : CARD_CONTROLS.filter(c => (c.act !== "prev" && c.act !== "next") || Q > 1),
    unanswered: state.attempted && !state.cancelled ? cardSubmit(doc, state).unanswered : [],
    cancelled: state.cancelled,
    sent: state.sent,
    answers: state.sent ? cardSubmit(doc, state).answers : [],
  };
}
