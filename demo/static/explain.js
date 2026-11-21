"use strict";
/* Interpretability + conversation layer: part explorer, decision trace, ask the crew, change requests,
   glossary, PCB boards.  Loaded after app.js; uses its globals ($, el, esc, clip, ROLE, SPEC, R, L, S…). */
const PARTCOL = Object.assign({ comp: "#55606e", bias: "#8a6d3b" }, Object.fromEntries(Object.entries(ROLE).map(([k, v]) => [k, v[0]])));
const PARTNAME = { in: "Input pair", tail: "Tail source", load: "Mirror load", drv: "Stage-2 driver", sink: "Stage-2 sink", psrc: "P current source",
  pcas: "P cascode", ncas: "N cascode", nsink: "N current sink", comp: "Compensation", bias: "Bias current" };
const GLOSS = {
  pm: ["Phase margin", "How far the amplifier is from oscillating: 60° or more means it settles cleanly instead of ringing."],
  ugf: ["Unity-gain frequency", "The frequency where the amplifier's gain has dropped to 1: its speed limit."],
  gain: ["DC gain", "How much the amplifier multiplies a slow signal: more gain means a more accurate output."],
  current: ["Core current", "The supply current the amplifier itself draws: lower means less power."],
  corner: ["Corner", "One combination of manufacturing variation (tt typical, ff fast, ss slow, sf and fs skewed) and temperature (−40, 27, 125 °C); a design must work at all 15."],
  sat: ["Saturation (headroom)", "A transistor is in saturation when it has enough voltage across it to amplify; the margin is how many millivolts it has to spare."],
  guard: ["Guard band", "A safety margin added on top of the spec, so the design still passes if the simulator is slightly off."],
  knob: ["Tuning knob", "One size the crew may change: a transistor's width or length, the bias current, or the compensation capacitor and resistor."],
  cand: ["Design (candidate)", "One complete set of knob values proposed by a worker; the Judge simulates each one at all 15 corners."],
  judge: ["The Judge", "The ngspice circuit simulator: it measures every design and has no opinions."],
  comp: ["Miller compensation", "A capacitor (Cc) and resistor (Rz) between the two stages that trade speed for stability."],
  clean: ["Clean pass", "A routed board with zero open connections and zero design-rule errors."],
  ripup: ["Rip-up", "Removing tracks that are already routed but in the way, so blocked connections can get through, then routing the removed ones again."],
  drc: ["Rule error (DRC)", "A place where the board breaks a manufacturing rule, such as two tracks too close together."],
  unrouted: ["Open connection", "Two pads that should be wired together but are not yet."],
  abba: ["ABBA interleaving", "Splitting two matched transistors into fingers placed A-B-B-A so manufacturing gradients cancel out."],
  offset: ["Input offset", "A built-in error voltage caused by the two halves of the input pair not being identical."],
};
const TERM_RX = [
  [/phase margin/i, "pm"], [/unity-gain frequency/i, "ugf"], [/DC gain/, "gain"], [/core (supply )?current/i, "current"],
  [/guard[- ]band(ed)?|safety margin/i, "guard"], [/saturation|headroom/i, "sat"], [/process corners?|corners?(?= pass)|15 corners|\bcorners?\b/i, "corner"],
  [/tuning knobs?|\bknobs?\b/i, "knob"], [/clean pass|\bclean\b/i, "clean"], [/rip(ped)?[- ]?ups?|ripped up/i, "ripup"],
  [/rule errors?|\bDRC\b/, "drc"], [/open connections?|unrouted/i, "unrouted"], [/\bABBA\b/, "abba"], [/input offset|mismatch/i, "offset"],
  [/Miller|compensation/, "comp"],
];
const X = { view: null, run: null, n: null, ov: {}, askTab: "ask" };
const jget = async (u) => (await fetch(u)).json();
const curRun = () => (R && R.id) || S.run || QS.get("replay") || ($("selReplay") && $("selReplay").value) || "";
const upto = (run) => (R && R.id === run && !R.done && R.cands.length ? Math.max(...R.cands.map((c) => c.n)) : 0);
const partChip = (p, label) => `<button class="pchip" data-part="${esc(p)}" style="border-color:${PARTCOL[p] || "#777"}"><i style="background:${PARTCOL[p] || "#777"}"></i>${esc(label || PARTNAME[p] || p)}</button>`;
const candLink = (n, txt) => `<button class="clink" data-cand="${n}">${txt || "#" + n}</button>`;
const linkify = (s) => esc(s).replace(/#(\d{1,3})\b/g, (m, n) => candLink(+n)).replace(/\b(tt|ff|ss|sf|fs)\/(-?\d+)\b/g, '<b class="cornertag">$1/$2</b>');
const g4 = (v) => v == null ? "–" : String(+(+v).toPrecision(4));

/* ------------------------------------------------------------------ drawer */
function openDrawer(title, html, view) {
  X.view = view; $("drawerTitle").innerHTML = title; $("drawerBody").innerHTML = html;
  $("drawer").hidden = false; document.body.classList.add("drawer-open"); $("drawerBody").scrollTop = 0; markTerms($("drawerBody"));
}
function closeDrawer() { $("drawer").hidden = true; document.body.classList.remove("drawer-open"); X.view = null; hideTip(); }
$("drawerClose").onclick = closeDrawer;
const loading = (t) => openDrawer(t, '<p class="muted">loading the recorded data…</p>', "loading");

/* ------------------------------------------------------------------ 1. part explorer */
async function openPart(part, opts = {}) {
  const run = opts.run || curRun();
  loading(esc(PARTNAME[part] || part));
  const circuit = opts.circuit || (S.roles && S.roles.circuit) || "";
  let d;
  try { d = await jget(`/api/explain?part=${encodeURIComponent(part)}&run=${encodeURIComponent(run)}&upto=${upto(run)}&circuit=${encodeURIComponent(circuit)}`); }
  catch (err) { return openDrawer("Part", `<p class="warnbox">Could not load: ${esc(err)}</p>`, "part"); }
  if (d.error) return openDrawer("Part", `<p class="warnbox">${esc(d.error)}</p>`, "part");
  const col = PARTCOL[part] || "#777";
  let h = `<p class="lead">${esc(d.what)}</p><p>${esc(d.how || "")}</p>`;
  if (d.devices.length) h += `<div class="kv"><span>Transistors</span><b>${d.devices.map((x) => `<code>${esc(x.label)}</code>`).join(" ")}</b></div>`;
  h += `<figure class="schem"><img src="/api/schematic?circuit=${esc(d.circuit)}" alt="schematic" onerror="this.parentElement.hidden=true"><figcaption>${esc(d.circuit_name)}: find ${d.devices.map((x) => esc(x.label)).join(", ") || "Cc / Rz / Ibias"} on the schematic</figcaption></figure>`;
  h += `<h4>Tuning knobs that control it</h4>`;
  if (d.knobs.length) {
    h += `<table class="xt"><tr><th>knob</th><th>as published</th><th>${d.as_of ? "now (best design " + candLink(d.as_of.best_n) + ")" : "now"}</th><th>change</th></tr>` +
      d.knobs.map((k) => `<tr><td><code>${esc(k.knob)}</code></td><td>${g4(k.published)} ${k.unit}</td><td><b>${k.now == null ? "–" : g4(k.now) + " " + k.unit}</b></td>` +
        `<td>${k.ratio == null ? "–" : k.ratio === 1 ? "unchanged" : k.ratio > 1 ? "×" + k.ratio + " larger" : "×" + (1 / k.ratio).toFixed(1) + " smaller"}</td></tr>`).join("") + `</table>` +
      `<p class="src">allowed range: ${d.knobs.map((k) => `${esc(k.knob)} ${g4(k.lo)}–${g4(k.hi)} ${k.unit}`).join(" · ")}</p>`;
  } else h += `<p class="muted">No knob of its own in this circuit: its size is fixed by the frozen bias network.</p>`;
  if (d.sat) {
    h += `<h4>Worst saturation margin</h4><div class="satbox ${d.sat.ok ? "ok" : "no"}"><b>${d.sat.worst_mv} mV</b> at corner <b class="cornertag">${esc(d.sat.at)}</b>
      <span>needs ≥ ${d.sat.required_mv} mV ${d.sat.ok ? "✓" : "✗"}</span></div>
      <p class="src">${d.sat.published_mv != null ? `Published design: ${d.sat.published_mv} mV at ${esc(d.sat.published_at)}. ` : ""}Source: ${esc(d.sat.source)}. ${candLink(d.sat.n, "Open design #" + d.sat.n)}</p>`;
  } else if (d.sat_required_mv != null) h += `<p class="src">The Judge requires ≥ ${d.sat_required_mv} mV of saturation margin on this part at every corner (plus the guard band).</p>`;
  if (d.as_of) {
    h += `<h4>Why it changed</h4>`;
    if (!d.changes.length) h += `<p class="muted">No design has moved this part's knobs yet.</p>`;
    else h += `<p class="src">${d.n_changes} designs moved these knobs; ${d.n_improving} of them became the new best. The steps that stuck:</p>` +
      d.changes.map((c) => `<div class="why"><div class="whyh">${candLink(c.n)} <span>${esc(c.who)}, round ${c.iter}</span><span class="pill ${c.passed ? "ok" : "no"}">${c.pass_count}/15</span></div>
        <div class="whyd">${c.diff.map((x) => `<code>${esc(x.knob)}</code> ${g4(x.from)} → <b>${g4(x.to)}</b> ${x.unit}`).join(" · ")} <small>vs ${candLink(c.vs)}</small></div>
        <div class="whyr">“${linkify(c.rationale)}”</div></div>`).join("");
    if (d.architect.length) h += `<h4>What the architect said about it</h4>` + d.architect.map((a) => `<div class="quote2"><small>${esc(a.where)} · plan written after design #${a.after_n}</small>${linkify(a.text)}</div>`).join("");
    h += `<p class="src">As of ${d.as_of.n_candidates} designs judged in run <code>${esc(d.as_of.run)}</code>. All text above is recorded agent output; all numbers are ngspice results.</p>`;
  } else h += `<p class="warnbox">${esc(d.note || "")} Start or replay a crew run to see how the crew changed this part and why.</p>`;
  openDrawer(`<i class="dotc" style="background:${col}"></i>${esc(d.name)}`, h, "part");
}

/* ------------------------------------------------------------------ 2. decision trace */
const FW = (f) => f.startsWith("sat") ? "headroom" : (FAILWORD[f] || f);
function pointDetail(p, tg) {
  const row = (k, lo) => p[k] == null ? "" : `<tr class="${(p.fails || []).includes(k) ? "bad" : ""}"><td>${SPEC[k][0]} <small>${SPEC[k][1]}</small></td><td><b>${fmt(k, p[k])}${SPEC[k][3] === "°" ? "°" : " " + SPEC[k][3]}</b></td><td>${tg[k] != null ? (lo ? "≥ " : "≤ ") + fmt(k, tg[k]) : ""}</td></tr>`;
  const sat = Object.entries(p.sat_margin_v || {}).map(([r, v]) => `<span class="${(p.fails || []).includes("sat:" + r) ? "badt" : ""}">${esc(PARTNAME[r] || r)} ${(v * 1e3).toFixed(0)} mV</span>`).join(" · ");
  return `<div class="ptd"><b>Corner ${esc(p.corner)} at ${p.temp} °C: ${p.pass ? "passes" : "fails " + (p.fails || []).map(FW).join(", ")}</b>
    <table class="xt"><tr><th>measured</th><th></th><th>target</th></tr>${row("pm_deg", 1)}${row("ugf_hz", 1)}${row("dc_gain_db", 1)}${row("i_core_a", 0)}</table>
    ${sat ? `<p class="src">saturation margin per part: ${sat}</p>` : ""}</div>`;
}
async function openCand(n, run) {
  run = run || curRun(); n = +n;
  loading("Design #" + n);
  let d;
  try { d = await jget(`/api/explain?run=${encodeURIComponent(run)}&n=${n}`); } catch (err) { d = { error: String(err) }; }
  if (d.error) return openDrawer("Design #" + n, `<p class="warnbox">${esc(d.error)}</p>`, "cand");
  X.run = run; X.n = n; X.cand = d;
  const tg = d.target;
  let h = `<div class="verdict ${d.passed ? "ok" : "no"}">${d.passed ? "✓ passes all 15 corners" : `✗ ${d.pass_count} of 15 corners pass`}${d.is_best ? " · best design of the run" : d.became_best ? ` · became the new best${d.beat ? " (beat #" + d.beat + ")" : ""}` : ""}</div>`;
  h += `<h4>1 · Who proposed it</h4><p><b>${esc(d.who)}</b>${d.worker !== "start" ? `, round ${d.iter}` : ""}.</p>`;
  if (d.direction) h += `<div class="quote2"><small>its assignment from the architect (plan written after design #${d.direction.plan_after_n})</small><b>${esc(d.direction.focus)}</b>
    <details><summary>full instructions</summary>${linkify(d.direction.instructions)}</details></div>`;
  else if (d.direction_note) h += `<p class="src">${esc(d.direction_note)}</p>`;
  if (d.rationale) h += `<h4>2 · Its reason</h4><div class="whyr big">“${linkify(d.rationale)}”</div>`;
  h += `<h4>3 · What it changed</h4>`;
  if (d.parent) {
    h += `<p class="src">compared with ${candLink(d.parent.n)} (${d.parent.pass_count}/15), ${esc(d.parent.why)}: ${d.diff.length} of ${d.n_knobs} knobs moved</p>`;
    h += d.diff.length ? `<table class="xt">` + d.diff.map((x) => `<tr><td>${partChip(x.part, PARTNAME[x.part])}</td><td><code>${esc(x.knob)}</code></td><td>${g4(x.from)} → <b>${g4(x.to)}</b> ${x.unit}</td><td class="${x.ratio > 1 ? "up" : "down"}">${x.ratio > 1 ? "▲" : "▼"} ${Math.abs((x.ratio - 1) * 100).toFixed(0)}%</td></tr>`).join("") + `</table>` : `<p class="muted">identical knobs</p>`;
  } else h += `<p class="muted">This is the starting point: the sizes published with the benchmark circuit.</p>`;
  h += `<details><summary>all knob values, and the change from the published design</summary><table class="xt">` + Object.entries(d.display).map(([k, v]) => { const x = d.diff_published.find((y) => k.startsWith(y.knob + "_")); return `<tr><td><code>${esc(k)}</code></td><td><b>${g4(v)}</b></td><td>${x ? "published " + g4(x.from) : "as published"}</td></tr>`; }).join("") + `</table></details>`;
  h += `<h4>4 · What the Judge said</h4><div class="cgridwrap"><div class="cgrid"><div></div>${TEMPS.map((t) => `<div class="h">${t} °C</div>`).join("")}` +
    CORNERS.map((c) => `<div class="h">${c}</div>` + TEMPS.map((t) => { const p = d.points.find((q) => q.corner === c && q.temp === t); return p ? `<button class="ct ${p.pass ? "pass" : "fail"}" data-pt="${c}/${t}" title="click for this corner's numbers">${p.pass ? "✓" : [...new Set(p.fails.map(FW))].slice(0, 2).join("<br>")}</button>` : `<div class="ct"></div>`; }).join("")).join("") + `</div>
    <div class="worst">${["pm_deg", "ugf_hz", "dc_gain_db", "i_core_a"].map((k) => { if (d.worst[k] == null) return ""; const ok = k === "i_core_a" ? d.worst[k] <= tg[k] : d.worst[k] >= tg[k]; return `<div class="${ok ? "ok" : "no"}"><small>${SPEC[k][0]} · worst case</small><b>${fmt(k, d.worst[k])}${SPEC[k][3] === "°" ? "°" : " " + SPEC[k][3]}</b><small>at ${esc(d.worst_at[k] || "?")} · need ${k === "i_core_a" ? "≤" : "≥"} ${fmt(k, tg[k])}</small></div>`; }).join("")}</div></div>
    <div id="ptDetail"></div>`;
  if (d.fail_summary.length) h += `<ul class="fails">` + d.fail_summary.map((f) => `<li><b>${esc(f.label)}</b> failed at ${f.count} corner${f.count > 1 ? "s" : ""}${f.worst ? ": worst " + esc(f.worst) : ""}${f.part ? " " + partChip(f.part, "explore") : ""}</li>`).join("") + `</ul>`;
  h += `<h4>5 · What the crew did next</h4>`;
  h += d.became_best ? `<p>It became the best design so far${d.beat ? `, replacing ${candLink(d.beat)}` : ""}; later workers started from it.</p>` : d.worker === "start" ? "" : `<p>It did not beat the best design at the time, so the crew kept building on the previous best.</p>`;
  if (d.next_plan) h += `<div class="quote2"><small>the architect's next plan (after design #${d.next_plan.after_n})${d.next_plan.binding ? " · hardest requirement: " + esc(clip(d.next_plan.binding, 120)) : ""}</small>${linkify(clip(d.next_plan.diagnosis, 520))}
    <details><summary>directions it handed out</summary><ol>${d.next_plan.directions.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></details></div>`;
  else if (d.next_plan_note) h += `<p class="src">${esc(d.next_plan_note)}</p>`;
  h += `<h4>Where these numbers come from</h4><p class="src"><b>Simulation:</b> ${esc(d.sources.simulation)}.<br><b>Agent message:</b> ${esc(d.sources.agent)}.</p>` +
    (d.sources.agent_reply ? `<details><summary>the worker's raw reply</summary><pre>${esc(d.sources.agent_reply)}</pre></details>` : "");
  const max = R && R.id === run && R.cands.length ? Math.max(...R.cands.map((c) => c.n)) : 999;
  openDrawer(`Design #${n} <span class="navs"><button class="mini" data-cand="${n - 1}" ${n <= 1 ? "disabled" : ""} title="previous design (←)">←</button><button class="mini" data-cand="${n + 1}" ${n >= max ? "disabled" : ""} title="next design (→)">→</button></span>`, h, "cand");
  document.querySelectorAll("#dots .dot.sel").forEach((x) => x.classList.remove("sel")); $("dot" + n)?.classList.add("sel");
  const firstFail = d.points.find((p) => !p.pass); if (firstFail) $("ptDetail").innerHTML = pointDetail(firstFail, tg);
}
async function openPlans() {
  const run = curRun(); loading("The architect's plans");
  let d; try { d = await jget(`/api/explain?run=${encodeURIComponent(run)}&upto=${upto(run)}`); } catch (err) { d = { error: String(err) }; }
  if (d.error || !(d.plans || []).length) return openDrawer("The architect's plans", `<p class="muted">${esc(d.error || "No plan recorded yet for this run.")}</p>`, "plans");
  openDrawer("The architect's plans", d.plans.map((p) => `<h4>Plan written after design #${p.after_n} <small>${esc(p.model || "")} · ${(+p.latency_s || 0).toFixed(0)} s</small></h4>
    <div class="whyr big">${linkify(p.diagnosis)}</div>${p.binding ? `<p><b>Hardest requirement:</b> ${esc(p.binding)}</p>` : ""}
    ${p.directions.map((x, i) => `<div class="quote2"><small>Worker ${i + 1}</small><b>${esc(x.focus)}</b><details><summary>instructions</summary>${linkify(x.instructions || "")}</details></div>`).join("")}`).join("") +
    `<p class="src">Source: runs/swarm/${esc(run)}/architect.jsonl</p>`, "plans");
}

/* ------------------------------------------------------------------ 3 + 4. ask the crew / request a change */
const ASK_SUGGEST = ["Why did the first design fail?", "Which corner was the hardest, and why?", "What did the crew change to cut the power?", "Is the winning design safe to build?"];
const CHG_SUGGEST = ["Make it faster, I can spend more power", "Halve the power budget", "I need 70 degrees of phase margin"];
function openAsk(tab) {
  X.askTab = tab || X.askTab || "ask";
  const ask = X.askTab === "ask", run = curRun();
  openDrawer("Talk to the crew", `<div class="tabs"><button class="${ask ? "on" : ""}" data-asktab="ask">Ask a question</button><button class="${ask ? "" : "on"}" data-asktab="change">Request a change</button></div>
    <p class="src">${ask ? `Gemini 4 Argon answers from the recorded simulations and agent messages of run <code>${esc(run || "none yet")}</code>, and cites them.` : `Say what you want differently. Gemini 4 Argon turns it into a new spec; you confirm before the crew re-runs.`}</p>
    <textarea id="askText" rows="2" placeholder="${ask ? "e.g. Why did design #6 fail?" : "e.g. make it faster, I can spend more power"}"></textarea>
    <div class="row"><button id="askSend" class="big primary">${ask ? "Ask" : "Propose the change"} <kbd>Enter</kbd></button><button id="askTalk" class="big">Hold to talk</button><span id="askState" class="src"></span></div>
    <div class="suggest">${(ask ? ASK_SUGGEST : CHG_SUGGEST).map((q) => `<button data-suggest="${esc(q)}">${esc(q)}</button>`).join("")}</div>
    <div id="askOut"></div>`, "ask");
  $("askSend").onclick = () => sendAsk({});
  holdToTalk($("askTalk"), (blob) => sendAsk({ blob }), $("askState"));
  setTimeout(() => $("askText")?.focus(), 30);
}
function holdToTalk(btn, onBlob, state) {
  let rec = null, parts = [];
  const start = async (e) => {
    e.preventDefault(); if (rec) return;
    try {
      micStream = micStream || await navigator.mediaDevices.getUserMedia({ audio: true });
      parts = []; rec = new MediaRecorder(micStream);
      rec.ondataavailable = (ev) => ev.data.size && parts.push(ev.data);
      rec.onstop = () => { const blob = new Blob(parts, { type: rec.mimeType || "audio/webm" }); rec = null; btn.classList.remove("rec"); btn.textContent = "Hold to talk"; if (blob.size > 800) onBlob(blob); else state.textContent = "too short: hold the button while you speak"; };
      rec.start(); btn.classList.add("rec"); btn.textContent = "Recording… release to send";
    } catch (err) { state.textContent = "microphone unavailable (" + err.name + "): type instead"; }
  };
  const stop = () => { if (rec && rec.state === "recording") rec.stop(); };
  btn.addEventListener("pointerdown", start); ["pointerup", "pointerleave"].forEach((ev) => btn.addEventListener(ev, stop));
  btn.addEventListener("keydown", (e) => { if (e.code === "Space" && !e.repeat) start(e); }); btn.addEventListener("keyup", (e) => { if (e.code === "Space") stop(); });
}
const b64 = (blob) => new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(",")[1]); fr.onerror = rej; fr.readAsDataURL(blob); });
async function sendAsk({ blob }) {
  const text = $("askText").value.trim(), ask = X.askTab === "ask", run = curRun();
  if (!blob && !text) return;
  const O = $("askOut"); $("askState").textContent = ""; $("askSend").disabled = true;
  O.innerHTML = `<p class="thinking">Gemini 4 Argon is ${blob ? "listening, then " : ""}${ask ? "reading the run's simulations and agent messages" : "working out the new spec"}…</p>`;
  const body = { run }; if (blob) { body.audio = await b64(blob); body.mime = blob.type || "audio/webm"; } else body[ask ? "question" : "text"] = text;
  if (!ask) body.spec = (S.spec && S.spec.spec) || (R && R.id ? R.spec : null);
  let d;
  try { d = await jpost(ask ? "/api/ask" : "/api/change", JSON.stringify(body), { "Content-Type": "application/json" }); } catch (err) { d = { error: String(err) }; }
  $("askSend") && ($("askSend").disabled = false);
  if (!$("askOut")) return;
  if (d.error) { O.innerHTML = `<p class="warnbox">${esc(d.error)}</p>` + lookedAt(d); return; }
  if (ask) {
    O.innerHTML = `<div class="q">“${esc(d.question)}”</div><div class="answer ${d.answered_from_data ? "" : "nodata"}">${linkify(d.answer)}</div>
      <p class="src">${d.answered_from_data ? "Answered from the recorded data" : "Not in the recorded data"} · ${esc(d.model)} · ${(+d.latency_s).toFixed(1)} s` +
      (d.candidates_cited.length ? ` · designs it relied on: ${d.candidates_cited.map((n) => candLink(n)).join(" ")}` : "") + (d.corners_cited.length ? ` · corners: ${d.corners_cited.map(esc).join(", ")}` : "") + `</p>` + lookedAt(d);
  } else renderChange(d);
  markTerms(O);
}
const lookedAt = (d) => !(d.looked_at || []).length ? "" : `<details class="looked"><summary>What I looked at (${d.looked_at.length} sources, ${Math.round(d.looked_at.reduce((a, s) => a + s.chars, 0) / 1000)}k characters of recorded data)</summary>` +
  d.looked_at.map((s) => `<details><summary>${esc(s.title)} <small>${s.chars} chars</small></summary><pre>${esc(s.text)}</pre></details>`).join("") + `</details>`;
