"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clip = (s, n) => { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s; };
const QS = new URLSearchParams(location.search);

const ROLE = {
  in: ["#2a78d6", "Input pair", "listens to the signal"], load: ["#eb6834", "Load", "turns current into voltage"],
  tail: ["#1baf7a", "Tail source", "sets stage-1 current"], drv: ["#6c56d9", "Output driver", "second-stage gain"],
  sink: ["#e87ba4", "Output sink", "second-stage current"], psrc: ["#d98a2b", "P current source", "feeds the fold"],
  pcas: ["#b0569c", "P cascode", "boosts gain"], ncas: ["#3a9db5", "N cascode", "boosts gain"],
  nsink: ["#7a8f3c", "N current sink", "folded branch current"],
};
const WCOL = ["#2a78d6", "#eb6834", "#1baf7a", "#d98a2b", "#e87ba4", "#3a9db5", "#6c56d9", "#7a8f3c"];
const SPEC = {
  pm_deg: ["Stability", "phase margin", 1, "°", 0], ugf_hz: ["Speed", "unity-gain frequency", 1e-6, "MHz", 1],
  dc_gain_db: ["Accuracy", "DC gain", 1, "dB", 0], i_core_a: ["Power", "core supply current", 1e6, "µA", 0],
};
const FAILWORD = { pm_deg: "stability", ugf_hz: "speed", dc_gain_db: "accuracy", i_core_a: "power", gain_margin_db: "stability" };
const CORNERS = ["tt", "ff", "ss", "sf", "fs"], TEMPS = [-40, 27, 125];
const DEFAULT_SPEC = { pm_deg: [">=", 60], ugf_hz: [">=", 1e7], dc_gain_db: [">=", 60], i_core_a: ["<=", 1e-4] };
const guard = (s) => ({ pm_deg: [s.pm_deg[0], s.pm_deg[1] + 5], ugf_hz: [s.ugf_hz[0], s.ugf_hz[1] * 1.1], dc_gain_db: [s.dc_gain_db[0], s.dc_gain_db[1] + 3], i_core_a: [s.i_core_a[0], s.i_core_a[1] * 0.9] });
const fmt = (k, v) => v == null ? "–" : (v * SPEC[k][2]).toFixed((v * SPEC[k][2]) >= 100 ? 0 : SPEC[k][4] || ((v * SPEC[k][2]) < 10 ? 1 : 0));

const S = { step: 1, roles: null, spec: null, run: null, es: null, signES: null };

function toast(msg, ms = 4000) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), ms); }
async function jpost(url, body, headers) {
  const r = await fetch(url, { method: "POST", body, headers });
  return r.json();
}

/* ------------------------------------------------------------------ steps */
function go(n) {
  S.step = n;
  document.querySelectorAll(".step").forEach((s) => s.classList.toggle("on", s.id === "step" + n));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("on", +b.dataset.step === n));
  if (n === 5 || n === 6) loadProof();
  if (n === 4) loadLayoutRuns();
}
document.querySelectorAll("#nav button").forEach((b) => (b.onclick = () => go(+b.dataset.step)));
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
  if (document.body.classList.contains("drawer-open") || e.target.closest?.("[data-cand],[data-part],[data-lay],[data-att],.term")) return;   // explain.js owns the keys
  if (typing) { if (e.key === "Enter" && document.activeElement.id === "specText") $("btnSpecText").click(); if (e.key === "Escape") document.activeElement.blur(); return; }
  if (e.key >= "1" && e.key <= "7") return go(+e.key);
  if (e.key === "ArrowRight") return go(Math.min(7, S.step + 1));
  if (e.key === "ArrowLeft") return go(Math.max(1, S.step - 1));
  if (e.key === "Enter") {
    e.preventDefault();
    if (S.step === 1) (camStream ? $("btnCapture") : $("btnCam")).click();
    else if (S.step === 3) $("btnRun").click();
    else if (S.step === 4) $("btnLayout").click();
    else if (S.step === 5) $("btnSignoff").click();
  }
  if (e.code === "Space" && S.step === 2) { e.preventDefault(); if (!e.repeat) talkStart(); }
});
document.addEventListener("keyup", (e) => { if (e.code === "Space" && S.step === 2 && recorder) { e.preventDefault(); talkStop(); } });