function renderChange(d) {
  X.change = d;
  const rows = Object.keys(SPEC).map((k) => {
    const ch = d.changed.includes(k), op = d.after[k][0].startsWith(">") ? "at least" : "at most", u = SPEC[k][3] === "°" ? "°" : " " + SPEC[k][3];
    return `<div class="chg ${ch ? "changed" : ""}"><div><b>${SPEC[k][0]}</b> <small>${SPEC[k][1]}</small></div><div class="vals"><span>${op} ${fmt(k, d.before[k][1])}${u}</span>${ch ? `<i>→</i><b>${fmt(k, d.after[k][1])}${u}</b>` : `<small>unchanged</small>`}</div>
      <small>${ch ? `crew will aim for ${fmt(k, d.after_target[k][1])}${u} (guard band)` : ""}</small></div>`;
  }).join("");
  $("askOut").innerHTML = `<div class="q">“${esc(d.request)}”</div>` + (!d.understood || !d.changed.length ? `<p class="warnbox">That does not change any of the four requirements. ${esc(d.reason)}</p>` : "") +
    `<h4>Spec: before → after</h4><div class="chgs">${rows}</div><div class="answer">${linkify(d.reason)}</div>` + (d.notes.length ? `<p class="src">${d.notes.map(esc).join(" · ")}</p>` : "") +
    `<p class="src">${esc(d.model)} · ${(+d.latency_s).toFixed(1)} s · ${d.start_from ? `the new run will start from this run's best design (${candLink(d.start_from_n)}) instead of the published sizes` : "the new run will start from the published sizes"}</p>` +
    (d.changed.length ? `<div class="row"><button id="chgGo" class="big primary">Confirm: re-run the crew with this spec</button><button id="chgNo" class="big">Cancel</button></div>` : "");
  if ($("chgGo")) { $("chgGo").onclick = confirmChange; $("chgNo").onclick = () => ($("askOut").innerHTML = ""); }
}
async function confirmChange() {
  const d = X.change; if (!d) return;
  $("chgGo").disabled = true;
  renderSpec({ spec: d.after, target: d.after_target, transcript: d.request, notes: [d.reason], model: d.model, latency_s: d.latency_s,
    stated: d.changed.map((k) => ({ pm_deg: "pm_deg", ugf_hz: "ugf", dc_gain_db: "dc_gain", i_core_a: "i_core" }[k])) });
  try {
    const circuit = ["diff", "fc", "tele"].includes(d.circuit) ? d.circuit : ($("selCircuit").value !== "auto" ? $("selCircuit").value : "diff");
    const r = await jpost("/api/run", JSON.stringify({ circuit, spec: d.after, start_from_run: d.start_from ? d.run : null }), { "Content-Type": "application/json" });
    if (r.error) throw new Error(r.error);
    closeDrawer(); go(3); connect(r.run_id, { replay: false });
    toast(`Crew re-running with the new spec${r.started_from ? ", starting from the best design of " + r.started_from : ""}`, 7000);
  } catch (err) { toast("could not start: " + (err.message || err), 9000); $("chgGo").disabled = false; }
}