/* ------------------------------------------------------------------ step 1: photo -> roles */
let camStream = null;
$("btnCam").onclick = async () => {
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 }, facingMode: "environment" }, audio: false });
    $("cam").srcObject = camStream; $("cam").hidden = false; $("photo").hidden = true; $("cam").parentElement.classList.remove("hasphoto");
    $("camhint").hidden = true; $("btnCapture").disabled = false; $("btnCam").textContent = "Retake";
  } catch (err) { $("camhint").textContent = "camera unavailable (" + err.name + ") — use Upload photo"; }
};
$("btnCapture").onclick = () => {
  const v = $("cam"); if (!v.videoWidth) return;
  const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  c.toBlob((b) => sendPhoto(b), "image/jpeg", 0.92);
};
$("filePhoto").onchange = (e) => { if (e.target.files[0]) sendPhoto(e.target.files[0]); };
async function sendPhoto(blob) {
  $("photo").src = URL.createObjectURL(blob); $("photo").hidden = false; $("cam").hidden = true; $("camhint").hidden = true; $("photo").parentElement.classList.add("hasphoto");
  $("roleHeadline").className = "headline muted"; $("roleHeadline").textContent = "Decisions API is labeling every transistor…";
  $("roleSub").textContent = ""; $("roleGroups").innerHTML = ""; $("knobs").innerHTML = "";
  try {
    const res = await jpost("/api/photo?circuit=" + $("selCircuit").value, blob, { "Content-Type": blob.type || "image/jpeg" });
    renderRoles(res);
  } catch (err) { $("roleHeadline").textContent = "Upload failed: " + err; }
}
function renderRoles(res) {
  S.roles = res;
  const devs = res.devices || [];
  const scored = devs.filter((d) => d.correct != null);
  const nOk = scored.filter((d) => d.correct).length;
  const H = $("roleHeadline");
  const cname = { diff: "two-stage differential op-amp", fc: "folded-cascode op-amp", tele: "telescopic op-amp" }[res.circuit] || res.circuit || "circuit";
  if (res.fallback) {
    H.className = "headline warn";
    H.innerHTML = "Decisions API not reached — showing the answer key instead";
    $("roleSub").textContent = (res.error ? "(" + clip(res.error, 160) + ") " : "") + "These are the known roles for the " + cname + ", not a model output.";
  } else {
    H.className = "headline";
    H.innerHTML = `Decisions API labeled <b>${devs.length}</b> transistors` + (res.latency_s != null ? ` in <b>${(+res.latency_s).toFixed(1)} s</b>` : "") +
      (scored.length ? ` — <b>${nOk}/${scored.length}</b> match the answer key` : "");
    $("roleSub").innerHTML = `It recognised a <b>${esc(cname)}</b>` + (res.circuit_confidence != null ? ` (${Math.round(res.circuit_confidence * 100)}% sure)` : "") + ` · model <code>${esc(res.model || "")}</code>`;
  }
  const G = $("roleGroups"); G.innerHTML = "";
  const order = Object.keys(ROLE); let i = 0;
  const roles = [...new Set(devs.map((d) => d.role))].sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
  for (const r of roles) {
    const [col, name, what] = ROLE[r] || ["#777", r, ""];
    const g = el("div", "rolegroup");
    const rn = el("div", "rolename", `<i style="background:${col}"></i><span>${esc(name)}<small>${esc(what)}</small></span>`);
    rn.dataset.part = r; rn.tabIndex = 0; rn.title = "click: what this part does, its knobs, and why the crew changed it"; g.append(rn);
    const chips = el("div", "chips");
    for (const d of devs.filter((x) => x.role === r)) {
      const c = el("div", "chip" + (d.correct === false ? " wrong" : ""));
      c.style.borderColor = col; c.style.animationDelay = (i++ * 70) + "ms"; c.dataset.part = r; c.tabIndex = 0;
      if (d.correct === false) c.dataset.truth = (ROLE[d.truth] || [0, d.truth])[1];
      const conf = d.confidence != null ? Math.round(d.confidence * 100) : null;
      c.innerHTML = `<b>${esc(d.label || d.inst)}</b>` + (conf != null ? `<span class="conf">${conf}%</span><div class="bar"><i style="width:${conf}%;background:${col}"></i></div>` : "");
      chips.append(c);
    }
    g.append(chips); G.append(g);
  }
  if (res.knobs?.length) $("knobs").innerHTML = `<b>${res.knobs.length} tuning knobs</b> follow from those roles (click a transistor to explore it): ` + res.knobs.map((k) => `<code>${esc(k)}</code>`).join("");
  if (res.circuit && ["diff", "fc", "tele"].includes(res.circuit)) $("selCircuit").value = res.circuit;
}

/* ------------------------------------------------------------------ step 2: voice -> spec */
let recorder = null, chunks = [], micStream = null;
async function talkStart() {
  if (recorder) return;
  try {
    micStream = micStream || await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = []; recorder = new MediaRecorder(micStream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" }); recorder = null;
      $("btnTalk").classList.remove("rec"); $("talkState").textContent = "Gemini 4 Hydrogen is listening to the recording…";
      try { renderSpec(await jpost("/api/voice", blob, { "Content-Type": blob.type })); } catch (err) { $("talkState").textContent = "failed: " + err; }
    };
    recorder.start(); $("btnTalk").classList.add("rec"); $("talkState").textContent = "recording… release to send";
  } catch (err) { $("talkState").textContent = "microphone unavailable (" + err.name + ") — type the spec below"; }
}
function talkStop() { if (recorder && recorder.state === "recording") recorder.stop(); }
const bt = $("btnTalk");
bt.addEventListener("pointerdown", (e) => { e.preventDefault(); talkStart(); });
["pointerup", "pointerleave"].forEach((ev) => bt.addEventListener(ev, talkStop));
$("btnSpecText").onclick = async () => {
  const text = $("specText").value.trim(); if (!text) return;
  $("talkState").textContent = "Gemini 4 Hydrogen is reading the spec…";
  try { renderSpec(await jpost("/api/spec_text", JSON.stringify({ text }), { "Content-Type": "application/json" })); } catch (err) { $("talkState").textContent = "failed: " + err; }
};
function renderSpec(res, initial) {
  if (res && res.error && !res.spec) { $("talkState").textContent = res.error; return; }
  const spec = (res && res.spec) || DEFAULT_SPEC, target = (res && res.target) || guard(spec);
  const prev = S.spec?.spec || DEFAULT_SPEC;
  if (!initial) {
    S.spec = { spec, target, session: res.session, fallback: !!res.fallback };
    $("talkState").textContent = "";
    $("transcript").hidden = !res.transcript; $("transcript").textContent = "“" + (res.transcript || "") + "”";
    const H = $("specHeadline");
    if (res.fallback) { H.className = "headline warn"; H.textContent = "Gemini not reached — offline parser read the numbers"; }
    else { H.className = "headline"; H.innerHTML = "Spec card" + (res.latency_s != null ? ` — read in <b>${(+res.latency_s).toFixed(1)} s</b>` : ""); }
    $("specNotes").innerHTML = (res.notes || []).map(esc).join(" · ") + (res.model ? ` <span class="small">· ${esc(res.model)}</span>` : "");
  }
  const C = $("specCard"); C.innerHTML = "";
  for (const k of Object.keys(SPEC)) {
    if (!spec[k]) continue;
    const [plain, jargon, , unit] = SPEC[k];
    const op = spec[k][0].startsWith(">") ? "at least" : "at most";
    const stem = { pm_deg: "pm_deg", ugf_hz: "ugf", dc_gain_db: "dc_gain", i_core_a: "i_core" }[k];
    const said = res && Array.isArray(res.stated) ? res.stated.some((x) => String(x).startsWith(stem)) : null;
    const d = el("div", "spec" + (!initial && (said === true || (said === null && prev[k] && Math.abs(prev[k][1] - spec[k][1]) > 1e-12)) ? " changed" : ""));
    d.innerHTML = `<div class="plain">${plain} <span class="jargon">${jargon}</span></div>
      <div class="val"><small>${op}</small> ${fmt(k, spec[k][1])}<small>${unit === "°" ? "°" : " " + unit}</small></div>
      <div class="said">${said === true ? "✓ you said this" : said === false ? "not mentioned — using the default" : ""}</div>
      <div class="guard">crew aims for ${fmt(k, target[k][1])} ${unit} — a safety margin, so it still passes if the simulator is slightly off</div>`;
    C.append(d);
  }
}

/* ------------------------------------------------------------------ step 3: the crew */
let R = null;   // per-run view state
function resetStage() {
  R = { id: null, cands: [], best: null, firstPass: null, workers: {}, nWorkers: 0, target: guard(DEFAULT_SPEC), spec: DEFAULT_SPEC,
        t: 0, tWall: performance.now(), speed: 1, live: false, planT: null, plans: 0, critics: 0, rounds: 0, done: false };
  $("grid").innerHTML = '<div class="h"></div>' + TEMPS.map((t) => `<div class="h">${t}&nbsp;°C</div>`).join("") +
    CORNERS.map((c) => `<div class="h" title="process corner">${c}</div>` + TEMPS.map((t) => `<div class="tile" id="tile_${c}_${t}"></div>`).join("")).join("");
  $("nPass").textContent = "0"; $("nPass").className = ""; $("bestLabel").textContent = "best design so far";
  $("dots").innerHTML = ""; $("workers").innerHTML = ""; $("banner").hidden = true; $("banner").className = "banner";
  $("archBubble").className = "bubble big-bubble muted"; $("archBubble").textContent = "Waiting for the first simulation…";
  $("archBinding").textContent = ""; $("archState").textContent = ""; $("critState").textContent = ""; $("judgeState").textContent = "";
  $("critImg").hidden = true; $("critNote").className = "bubble muted"; $("critNote").textContent = "No plot reviewed yet.";
  $("curFill").style.width = "0"; $("curVal").textContent = "–"; $("metrics").innerHTML = "";
  document.querySelectorAll(".agent").forEach((a) => a.classList.remove("active"));
}
function buildWorkers(n) {
  R.nWorkers = n; const W = $("workers"); W.innerHTML = "";
  W.style.gridTemplateColumns = n > 4 ? "1fr 1fr" : "1fr"; W.classList.toggle("few", n <= 4);
  for (let i = 0; i < n; i++) {
    const d = el("div", "worker"); d.id = "w" + i; d.style.borderLeftColor = WCOL[i % 8];
    d.innerHTML = `<div class="wh"><span style="color:${WCOL[i % 8]}">Worker ${i + 1}</span><span class="badge">waiting</span></div>
      <div class="focus muted">generic direction (architect still thinking)</div><div class="rat"></div>`;
    W.append(d);
  }
}
const nPassPts = (c) => (c.points || []).filter((p) => p.pass).length;
function better(a, b) { // is a better than b
  if (!b) return true;
  if (a.passed !== b.passed) return a.passed;
  if (a.passed) return (a.worst?.i_core_a ?? 1) < (b.worst?.i_core_a ?? 1);
  const pa = nPassPts(a), pb = nPassPts(b);
  return pa !== pb ? pa > pb : (a.n_failures ?? 99) < (b.n_failures ?? 99);
}
function showBest() {
  const b = R.best; if (!b) return;
  const np = nPassPts(b), N = $("nPass");
  if (N.textContent !== String(np)) { N.textContent = np; N.classList.remove("bump"); void N.offsetWidth; N.classList.add("bump"); }
  N.classList.toggle("all", np === 15 && b.passed);
  $("bestLabel").textContent = `best design so far: #${b.n}` + (b.worker && b.worker !== "start" ? ` by worker ${+b.worker.slice(1) + 1}` : " (published starting point)");
  for (const p of b.points || []) {
    const t = $(`tile_${p.corner}_${p.temp}`); if (!t) continue;
    t.className = "tile " + (p.pass ? "pass" : "fail");
    const words = [...new Set((p.fails || []).map((f) => f.startsWith("sat") ? "headroom" : (FAILWORD[f] || f)))];
    t.innerHTML = p.pass ? "" : words.slice(0, 3).join("<br>");
    t.title = (p.fails || []).join(", ");
  }
  const w = b.worst || {}, tg = R.target;
  if (w.i_core_a != null && tg.i_core_a) {
    const lim = tg.i_core_a[1], ok = w.i_core_a <= lim;
    $("curFill").style.width = Math.min(100, (w.i_core_a / lim) * 66.6) + "%"; $("curFill").classList.toggle("ok", ok);
    $("curVal").innerHTML = `${(w.i_core_a * 1e6).toFixed(1)} µA <small>target ≤ ${(lim * 1e6).toFixed(0)} µA</small>`;
    $("curLimitLabel").textContent = `limit ${(lim * 1e6).toFixed(0)} µA`;
  }
  $("metrics").innerHTML = ["pm_deg", "ugf_hz", "dc_gain_db"].map((k) => {
    if (w[k] == null || !tg[k]) return "";
    const ok = w[k] >= tg[k][1];
    return `<div class="metric ${ok ? "ok" : "no"}" title="worst of the 15 corners">${SPEC[k][0]} <small>${SPEC[k][1]}</small><b>${fmt(k, w[k])}${SPEC[k][3] === "°" ? "°" : " " + SPEC[k][3]} <small>need&nbsp;${fmt(k, tg[k][1])}</small></b></div>`;
  }).join("");
  document.querySelectorAll("#dots .dot.best").forEach((d) => d.classList.remove("best"));
  $("dot" + b.n)?.classList.add("best");
}
function llmEstimate() { return R.plans + R.critics + R.rounds * R.nWorkers; }
function onEvent(e) {
  if (e.t != null) { R.t = e.t; R.tWall = performance.now(); }
  switch (e.type) {
    case "start":
      R.spec = e.spec || R.spec; R.target = e.target || guard(R.spec); buildWorkers(e.workers || 8); showBest();
      if (e.circuit === "live") e.circuit = (S.roles && S.roles.circuit) || "live";
      $("runInfo").innerHTML = `<b>${esc(R.id)}</b> · ${{ diff: "two-stage diff op-amp", fc: "folded-cascode op-amp", tele: "telescopic op-amp", live: "circuit from your photo" }[e.circuit] || e.circuit}${R.live && S.roles && !S.roles.fallback ? " (knobs from your photo)" : ""} · ${(e.knobs || []).length} knobs · ${e.workers} workers` +
        (R.live ? " · <b style='color:var(--bad)'>LIVE</b>" : e.synth ? " · recorded (timing reconstructed)" : " · recorded");
      $("judgeState").textContent = "simulating…";
      break;
    case "plan_requested":
      R.planT = e.t; R.plans++; $("architect").classList.add("active"); break;
    case "plan_ready":
      R.planT = null; $("archState").textContent = (!e.model || e.model === "none") ? `no new plan (timed out after ${(+e.latency_s).toFixed(0)} s) — workers keep the last one` : `plan ready in ${(+e.latency_s).toFixed(0)} s`;
      if (!e.model || e.model === "none") $("architect").classList.remove("active"); break;
    case "plan_applied": {
      $("architect").classList.remove("active");
      const B = $("archBubble"); B.className = "bubble big-bubble fresh"; B.textContent = clip(e.diagnosis, 560); B.title = e.diagnosis || "";
      $("archBinding").innerHTML = (e.binding ? `Hardest requirement right now: <b>${esc(clip(e.binding, 90))}</b>` : "") + (e.likely_infeasible ? " · <b>architect thinks the spec may be infeasible</b>" : "");
      (e.directions || []).forEach((d, i) => { const w = $("w" + i); if (w) { const f = w.querySelector(".focus"); f.className = "focus"; f.textContent = d.focus; f.title = (d.knobs || []).join(", "); w.classList.remove("ping"); void w.offsetWidth; w.classList.add("ping"); } });
      break;
    }
    case "round_start":
      R.rounds++; $("judgeState").textContent = `round ${e.iter} · simulating…`;
      document.querySelectorAll("#workers .worker .badge").forEach((b) => { if (!b.classList.contains("pass")) { b.className = "badge"; b.textContent = "proposing…"; } });
      break;
    case "candidate": {
      R.cands.push(e);
      const wi = /^w\d+$/.test(e.worker) ? +e.worker.slice(1) : -1, col = wi >= 0 ? WCOL[wi % 8] : "#8a8984";
      const d = el("div", "dot"); d.id = "dot" + e.n; d.style.borderColor = col; if (e.passed) d.style.background = col;
      d.title = `#${e.n} ${e.worker}: ${nPassPts(e)}/15`; $("dots").append(d);
      const w = $("w" + wi);
      if (w) {
        const b = w.querySelector(".badge"), np = nPassPts(e);
        b.className = "badge " + (e.passed ? "pass" : "fail"); b.textContent = e.passed ? `#${e.n} passes all 15` : `#${e.n}: ${np}/15`;
        w.querySelector(".rat").textContent = clip(e.rationale, 230); w.title = e.rationale || "";
        w.classList.remove("ping"); void w.offsetWidth; w.classList.add("ping");
      }
      $("judgeState").textContent = `${R.cands.length} designs judged · ${R.cands.length * 15} simulations`;
      if (better(e, R.best)) { R.best = e; showBest(); if (e.passed && w) { document.querySelectorAll("#workers .worker.won").forEach((x) => x.classList.remove("won")); w.classList.add("won"); } }
      if (e.passed && !R.firstPass) {
        R.firstPass = e;
        banner("ALL 15 CORNERS PASS", `candidate #${e.n} · ${Math.round(e.t)} s · ~${llmEstimate()} LLM calls`, false, 5200);
      }
      break;
    }
    case "critic":
      R.critics++; $("critState").textContent = `looked at design #${e.n} · ${(+e.latency_s || 0).toFixed(0)} s`;
      if (e.image) { $("critImg").src = `/api/runfile?run=${encodeURIComponent(R.id)}&name=${encodeURIComponent(e.image)}`; $("critImg").hidden = false; }
      $("critNote").className = "bubble fresh"; $("critNote").textContent = clip(e.note, 330); $("critNote").title = e.note || "";
      break;
    case "done":
      R.done = true; $("judgeState").textContent = `finished · ${R.cands.length} designs · ${R.cands.length * 15} simulations`;
      if (R.planT != null) $("archState").textContent = "run ended before the plan arrived"; $("architect").classList.remove("active"); R.planT = null;
      if (e.success) banner("ALL 15 CORNERS PASS", `first pass at candidate #${e.candidates_to_first_pass} · best design draws ${(+e.best_i_core_uA).toFixed(1)} µA · ${e.llm_calls} LLM calls · ${Math.round(e.wall_s)} s` + (e.recheck_full_lib ? " · re-checked on the full model library ✓" : ""), false, R.firstPass ? 0 : 5200);
      else banner("No design passed all 15 corners", `${R.cands.length} designs tried · best got ${R.best ? nPassPts(R.best) : 0}/15 · ${e.llm_calls ?? "?"} LLM calls · ${Math.round(e.wall_s || R.t)} s`, true, 4000);
      break;
  }
}
function banner(main, sub, fail, dockAfter) {
  const B = $("banner"); $("bannerMain").textContent = main; $("bannerSub").textContent = sub;
  B.className = "banner" + (fail ? " failb" : "") + (dockAfter === 0 ? " dock" : ""); B.hidden = false;
  clearTimeout(banner.h); if (dockAfter) banner.h = setTimeout(() => B.classList.add("dock"), dockAfter);
}
setInterval(() => { // layout clock
  if (!L || !L.id) return;
  $("layClock").textContent = Math.round(L.done ? L.t : L.t + Math.min((performance.now() - L.tWall) / 1000 * L.speed, L.live ? 1e9 : 30 * L.speed)) + " s";
}, 250);
setInterval(() => { // clock + architect "thinking" timer
  if (!R || !R.id) return;
  const now = R.done ? R.t : R.t + Math.min((performance.now() - R.tWall) / 1000 * R.speed, R.live ? 1e9 : 30 * R.speed);
  $("clock").textContent = Math.round(now) + " s";
  if (R.planT != null) $("archState").textContent = `thinking… ${Math.max(0, Math.round(now - R.planT))} s`;
}, 250);