/* ------------------------------------------------------------------ 5. glossary + legend */
function openGlossary() {
  openDrawer("Glossary", `<p class="src">Hover or tap any dotted word on screen for its definition.</p>` + Object.values(GLOSS).map(([t, def]) => `<div class="gl"><b>${esc(t)}</b>${esc(def)}</div>`).join("") +
    `<h4>Legend</h4><div class="gl"><b><i class="dot demo"></i> hollow dot</b>a design that fails at one or more corners; its colour is the worker that proposed it.</div>
    <div class="gl"><b><i class="dot demo full"></i> filled dot</b>a design that passes all 15 corners. A black ring marks the best so far. Click any dot for its full story.</div>
    <div class="gl"><b><span class="lg pass">✓</span> <span class="lg fail">✗</span> tiles</b>one tile per corner for the best design: green passes, red names the check that failed.</div>
    <div class="gl"><b>Parts</b>${Object.keys(PARTNAME).map((p) => partChip(p)).join(" ")}</div>`, "glossary");
}
function markTerms(root) {
  if (!root) return;
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (n) => n.nodeValue.length < 4 || n.parentElement.closest(".term,button,select,textarea,input,pre,code,script,kbd,.noterm,nav") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const nodes = []; while (tw.nextNode()) nodes.push(tw.currentNode);
  for (const n of nodes) {
    for (const [rx, key] of TERM_RX) {
      const m = rx.exec(n.nodeValue); if (!m) continue;
      const mid = n.splitText(m.index); mid.splitText(m[0].length);
      const s = document.createElement("span"); s.className = "term"; s.dataset.term = key; s.tabIndex = 0; s.textContent = mid.nodeValue; mid.replaceWith(s);
      break;   // one term per text node per pass keeps this cheap; the next pass picks up the rest
    }
  }
}
function showTip(t) {
  const g = GLOSS[t.dataset.term]; if (!g) return;
  const T = $("tip"); T.innerHTML = `<b>${esc(g[0])}</b>${esc(g[1])}`; T.hidden = false;
  const r = t.getBoundingClientRect(), w = T.offsetWidth, hgt = T.offsetHeight;
  T.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
  T.style.top = (r.bottom + hgt + 12 > innerHeight ? r.top - hgt - 8 : r.bottom + 8) + "px";
}
function hideTip() { $("tip").hidden = true; }
document.addEventListener("mouseover", (e) => { const t = e.target.closest?.(".term"); if (t) showTip(t); });
document.addEventListener("mouseout", (e) => { if (e.target.closest?.(".term")) hideTip(); });
document.addEventListener("focusin", (e) => { const t = e.target.closest?.(".term"); if (t) showTip(t); else hideTip(); });
setInterval(() => { markTerms(document.querySelector(".step.on")); }, 1200);