function connect(runId, opts) {
  if (S.es) { S.es.close(); S.es = null; }
  resetStage(); R.id = runId; R.live = !opts.replay; R.speed = opts.replay ? opts.speed : 1; S.run = runId;
  $("runInfo").innerHTML = `<b>${esc(runId)}</b> · ` + (opts.replay ? "loading recording…" : "starting: loading models, first simulation in ~10 s…");
  const url = `/api/events?run=${encodeURIComponent(runId)}` + (opts.replay ? `&replay=1&speed=${opts.speed}` : "");
  const es = (S.es = new EventSource(url));
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (err) { console.error(err); } };
  es.addEventListener("end", (m) => { es.close(); S.es = null; const d = JSON.parse(m.data); if (d.exit) { toast("run stopped: " + d.reason + (d.log ? " — " + clip(d.log, 300) : ""), 15000); } $("btnRun").disabled = false; });
  es.addEventListener("fail", (m) => { es.close(); S.es = null; toast(JSON.parse(m.data).error, 8000); $("btnRun").disabled = false; });
  es.onerror = () => { if (R.done) { es.close(); S.es = null; } };
}
$("btnRun").onclick = async () => {
  if (S.es && R && R.live && !R.done) return;
  const circuit = (S.roles && ["diff", "fc", "tele"].includes(S.roles.circuit) && S.roles.circuit) || ($("selCircuit").value !== "auto" ? $("selCircuit").value : "diff");
  $("btnRun").disabled = true;
  try {
    const r = await jpost("/api/run", JSON.stringify({ circuit, spec: S.spec?.spec || null, roles_session: S.roles && !S.roles.fallback ? S.roles.session : null, spec_session: S.spec && !S.spec.fallback ? S.spec.session : null }), { "Content-Type": "application/json" });
    if (r.error) throw new Error(r.error);
    connect(r.run_id, { replay: false });
  } catch (err) { toast("could not start: " + err, 8000); $("btnRun").disabled = false; }
};
$("btnReplay").onclick = () => { const id = $("selReplay").value; if (id) connect(id, { replay: true, speed: +$("selSpeed").value }); };
async function loadRuns() {
  try {
    const { runs } = await (await fetch("/api/runs")).json();
    $("selReplay").innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(r.id)}${r.done ? (r.success ? " ✓" : " ✗") : " (running)"}</option>`).join("");
    const want = QS.get("replay"); if (want && runs.some((r) => r.id === want)) $("selReplay").value = want;
  } catch (err) { /* offline */ }
}

/* ------------------------------------------------------------------ step 4: layout crew */
let L = null;
const num = (v) => v == null || isNaN(v) ? "–" : Math.abs(v) >= 100 ? (+v).toFixed(0) : Math.abs(v) >= 10 ? (+v).toFixed(1) : (+v).toFixed(2);
const layImg = (name) => `/api/runfile?kind=layout&run=${encodeURIComponent(L.id)}&name=${encodeURIComponent(name)}`;
function setImg(img, src) {
  if (!src || img.dataset.src === src) return;
  img.dataset.src = src; img.onerror = () => { img.hidden = true; };
  const pre = new Image();
  pre.onload = () => { img.classList.add("swap"); setTimeout(() => { img.src = src; img.hidden = false; img.classList.remove("swap"); }, img.hidden ? 0 : 350); };
  pre.onerror = () => { if (!img.getAttribute("src")) img.hidden = true; };
  pre.src = src;
}
function resetLayout() {
  L = { id: null, cands: [], best: null, naive: null, t: 0, tWall: performance.now(), speed: 1, live: false, done: false, nW: 0 };
  ["layArea0", "layArea1", "layOff0", "layOff1"].forEach((i) => { $(i).textContent = "–"; $(i).className = ""; });
  $("layAreaPct").textContent = ""; $("layOffPct").textContent = "";
  $("layBadge").className = "laybadge wait"; $("layBadge").textContent = "waiting for the first floorplan";
  for (const i of ["layNaive", "layBest", "layCritImg"]) { $(i).hidden = true; $(i).removeAttribute("src"); delete $(i).dataset.src; }
  $("layBestCap").textContent = "After: the crew's best so far"; $("layDots").innerHTML = ""; $("layWorkers").innerHTML = "";
  $("layCritNote").className = "bubble muted"; $("layCritNote").textContent = "No floorplan reviewed yet."; $("layCritState").textContent = "";
  layWorkers(4);
}
function layWorkers(n) {
  const W = $("layWorkers");
  for (let i = L.nW; i < n; i++) {
    const d = el("div", "worker"); d.id = "lw" + i; d.style.borderLeftColor = WCOL[i % 8];
    d.innerHTML = `<div class="wh"><span style="color:${WCOL[i % 8]}">Worker ${i + 1}</span><span class="badge">waiting</span></div><div class="plan"></div><div class="rat"></div>`;
    W.append(d);
  }
  L.nW = Math.max(L.nW, n); W.style.gridTemplateColumns = L.nW > 4 ? "1fr 1fr" : "1fr 1fr";
}
function planText(p) {
  if (!p || typeof p !== "object") return p ? String(p) : "";
  const one = (v) => v && typeof v === "object" ? (Array.isArray(v) ? v.join(",") : Object.entries(v).map(([a, b]) => `${a}:${typeof b === "object" ? JSON.stringify(b) : b}`).join(" ")) : String(v);
  const name = { nf: "fingers", pattern: "interleave", dummies: "dummies", rows: "rows" };
  return Object.entries(p).map(([k, v]) => `${name[k] || k}  ${one(v)}`).join("\n");
}
function layBetter(a, b) {
  if (!b) return true;
  if (!!a.passed !== !!b.passed) return !!a.passed;
  if (a.cost != null && b.cost != null) return a.cost < b.cost;
  return (a.area_um2 ?? 1e99) < (b.area_um2 ?? 1e99);
}
function layShow(final) {
  const nv = L.naive || {}, b = final || L.best;
  $("layArea0").textContent = num(final?.naive_area_um2 ?? nv.area_um2); $("layOff0").textContent = num(final?.naive_offset_uV ?? nv.offset_uV);
  if (!b) return;
  const a0 = final?.naive_area_um2 ?? nv.area_um2, o0 = final?.naive_offset_uV ?? nv.offset_uV;
  $("layArea1").textContent = num(b.area_um2); $("layOff1").textContent = num(b.offset_uV);
  if (a0 != null && b.area_um2 != null) {
    const pct = final?.area_reduction_pct ?? (1 - b.area_um2 / a0) * 100;
    $("layArea1").className = pct > 0 ? "better" : pct < 0 ? "worse" : ""; $("layAreaPct").textContent = pct >= 0 ? `${num(pct)}% smaller` : `${num(-pct)}% larger`;
  }
  if (o0 != null && b.offset_uV != null) {
    const d = Math.abs(o0) - Math.abs(b.offset_uV);
    $("layOff1").className = d > 0 ? "better" : d < 0 ? "worse" : "";
    $("layOffPct").textContent = Math.abs(o0) > 0 && d > 0 ? (Math.abs(b.offset_uV) < 0.005 ? "systematic mismatch cancelled" : `${num(Math.abs(o0) / Math.abs(b.offset_uV))}× less mismatch`) : d < 0 ? "more mismatch than the naive layout" : "";
  }
  const B = $("layBadge");
  if (b.passed) { B.className = "laybadge"; B.textContent = "✓ still passes all 15 corners with layout parasitics"; }
  else { B.className = "laybadge no"; B.textContent = "✗ fails some corners once layout parasitics are added"; }
}
function onLayoutEvent(e) {
  if (e.t != null) { L.t = e.t; L.tWall = performance.now(); }
  switch (e.type) {
    case "start":
      L.naive = e.naive || {};
      $("layInfo").innerHTML = `<b>${esc(L.id)}</b> · laying out the design from ${esc(String(e.design_from || "the sizing run").split("/").pop())} · up to ${e.max_candidates ?? "?"} floorplans` + (L.live ? " · <b style='color:var(--bad)'>LIVE</b>" : " · recorded");
      setImg($("layNaive"), layImg(L.naive.image || "naive.png")); layShow();
      break;
    case "candidate": {
      L.cands.push(e);
      const wi = /^w\d+$/.test(e.worker) ? +e.worker.slice(1) : -1, col = wi >= 0 ? WCOL[wi % 8] : "#8a8984";
      if (wi >= L.nW) layWorkers(wi + 1);
      const d = el("div", "dot"); d.id = "ldot" + e.n; d.style.borderColor = col; if (e.passed) d.style.background = col;
      d.title = `#${e.n}: ${num(e.area_um2)} µm², ${num(e.offset_uV)} µV`; $("layDots").append(d);
      const w = $("lw" + wi);
      if (w) {
        const b = w.querySelector(".badge"); b.className = "badge " + (e.passed ? "pass" : "fail");
        b.textContent = `#${e.n}: ${num(e.area_um2)} µm²` + (e.passed ? " ✓ passes" : " ✗ fails");
        w.querySelector(".plan").textContent = planText(e.plan) + (!e.passed && (e.violations || []).length ? "\nfails: " + clip(e.violations.join(", "), 60) : ""); w.querySelector(".rat").textContent = clip(e.rationale, 200); w.title = e.rationale || "";
        w.classList.remove("ping"); void w.offsetWidth; w.classList.add("ping");
      }
      if (layBetter(e, L.best)) {
        L.best = e; layShow();
        $("layBestCap").textContent = `After: the crew's best so far — floorplan #${e.n}` + (wi >= 0 ? ` by layout worker ${wi + 1}` : "");
        if (e.image) setImg($("layBest"), layImg(e.image));
        document.querySelectorAll("#layDots .dot.best").forEach((x) => x.classList.remove("best")); d.classList.add("best");
        document.querySelectorAll("#layWorkers .worker.won").forEach((x) => x.classList.remove("won")); if (w && e.passed) w.classList.add("won");
      }
      break;
    }
    case "critic":
      $("layCritState").textContent = `looked at floorplan #${e.n}` + (e.latency_s != null ? ` · ${(+e.latency_s).toFixed(0)} s` : "");
      if (e.image) setImg($("layCritImg"), layImg(e.image));
      $("layCritNote").className = "bubble fresh"; $("layCritNote").textContent = clip(e.note, 420); $("layCritNote").title = e.note || "";
      break;
    case "done": {
      L.done = true;
      const b = L.cands.find((c) => c.n === e.best_n);
      if (b) { L.best = b; $("layBestCap").textContent = `After: the crew's final floorplan (#${b.n})`; }
      layShow({ ...(b || {}), ...e });
      setImg($("layBest"), layImg((b && b.image) || "best.png"));
      $("layInfo").innerHTML += ` · finished: ${e.candidates ?? L.cands.length} floorplans, ${e.llm_calls ?? "?"} LLM calls, ${Math.round(e.wall_s || L.t)} s`;
      break;
    }
  }
}
function connectLayout(runId, opts) {
  if (S.layES) { S.layES.close(); S.layES = null; }
  resetLayout(); L.id = runId; L.live = !opts.replay; L.speed = opts.replay ? opts.speed : 1;
  $("layInfo").innerHTML = `<b>${esc(runId)}</b> · ` + (opts.replay ? "loading recording…" : "starting the layout crew…");
  const es = (S.layES = new EventSource(`/api/events?kind=layout&run=${encodeURIComponent(runId)}` + (opts.replay ? `&replay=1&speed=${opts.speed}` : "")));
  es.onmessage = (m) => { try { onLayoutEvent(JSON.parse(m.data)); } catch (err) { console.error(err); } };
  es.addEventListener("end", (m) => { es.close(); S.layES = null; const d = JSON.parse(m.data); if (d.exit) toast("layout run stopped: " + d.reason + (d.log ? " — " + clip(d.log, 300) : ""), 15000); $("btnLayout").disabled = false; });
  es.addEventListener("fail", (m) => { es.close(); S.layES = null; toast(JSON.parse(m.data).error, 8000); $("layInfo").textContent = "no recording for " + runId; $("btnLayout").disabled = false; });
  es.onerror = () => { if (L.done) { es.close(); S.layES = null; } };
}
$("btnLayout").onclick = async () => {
  if (S.layES && L && L.live && !L.done) return;
  const run = S.run || $("selReplay").value;
  $("btnLayout").disabled = true;
  try {
    const r = await jpost("/api/layout", JSON.stringify({ run }), { "Content-Type": "application/json" });
    if (r.error) throw new Error(r.error);
    connectLayout(r.run_id, { replay: false });
  } catch (err) { toast("could not start layout: " + (err.message || err), 9000); $("btnLayout").disabled = false; }
};
$("btnLayReplay").onclick = () => { const id = $("selLayReplay").value; if (id) connectLayout(id, { replay: true, speed: +$("selSpeed").value }); };
async function loadLayoutRuns() {
  try {
    const { runs } = await (await fetch("/api/runs?kind=layout")).json(), sel = $("selLayReplay"), cur = sel.value;
    sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(r.id)}${r.done ? (r.success ? " ✓" : " ✗") : " (running)"}</option>`).join("") || "<option value=''>no layout run recorded yet</option>";
    if (cur && runs.some((r) => r.id === cur)) sel.value = cur;
  } catch (err) { /* offline */ }
}

/* ------------------------------------------------------------------ step 5 + 6 */
function traceLine(s) {
  const T = $("signTrace"), d = el("div", /agent ->/.test(s) ? "tool" : /\[run_spice/.test(s) ? "res" : "");
  d.textContent = s; T.append(d); T.scrollTop = T.scrollHeight;
}
function renderSignoff(c) {
  const R4 = $("signResult");
  if (!c) { R4.innerHTML = '<span class="muted">No comparison yet (agent still running or it stopped early).</span>'; return; }
  const cal = c.calibration || {};
  const chk = (ok, big, txt) => `<div class="check ${ok ? "" : "no"}"><b>${ok ? "✓" : "✗"} ${big}</b>${txt}</div>`;
  R4.innerHTML = `<div class="checks">` +
    chk(cal.matches_published, "Calibration", `its own testbench reproduces the published numbers (${(+cal.dc_gain_db).toFixed(1)} dB, ${(cal.ugf_hz / 1e6).toFixed(0)} MHz, ${(+cal.pm_deg).toFixed(1)}°)`) +
    chk(c.points_matched === c.points_total, `${c.points_matched}/${c.points_total} corners agree`, `with our Judge (largest gap: ${(+c.max_abs_dpm_deg).toFixed(2)}° stability, ${(c.max_rel_dugf * 100).toFixed(2)}% speed)`) +
    chk(c.verdict_agrees, c.agent_pass_target ? "Signed off" : "Not signed off", `agent's verdict ${c.verdict_agrees ? "matches" : "DISAGREES with"} ours · ${c.spice_calls} SPICE calls, ${c.decks} decks it wrote itself`) +
    chk(true, `${Math.round(c.wall_s / 60)} min`, `agent session · ${c.turns} turns · ${Math.round((c.tokens_last || 0) / 1000)}k tokens`) +
    `</div><div class="quote">${esc(clip(c.agent_verdict, 420))}</div>`;
}
function watchSignoff(name) {
  if (S.signES) S.signES.close();
  $("signTrace").innerHTML = ""; $("signResult").innerHTML = "";
  const es = (S.signES = new EventSource("/api/signoff_events?name=" + encodeURIComponent(name)));
  es.onmessage = (m) => { const d = JSON.parse(m.data); if (d.line) traceLine(d.line); };
  es.addEventListener("end", (m) => { es.close(); renderSignoff(JSON.parse(m.data).comparison); });
  es.addEventListener("fail", (m) => { es.close(); traceLine(JSON.parse(m.data).error); });
  es.onerror = () => es.close();
}
$("btnSignoff").onclick = async () => {
  const run = S.run || $("selReplay").value;
  const r = await jpost("/api/signoff", JSON.stringify({ run }), { "Content-Type": "application/json" });
  if (r.error) return toast(r.error, 7000);
  traceLine("launching independent sign-off agent for " + run + " (takes 5-15 min)…"); watchSignoff(r.name);
};
$("btnSignoffShow").onclick = () => { if ($("selSignoff").value) watchSignoff($("selSignoff").value); };

async function loadProof() {
  let P; try { P = await (await fetch("/api/proof")).json(); } catch (err) { return; }
  // sign-off list
  const names = Object.keys(P.signoff || {}), sel = $("selSignoff"), cur = sel.value;
  sel.innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join("") || "<option value=''>no recorded sign-off yet</option>";
  if (cur && names.includes(cur)) sel.value = cur;
  // red team
  const rt = Object.entries(P.redteam || {}), RT = $("redteam");
  if (!rt.length) RT.innerHTML = '<span class="muted">No red-team session recorded yet.</span>';
  else {
    const [name, r] = rt[rt.length - 1];
    if (r.running) RT.innerHTML = `<div class="headline muted">Red team “${esc(name)}” is still attacking…</div><div class="sub">${r.tool_calls} tool calls so far. Findings appear here when it reports.</div>`;
    else {
      const rank = { high: 0, medium: 1, low: 2 }, fs = [...(r.findings || [])].sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3));
      const s = r.summary || {};
      RT.innerHTML = `<div class="sub"><b>${fs.length} findings</b>${s.confirmed != null ? `, ${s.confirmed} confirmed with a reproducing test` : ""}. We show them all — these are the holes in our own Judge.</div>` +
        fs.slice(0, 5).map((f) => `<div class="finding ${esc(f.severity)}"><span class="sev">${esc(f.severity || "?")}${f.confirmed ? " · confirmed" : ""}</span><b>${esc(f.id ? f.id + ": " : "")}${esc(clip(f.title, 110))}</b><p>${esc(clip(f.description || f.evidence || f.impact || f.summary || "", 300))}</p></div>`).join("");
    }
  }
  // proof table
  const PF = $("proof"); PF.innerHTML = "";
  for (const g of P.groups || []) {
    const d = el("div", "pgroup");
    d.innerHTML = `<h3>${esc(g.title)}</h3><table><tr><th>method</th><th>seeds that passed</th><th>designs tried before first pass</th><th>lowest current</th></tr>` +
      g.rows.map((r) => {
        const pips = "<span class='pips'>" + "<i class='y'></i>".repeat(r.passed) + "<i></i>".repeat(r.n - r.passed) + "<i class='r'></i>".repeat(r.running) + "</span>";
        const cls = r.n === 0 ? "" : r.passed === r.n ? "all" : r.passed === 0 ? "none" : "some";
        return `<tr class="${r.is_crew ? "crew" : ""}"><td>${esc(r.method)}</td><td class="k ${cls}">${r.n ? r.passed + " / " + r.n : "–"}${pips}${r.running ? `<small> ${r.running} running</small>` : ""}</td>` +
          `<td>${r.median_first_pass != null ? r.median_first_pass + " <small>(median, of " + (r.budget[0] || "?") + ")</small>" : r.n ? "<small>never, in " + (r.budget.join("/") || "?") + "</small>" : "–"}</td>` +
          `<td>${r.best_current_uA != null ? r.best_current_uA + " µA" : "–"}</td></tr>`;
      }).join("") + "</table>";
    PF.append(d);
  }
  const it = P.intake;
  if (it && it.with_image.n) {
    const line = `Role labeling, all 3 circuits: <b>${it.with_image.n_correct}/${it.with_image.n}</b> transistors correct from the schematic image (netlist text only: ${it.netlist_only.n_correct}/${it.netlist_only.n}).`;
    S.intakeLine = line; if (!S.roles) $("roleSub").innerHTML = "Measured earlier — " + line;
  }
  const c = P.cost;
  $("cost").innerHTML = (S.intakeLine ? S.intakeLine + "<br>" : "") + (!c ? "" : `<b>Cost of one crew run</b> (median of ${c.runs} recorded runs): <b>${c.median_llm_calls}</b> LLM calls, <b>~${Math.round((c.median_tokens || 0) / 1000)}k</b> tokens, <b>${(c.median_wall_s / 60).toFixed(1)} min</b> wall-clock, including the SPICE simulations (15 per design tried).`);
}

/* ------------------------------------------------------------------ boot */
resetStage(); resetLayout(); renderSpec(null, true);
if (QS.get("replay_layout")) { go(4); connectLayout(QS.get("replay_layout"), { replay: true, speed: +(QS.get("speed") || 8) }); }
loadRuns().then(() => {
  const rp = QS.get("replay");
  if (rp) { go(3); const sp = +(QS.get("speed") || 8); if (![...$("selSpeed").options].some((o) => o.value === String(sp))) $("selSpeed").append(new Option(sp + "x", String(sp))); $("selSpeed").value = String(sp); connect(rp, { replay: true, speed: sp }); }
});
go(+(QS.get("step") || (QS.get("replay_layout") ? 4 : QS.get("replay") ? 3 : 1)));
loadProof();