/* ------------------------------------------------------------------ layout step: attempts are clickable too */
function openLay(n) {
  const c = L && L.cands.find((x) => x.n === +n); if (!c) return;
  const wi = /^w\d+$/.test(c.worker) ? +c.worker.slice(1) + 1 : null;
  openDrawer(`Floorplan #${c.n}`, `<div class="verdict ${c.passed && c.feasible !== false ? "ok" : "no"}">${c.passed ? "✓ still passes all 15 corners with estimated parasitics" : "✗ fails once layout parasitics are added"}${c.feasible === false ? " · breaks a layout rule" : ""}</div>
    <h4>1 · Who proposed it</h4><p><b>${wi ? "Layout worker " + wi : esc(c.worker || "?")}</b>, round ${c.iter ?? "?"}.</p>
    ${c.rationale ? `<h4>2 · Its reason</h4><div class="whyr big">“${esc(c.rationale)}”</div>` : ""}
    <h4>3 · The plan</h4><pre>${esc(planText(c.plan))}</pre>
    <h4>4 · What the Judge said</h4><table class="xt"><tr><td>Chip area</td><td><b>${num(c.area_um2)} µm²</b></td><td>${num(c.width_um)} × ${num(c.height_um)} µm</td></tr>
      <tr><td>Built-in mismatch (input offset)</td><td><b>${num(c.offset_uV)} µV</b></td><td></td></tr>
      <tr><td>Stability (phase margin)</td><td><b>${num(c.pm_deg)}°</b></td><td>re-simulated with parasitics</td></tr>
      <tr><td>Speed (unity-gain frequency)</td><td><b>${num((c.ugf_hz || 0) / 1e6)} MHz</b></td><td></td></tr>
      <tr><td>Estimated wire length</td><td><b>${num(c.wirelength_um)} µm</b></td><td></td></tr></table>
    ${(c.violations || []).length ? `<ul class="fails">${c.violations.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}${(c.sim_failures || []).length ? `<ul class="fails">${c.sim_failures.slice(0, 6).map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
    ${c.image ? `<figure class="schem"><img src="${layImg(c.image)}" alt=""></figure>` : ""}
    <p class="src">Source: runs/layout/${esc(L.id)}/events.jsonl, candidate ${c.n}. Estimated parasitics: not routed, no DRC/LVS.</p>`, "lay");
}

/* ------------------------------------------------------------------ 6. PCB boards */
const P = { runs: [], run: null };
const pcbImg = (run, board, name) => `/api/pcbfile?run=${encodeURIComponent(run)}&board=${encodeURIComponent(board)}&name=${name}`;
const stat = (s) => !s ? "?" : `${s.unrouted ?? "?"} open connection${s.unrouted === 1 ? "" : "s"}, ${s.drc_errors ?? "?"} rule error${s.drc_errors === 1 ? "" : "s"}`;
async function loadPcb() {
  let d; try { d = await jget("/api/pcb"); } catch (err) { d = { runs: [] }; }
  P.runs = d.runs || [];
  const sel = $("selPcb"), cur = sel.value || QS.get("pcb") || "";
  sel.innerHTML = P.runs.map((r) => `<option value="${esc(r.id)}">${esc(r.id)}${r.done ? "" : " (running)"}</option>`).join("") || "<option value=''>no run yet</option>";
  if (cur && P.runs.some((r) => r.id === cur)) sel.value = cur;
  renderPcb();
}
function renderPcb() {
  const run = P.runs.find((r) => r.id === $("selPcb").value) || P.runs[0], B = $("boards");
  if (!run) {
    $("pcbInfo").textContent = "no run yet";
    B.innerHTML = `<div class="empty"><b>No board-routing run recorded yet.</b><p>When the routing crew has run, each board appears here as a card: the board before and after, how many open connections and rule errors it started with, whether it ended as a clean pass, the repair plan that won, and every attempt the Judge scored.</p><p class="src">Looking in runs/pcb/&lt;run&gt;/summary.json</p></div>`;
    return;
  }
  P.run = run.id;
  const clean = run.boards.filter((b) => b.final && b.final.clean_pass).length;
  $("pcbInfo").innerHTML = `<b>${esc(run.id)}</b> · ${run.boards.length} board${run.boards.length === 1 ? "" : "s"}` + (run.done ? ` · <b>${clean}</b> ended as a clean pass` : " · still running");
  B.className = "boards n" + Math.min(run.boards.length, 4);
  B.innerHTML = run.boards.map((b) => {
    const ok = b.final && b.final.clean_pass, st0 = b.start || {};
    const head = b.running ? `still routing… ${b.attempts || 0} attempt${b.attempts === 1 ? "" : "s"} so far` :
      `${stat(st0)} → ` + (ok ? "<b class='okt'>clean</b>" : b.final ? `<b class='badt'>${stat(b.final)} left</b>` : "<b class='badt'>no result</b>");
    const plan = b.best_plan || {};
    return `<div class="board ${ok ? "ok" : b.running ? "" : "no"}"><div class="bh"><b>${esc(b.board_id)}</b><span class="pill ${ok ? "ok" : b.running ? "" : "no"}">${ok ? "✓ clean pass" : b.running ? "running" : "✗ not clean"}</span></div>
      <div class="bimgs"><figure><div class="imgbox">${b.has_start ? `<img src="${pcbImg(run.id, b.board_id, "start.png")}" alt="">` : `<span class="ph">no picture</span>`}</div><figcaption>before</figcaption></figure>
      <figure><div class="imgbox">${b.has_final ? `<img src="${pcbImg(run.id, b.board_id, "final.png")}" alt="">` : `<span class="ph">${b.running ? "routing…" : "no final picture recorded"}</span>`}</div><figcaption>after</figcaption></figure></div>
      <div class="bstat">${head}</div>
      ${plan.name || b.plan_words ? `<div class="bplan"><small>${ok ? "the repair plan that won" : "best plan tried"}${b.best_tag ? " · " + esc(b.best_tag) : ""}</small><b>${esc(plan.name || "")}</b> ${esc(b.plan_words || "")}${plan.why ? `<div class="whyr">“${esc(clip(plan.why, 260))}”</div>` : ""}</div>` : (b.running ? "" : `<div class="bplan muted">No repair plan recorded.</div>`)}
      <div class="battempts" data-board="${esc(b.board_id)}"><small>${typeof b.attempts === "number" ? b.attempts : (b.attempts || []).length} attempts (click one):</small> <span class="muted">loading…</span></div></div>`;
  }).join("") || `<div class="empty"><b>This run has no boards yet.</b></div>`;
  for (const b of run.boards) loadAttempts(run.id, b.board_id);
  markTerms(B);
}
async function loadAttempts(run, board) {
  let d; try { d = await jget(`/api/pcb_board?run=${encodeURIComponent(run)}&board=${encodeURIComponent(board)}`); } catch (err) { return; }
  P[run + "/" + board] = d;
  const box = document.querySelector(`.battempts[data-board="${CSS.escape(board)}"]`); if (!box) return;
  const hs = d.history || [];
  box.innerHTML = `<small>${hs.length} attempt${hs.length === 1 ? "" : "s"} (click one):</small> ` + (hs.map((a, i) => { const ok = (a.score && a.score.clean_pass) || (a.end_status && a.end_status.clean); return `<button class="att ${ok ? "ok" : "no"}" data-att="${i}" data-board="${esc(board)}" title="${esc(a.tag || "")}">${esc(a.tag || i + 1)}${ok ? " ✓" : ""}</button>`; }).join("") || `<span class="muted">none recorded</span>`);
}
function openAttempt(board, i) {
  const d = P[P.run + "/" + board], a = d && d.history[+i]; if (!a) return;
  const plan = a.plan || {}, es = a.end_status || {}, ok = (a.score && a.score.clean_pass) || es.clean;
  const diag = (d.llm || []).filter((l) => l.diagnosis).map((l) => l.diagnosis);
  openDrawer(`${esc(board)} · attempt ${esc(a.tag || +i + 1)}`, `<div class="verdict ${ok ? "ok" : "no"}">${ok ? "✓ clean pass" : `✗ ${stat(es)} left`}</div>
    <h4>1 · The plan</h4><p><b>${esc(plan.name || "(unnamed plan)")}</b></p><p>${esc(a.plan_words || "No rip-up: route what is open as it stands.")}</p>${plan.why ? `<div class="whyr big">“${esc(plan.why)}”</div>` : ""}
    <details><summary>plan as the agent wrote it</summary><pre>${esc(JSON.stringify(plan, null, 1))}</pre></details>
    <h4>2 · What the Judge said</h4><table class="xt"><tr><td>Open connections left</td><td><b>${es.unrouted ?? "?"}</b></td></tr><tr><td>Rule errors (DRC)</td><td><b>${es.drc_errors ?? "?"}</b></td></tr>
      <tr><td>Clean pass</td><td><b>${ok ? "yes" : "no"}</b></td></tr>${a.seconds != null ? `<tr><td>Router time</td><td><b>${(+a.seconds).toFixed(1)} s</b></td></tr>` : ""}</table>
    <details><summary>full score</summary><pre>${esc(JSON.stringify(a.score || {}, null, 1))}</pre></details>
    ${diag.length ? `<h4>3 · The agent's diagnosis of this board</h4>${diag.slice(-2).map((x) => `<div class="quote2">${esc(clip(x, 700))}</div>`).join("")}` : ""}
    <p class="src">Source: runs/pcb/${esc(P.run)}/${esc(board)}/history.json, attempt ${+i + 1}; the judge is the router's own connectivity and rule check.</p>`, "pcb");
}
$("selPcb").onchange = renderPcb; $("btnPcbReload").onclick = loadPcb;

/* ------------------------------------------------------------------ parts strips + hooks into the stage */
async function partsStrip(target, run, circuit) {
  let html = "";
  try {
    const d = run ? await jget(`/api/explain?run=${encodeURIComponent(run)}&upto=1`) : null;
    if (d && d.parts) { X.ov[run] = d; html = d.parts.map((p) => partChip(p.part, p.name.replace(/ \(.*/, ""))).join(""); }
  } catch (err) { /* offline */ }
  if (!html) html = ["in", "tail", "load", "drv", "sink", "comp", "bias"].map((p) => partChip(p)).join("");
  $(target).innerHTML = `<span class="small noterm">parts (click to explore):</span>` + html;
}
const _onEvent = onEvent;
onEvent = function (e) {   // eslint-disable-line no-global-assign
  _onEvent(e);
  if (e.type === "start") partsStrip("partsStrip", R.id);
  if (e.type === "candidate") {
    const w = $("w" + (/^w\d+$/.test(e.worker) ? +e.worker.slice(1) : -1)); if (w) { w.dataset.cand = e.n; w.tabIndex = 0; }
    const d = $("dot" + e.n); if (d) { d.dataset.cand = e.n; d.tabIndex = 0; }
    if (R.best) for (const id of ["judgeBody", "currentBar", "metrics"]) $(id).dataset.cand = R.best.n;
  }
};
const _onLay = onLayoutEvent;
onLayoutEvent = function (e) {   // eslint-disable-line no-global-assign
  _onLay(e);
  if (e.type === "start") { const m = /swarm\/([A-Za-z0-9_.-]+)\//.exec(String(e.design_from || "")); L.fromRun = m ? m[1] : null; partsStrip("layParts", L.fromRun || curRun()); }
  if (e.type === "candidate") { const d = $("ldot" + e.n); if (d) { d.dataset.lay = e.n; d.tabIndex = 0; } const w = $("lw" + (/^w\d+$/.test(e.worker) ? +e.worker.slice(1) : -1)); if (w) { w.dataset.lay = e.n; w.tabIndex = 0; } }
};
const _go = go;
go = function (n) { _go(n); if (n === 7) loadPcb(); markTerms(document.querySelector(".step.on")); };   // eslint-disable-line no-global-assign
document.querySelectorAll("#nav button[data-step]").forEach((b) => (b.onclick = () => go(+b.dataset.step)));
$("btnAsk").onclick = () => (X.view === "ask" ? closeDrawer() : openAsk());
$("btnGloss").onclick = () => (X.view === "glossary" ? closeDrawer() : openGlossary());
$("archBubble").onclick = openPlans; $("archBubble").tabIndex = 0;

document.addEventListener("click", (e) => {
  const t = e.target.closest?.("[data-part],[data-cand],[data-lay],[data-att],[data-pt],[data-asktab],[data-suggest],.term");
  if (!t || t.disabled) return;
  if (t.classList.contains("term")) return showTip(t);
  if (t.dataset.pt && X.cand) { const [c, tt] = t.dataset.pt.split("/"); const p = X.cand.points.find((q) => q.corner === c && String(q.temp) === tt); if (p) $("ptDetail").innerHTML = pointDetail(p, X.cand.target); return; }
  if (t.dataset.asktab) return openAsk(t.dataset.asktab);
  if (t.dataset.suggest) { $("askText").value = t.dataset.suggest; return sendAsk({}); }
  if (t.dataset.att != null) return openAttempt(t.dataset.board, t.dataset.att);
  if (t.dataset.part) return openPart(t.dataset.part, { run: S.step === 4 && L && L.fromRun ? L.fromRun : undefined, circuit: t.dataset.circuit });
  if (t.dataset.lay) return openLay(t.dataset.lay);
  if (t.dataset.cand) return openCand(t.dataset.cand, X.view === "cand" && t.closest("#drawer") ? X.run : undefined);
});
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
  if (typing) { if (e.key === "Enter" && !e.shiftKey && document.activeElement.id === "askText") { e.preventDefault(); sendAsk({}); } if (e.key === "Escape" && document.activeElement.id === "askText") closeDrawer(); return; }
  if (e.key === "Escape") return closeDrawer();
  if (e.key === "a" || e.key === "/") { e.preventDefault(); return openAsk(); }
  if (e.key === "g") return openGlossary();
  if (e.key === "7") return go(7);
  if (X.view === "cand" && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { const b = document.querySelector(`#drawerTitle [data-cand="${X.n + (e.key === "ArrowRight" ? 1 : -1)}"]`); if (b && !b.disabled) b.click(); return; }
  if ((e.key === "Enter" || e.key === " ") && document.activeElement?.matches?.("[data-part],[data-cand],[data-lay],.term") && document.activeElement.tagName !== "BUTTON") { e.preventDefault(); document.activeElement.click(); }
}, true);
markTerms(document.body);
if (QS.get("step") === "7") go(7);
if (QS.get("open")) setTimeout(() => { const o = QS.get("open"); if (/^\d+$/.test(o)) openCand(+o); else if (o === "ask") openAsk(QS.get("tab") || "ask"); else if (o === "glossary") openGlossary(); else if (o === "plans") openPlans(); else openPart(o); if (QS.get("q")) setTimeout(() => { $("askText").value = QS.get("q"); sendAsk({}); }, 200); }, +(QS.get("open_after") || 1500));
