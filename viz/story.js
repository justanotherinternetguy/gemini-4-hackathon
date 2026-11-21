/* viz.story runtime: scene navigation, SVG gauges / corner grids / progress chart, playback. No deps. */
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("data").textContent);
  const NS = "http://www.w3.org/2000/svg";
  const scenes = Array.from(document.querySelectorAll(".scene"));
  const C = D.cands;
  const N = C.length;
  const css = getComputedStyle(document.documentElement);
  const col = (v) => css.getPropertyValue(v).trim();
  const COL = {
    good: col("--goodfill"), goodbg: col("--goodbg"), bad: col("--badfill"), badbg: col("--badbg"),
    amber: col("--amber"), amberbg: col("--amberbg"), ink: col("--ink"), muted: col("--muted"),
    line: col("--line"), hyd: col("--hyd"),
  };
  const passYard = (c) => (D.yard === "target" ? c.pass_target : c.pass_spec);

  // ------------------------------------------------------------------ layout: scale 1920x1080 stages
  function fit() {
    const k = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    document.documentElement.style.setProperty("--k", k.toFixed(4));
  }
  fit();
  window.addEventListener("resize", fit);

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function h(tag, cls, parent, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    if (parent) parent.appendChild(e);
    return e;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const GL = {
    pm: ["Phase margin", "How far the feedback loop is from ringing or oscillating. More degrees = calmer."],
    ugf: ["Unity-gain frequency (UGF)", "How fast the amplifier responds: the frequency where its gain drops to 1×."],
    gain: ["DC gain", "How strongly it amplifies slow signals. More dB = more accurate output."],
    icore: ["Core current", "Supply current of the amplifier's two stages: what the battery pays for."],
    sat: ["Saturation", "The region where a transistor works as a good amplifier. Too little voltage headroom and it drops out: gain collapses."],
  };
  const g = (k, t) => `<span class="g" tabindex="0">${esc(t)}<span class="tip"><b>${esc(GL[k][0])}</b>${esc(GL[k][1])}</span></span>`;

  // ------------------------------------------------------------------ metrics
  const M = [
    { k: "pm_deg", name: "Stability", tech: g("pm", "phase margin"), dom: [0, 90], dir: 1, spec: D.spec.pm,
      fmt: (v) => v.toFixed(1) + "°", lim: (v) => v.toFixed(0) + "°" },
    { k: "ugf_mhz", name: "Speed", tech: g("ugf", "unity-gain frequency"), dom: [5, 150], log: true, dir: 1, spec: D.spec.ugf,
      fmt: (v) => (v < 100 ? v.toFixed(1) : v.toFixed(0)) + " MHz", lim: (v) => v.toFixed(0) + " MHz" },
    { k: "dc_gain_db", name: "Accuracy", tech: g("gain", "DC loop gain"), dom: [40, 85], dir: 1, spec: D.spec.gain,
      fmt: (v) => v.toFixed(1) + " dB", lim: (v) => v.toFixed(0) + " dB" },
    { k: "i_core_ua", name: "Power", tech: g("icore", "core current") + " (lower is better)", dom: [0, 120], dir: -1, spec: D.spec.cur,
      fmt: (v) => v.toFixed(1) + " µA", lim: (v) => v.toFixed(0) + " µA" },
    { k: "slack_mv", name: "Headroom", tech: g("sat", "saturation") + " margin above need", dom: [-50, 60], dir: 1, spec: D.spec.slack,
      fmt: (v) => (v > 0 ? "+" : "") + v.toFixed(1) + " mV", lim: (v) => (v > 0 ? "+" : "") + v.toFixed(0) + " mV" },
  ];
  function status(m, v) {
    if (v === null || v === undefined) return 2;
    const [s, t] = m.spec;
    if (m.dir > 0) return v < s ? 2 : v < t ? 1 : 0;
    return v > s ? 2 : v > t ? 1 : 0;
  }
  function frac(m, v) {
    const [a, b] = m.dom;
    let f = m.log ? (Math.log(v) - Math.log(a)) / (Math.log(b) - Math.log(a)) : (v - a) / (b - a);
    return Math.max(0, Math.min(1, f));
  }

  // ------------------------------------------------------------------ gauges
  function buildGauges(root) {
    const mini = root.classList.contains("mini");
    const H = mini ? 50 : 120;
    const ty0 = mini ? 12 : 44, th = mini ? 26 : 34;
    const k = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--k")) || 1;
    root.innerHTML = "";
    root._rows = M.map((m) => {
      const row = h("div", "grow", root);
      h("div", "", row, `<div class="gname">${m.name}</div><div class="gtech">${m.tech}</div>`);
      const svg = el("svg", { class: "gauge" }, row);
      const val = h("div", "gval", row);
      const W = Math.round(svg.getBoundingClientRect().width / k) || 600;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      // zones
      const [s, t] = m.spec;
      const xs = frac(m, s) * W, xt = frac(m, t) * W;
      const zones = m.dir > 0
        ? [[0, xs, COL.badbg], [xs, xt, COL.amber], [xt, W, COL.goodbg]]
        : [[0, xt, COL.goodbg], [xt, xs, COL.amber], [xs, W, COL.badbg]];
      el("rect", { x: 0, y: ty0, width: W, height: th, rx: 8, fill: "#eceae4" }, svg);
      for (const [a, b, c] of zones) el("rect", { x: a, y: ty0, width: Math.max(0, b - a), height: th, fill: c, opacity: c === COL.amber ? 0.75 : 1 }, svg);
      el("line", { x1: xs, x2: xs, y1: ty0 - 4, y2: ty0 + th + 4, stroke: COL.bad, "stroke-width": 3 }, svg);
      // before marker (diamond) + label below; after marker (circle) + label above
      const before = el("g", { class: "mkr" }, svg);
      const dsz = mini ? 9 : 13;
      el("rect", { x: -dsz, y: -dsz, width: 2 * dsz, height: 2 * dsz, transform: `translate(0 ${ty0 + th / 2}) rotate(45)`, fill: "#8f8d86", stroke: "#fff", "stroke-width": 3 }, before);
      let bl = null, al = null;
      if (!mini) bl = el("text", { y: ty0 + th + 34, "text-anchor": "middle", "font-size": 22, fill: COL.muted }, before);
      const after = el("g", { class: "mkr" }, svg);
      el("circle", { cy: ty0 + th / 2, r: mini ? 12 : 17, fill: COL.ink, stroke: "#fff", "stroke-width": 4 }, after);
      if (!mini) al = el("text", { y: ty0 - 12, "text-anchor": "middle", "font-size": 24, "font-weight": 800, fill: COL.ink }, after);
      return { m, svg, val, before, after, bl, al, W };
    });
  }
  function updateGauges(root, ia, ib) {
    if (!root._rows) buildGauges(root);
    const mini = root.classList.contains("mini");
    for (const r of root._rows) {
      const m = r.m;
      const va = C[ia].worst[m.k];
      const vb = ib === null || ib === undefined ? null : C[ib].worst[m.k];
      const xa = va === null ? 0 : frac(m, va) * r.W;
      r.after.style.transform = `translateX(${xa}px)`;
      r.after.style.opacity = va === null ? 0 : 1;
      if (vb !== null) {
        r.before.style.transform = `translateX(${frac(m, vb) * r.W}px)`;
        r.before.style.opacity = 1;
      } else r.before.style.opacity = 0;
      if (r.al) {
        r.al.textContent = ia === D.final ? "Gemini" : "#" + C[ia].n;
        r.al.setAttribute("text-anchor", xa < 70 ? "start" : xa > r.W - 70 ? "end" : "middle");
        r.al.setAttribute("x", xa < 70 ? -16 : xa > r.W - 70 ? 16 : 0);
      }
      if (r.bl && vb !== null) {
        const xb = frac(m, vb) * r.W;
        r.bl.textContent = "published " + m.fmt(vb);
        r.bl.setAttribute("text-anchor", xb < 140 ? "start" : xb > r.W - 140 ? "end" : "middle");
        r.bl.setAttribute("x", xb < 140 ? -14 : xb > r.W - 140 ? 14 : 0);
      }
      const st = status(m, va);
      const need = m.dir > 0 ? `need ≥ ${m.lim(m.spec[0])} · target ${m.lim(m.spec[1])}` : `limit ≤ ${m.lim(m.spec[0])} · target ${m.lim(m.spec[1])}`;
      const glyph = ["✓", "!", "✗"][st];
      r.val.innerHTML = `<div class="gbig s${st}">${va === null ? "—" : m.fmt(va)} ${mini ? "" : glyph}</div><div class="gneed">${need}</div>`;
    }
  }

  // ------------------------------------------------------------------ corner grids
  function buildGrid(root) {
    const mini = root.classList.contains("mini");
    root.innerHTML = "";
    h("div", "th", root, "");
    for (const t of D.temps) h("div", "th", root, `${String(t).replace("-", "−")} °C`);
    root._tiles = [];
    D.corners.forEach(([code, name]) => {
      h("div", "rh", root, mini ? code : `${name}<small>${code}</small>`);
      for (let j = 0; j < D.temps.length; j++) {
        const t = h("div", "tile", root, `<div class="gl"></div>${mini ? "" : '<div class="why"></div>'}`);
        t.title = `${name} silicon (${code}) at ${D.temps[j]} °C`;
        root._tiles.push(t);
      }
    });
  }
  function updateGrid(root, ci) {
    if (!root._tiles) buildGrid(root);
    const pts = C[ci].pts;
    root._tiles.forEach((t, i) => {
      const p = pts[i] || [2, ["no data"]];
      t.className = "tile t" + p[0] + (p[0] && p[1].length > 2 && !root.classList.contains("mini") ? " many" : "");
      t.querySelector(".gl").textContent = ["✓", "!", "✗"][p[0]];
      const w = t.querySelector(".why");
      if (w) w.textContent = p[0] ? p[1].join(" · ") : "";
    });
  }

  // ------------------------------------------------------------------ progress chart
  const P = { W: 1060, L: 96, R: 34, top: [64, 286], bot: [372, 584] };
  const px = (i) => P.L + (N > 1 ? (i / (N - 1)) * (P.W - P.L - P.R) : 0);
  const curMax = 120;
  const py1 = (v) => P.top[1] - (v / 15) * (P.top[1] - P.top[0]);
  const py2 = (v) => P.bot[1] - (Math.min(v, curMax) / curMax) * (P.bot[1] - P.bot[0]);
  function buildProg(svg) {
    svg.innerHTML = "";
    const t = (x, y, s, a) => el("text", Object.assign({ x, y, "font-size": 22, fill: COL.muted }, a || {}), svg, s);
    t(P.L - 70, 34, "Corners passing (of 15)", { "font-size": 24, "font-weight": 800, fill: COL.ink });
    t(P.L - 70, 344, "Current, µA — lower is better", { "font-size": 24, "font-weight": 800, fill: COL.ink });
    for (const v of [0, 5, 10, 15]) {
      el("line", { x1: P.L, x2: P.W - P.R, y1: py1(v), y2: py1(v), stroke: v === 15 ? COL.good : "#ebe9e3", "stroke-width": v === 15 ? 2 : 1.5, "stroke-dasharray": v === 15 ? "6 5" : "" }, svg);
      t(P.L - 14, py1(v) + 7, String(v), { "text-anchor": "end" });
    }
    for (const v of [0, 30, 60, 90, 120]) {
      el("line", { x1: P.L, x2: P.W - P.R, y1: py2(v), y2: py2(v), stroke: "#ebe9e3", "stroke-width": 1.5 }, svg);
      t(P.L - 14, py2(v) + 7, String(v), { "text-anchor": "end" });
    }
    // current limit (spec) + guard target
    el("line", { x1: P.L, x2: P.W - P.R, y1: py2(D.spec.cur[0]), y2: py2(D.spec.cur[0]), stroke: COL.bad, "stroke-width": 2.5, "stroke-dasharray": "8 6" }, svg);
    t(P.W - P.R - 4, py2(D.spec.cur[0]) - 8, `limit ${D.spec.cur[0]} µA`, { "text-anchor": "end", fill: COL.bad, "font-size": 20 });
    // x ticks
    for (let i = 0; i < N; i++) {
      const n = C[i].n;
      if (n === 1 || n % 10 === 0) t(px(i), 616, "#" + n, { "text-anchor": "middle", "font-size": 20 });
    }
    // replans
    (D.plans || []).slice(1).forEach((p) => {
      const i = C.findIndex((c) => c.iter > p.iter);
      if (i < 0) return;
      const g_ = el("g", { class: "lazy", "data-i": i }, svg);
      el("line", { x1: px(i) - 8, x2: px(i) - 8, y1: P.top[0] - 10, y2: P.top[1] + 10, stroke: "#1f232b", "stroke-width": 2, "stroke-dasharray": "3 5" }, g_);
      el("line", { x1: px(i) - 8, x2: px(i) - 8, y1: P.bot[0] - 12, y2: P.bot[1], stroke: "#1f232b", "stroke-width": 2, "stroke-dasharray": "3 5" }, g_);
      el("text", { x: px(i) - 2, y: P.bot[0] - 26, "font-size": 18, fill: "#1f232b", "font-weight": 700 }, g_, "Carbon re-plans");
    });
    // first pass + best marks
    const best = D.final;
    if (D.first !== null) {
      const i = D.first;
      const g_ = el("g", { class: "lazy", "data-i": i }, svg);
      el("line", { x1: px(i), x2: px(i), y1: P.top[0] - 12, y2: P.top[1] + 10, stroke: COL.good, "stroke-width": 3 }, g_);
      el("line", { x1: px(i), x2: px(i), y1: P.bot[0] - 12, y2: P.bot[1], stroke: COL.good, "stroke-width": 3 }, g_);
      const lab = "first design that passes everything";
      const right = px(i) < P.W - 420;
      el("text", { x: px(i) + (right ? 10 : -10), y: py1(7.5), "font-size": 21, "font-weight": 800, fill: COL.good, "text-anchor": right ? "start" : "end" }, g_, lab);
    }
    svg._step = el("path", { fill: "none", stroke: COL.good, "stroke-width": 3.5, "stroke-linejoin": "round" }, svg);
    svg._dots = C.map((c, i) => {
      const a = el("circle", { cx: px(i), cy: py1(c.n_spec), r: 9 }, svg);
      const fill = c.pass_target ? COL.good : c.pass_spec ? COL.amber : COL.bad;
      a.setAttribute("fill", fill);
      a.setAttribute("stroke", "#fff");
      a.setAttribute("stroke-width", 2);
      const b = el("circle", { cx: px(i), cy: py2(c.worst.i_core_ua || curMax), r: 9 }, svg);
      const ok = passYard(c);
      b.setAttribute("fill", ok ? COL.good : "#fff");
      b.setAttribute("stroke", ok ? "#fff" : "#9b998f");
      b.setAttribute("stroke-width", ok ? 2 : 3);
      return [a, b];
    });
    if (best !== null && passYard(C[best])) {
      const g_ = el("g", { class: "lazy", "data-i": best }, svg);
      const x = px(best), y = py2(C[best].worst.i_core_ua);
      const star = [];
      for (let k = 0; k < 10; k++) {
        const r = k % 2 ? 9 : 22, a = -Math.PI / 2 + (k * Math.PI) / 5;
        star.push(`${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`);
      }
      el("polygon", { points: star.join(" "), fill: "#ffd66b", stroke: COL.ink, "stroke-width": 2.5 }, g_);
      const right = x < P.W - 260;
      el("text", { x: x + (right ? 28 : -28), y: y - 18, "font-size": 23, "font-weight": 800, fill: COL.ink, "text-anchor": right ? "start" : "end" }, g_, `best: ${C[best].worst.i_core_ua.toFixed(1)} µA`);
    }
    svg._ring = el("circle", { r: 17, fill: "none", stroke: COL.ink, "stroke-width": 4 }, svg);
    svg._ring2 = el("circle", { r: 17, fill: "none", stroke: COL.ink, "stroke-width": 4 }, svg);
  }
  function updateProg(svg, ci) {
    if (!svg._dots) buildProg(svg);
    svg._dots.forEach(([a, b], i) => {
      const o = i <= ci ? 1 : 0.12;
      a.setAttribute("opacity", o);
      b.setAttribute("opacity", o);
    });
    svg.querySelectorAll(".lazy").forEach((g_) => (g_.style.opacity = +g_.dataset.i <= ci ? 1 : 0));
    let best = null, d = "";
    for (let i = 0; i <= ci; i++) {
      const c = C[i];
      if (passYard(c) && (best === null || c.worst.i_core_ua < best)) best = c.worst.i_core_ua;
      if (best !== null) d += (d ? " L" : "M") + `${px(i).toFixed(1)} ${py2(best).toFixed(1)}`;
    }
    if (d && ci < N - 1) d += ` L${px(ci).toFixed(1)} ${py2(best).toFixed(1)}`;
    svg._step.setAttribute("d", d);
    const c = C[ci];
    svg._ring.setAttribute("cx", px(ci)); svg._ring.setAttribute("cy", py1(c.n_spec));
    svg._ring2.setAttribute("cx", px(ci)); svg._ring2.setAttribute("cy", py2(c.worst.i_core_ua || curMax));
  }

  // ------------------------------------------------------------------ static scenes
  document.querySelectorAll(".cgrid[data-cand]").forEach((g_) => updateGrid(g_, +g_.dataset.cand));
  document.querySelectorAll(".gauges[data-after]").forEach((g_) => updateGauges(g_, +g_.dataset.after, +g_.dataset.before));

  // ------------------------------------------------------------------ playback
  const pb = document.querySelector(".play");
  let playIdx = 0, timer = null, autoplayed = false;
  function focusOf(c) {
    const idx = parseInt(String(c.worker).slice(1), 10);
    let plan = null;
    for (const p of D.plans || []) if (p.iter < c.iter) plan = p;
    return plan && plan.focus[idx] ? plan.focus[idx] : "";
  }
  function setIdx(i) {
    playIdx = Math.max(0, Math.min(N - 1, i));
    const c = C[playIdx];
    updateProg(pb.querySelector(".prog"), playIdx);
    updateGrid(pb.querySelector(".cgrid"), playIdx);
    updateGauges(pb.querySelector(".gauges"), playIdx, D.pub);
    pb.querySelector(".slider").value = playIdx;
    pb.querySelector(".ccount").textContent = `#${c.n} / ${C[N - 1].n}`;
    const cap = pb.parentElement.querySelector(".caption");
    const st = c.pass_target ? '<span class="chip good">passes, with margin</span>'
      : c.pass_spec ? '<span class="chip guard">passes spec, inside guard band</span>'
      : `<span class="chip bad">fails ${15 - c.n_spec} corner${15 - c.n_spec === 1 ? "" : "s"}</span>`;
    const tags = [];
    if (playIdx === D.first) tags.push('<span class="chip good">first full pass</span>');
    if (playIdx === D.final) tags.push('<span class="chip good">★ best design</span>');
    if (c.worker === "start") {
      cap.innerHTML = `<div class="cn">#${c.n}</div><div class="cbody"><b>Starting point: the published design.</b> ${st}</div>`;
    } else {
      const w = parseInt(String(c.worker).slice(1), 10);
      const f = focusOf(c);
      cap.innerHTML = `<div class="cn">#${c.n}</div><div class="cbody"><span class="badge h">Hydrogen ${isNaN(w) ? c.worker : w + 1}</span> `
        + (f ? `<span class="cfocus">${esc(f)}</span> ` : "") + `${st} ${tags.join(" ")}<br><span class="crat">“${esc(c.rationale)}”</span></div>`;
    }
  }
  function play(on) {
    const btn = pb.querySelector(".pbtn");
    if (on && playIdx >= N - 1) setIdx(0);
    clearInterval(timer);
    timer = null;
    if (on) {
      timer = setInterval(() => {
        if (playIdx >= N - 1) return play(false);
        setIdx(playIdx + 1);
      }, 650);
    }
    btn.textContent = on ? "❚❚" : "▶";
    btn.setAttribute("aria-label", on ? "pause" : "play");
  }
  if (pb) {
    const q = new URLSearchParams(location.search);
    const qc = q.get("cand");
    let start = 0;
    if (qc !== null) {
      const i = C.findIndex((c) => String(c.n) === qc);
      start = i >= 0 ? i : 0;
      autoplayed = true;
    }
    setIdx(start);
    pb.querySelector(".pbtn").addEventListener("click", () => play(!timer));
    pb.querySelector(".slider").addEventListener("input", (e) => { play(false); setIdx(+e.target.value); });
  }

  // ------------------------------------------------------------------ navigation
  const dots = document.querySelector("nav.dots");
  scenes.forEach((s, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.title = `${i + 1}. ${s.dataset.title}`;
    b.setAttribute("aria-label", b.title);
    b.addEventListener("click", (e) => { e.stopPropagation(); go(i); });
    dots.insertBefore(b, dots.querySelector(".nav.next"));
  });
  const dotBtns = Array.from(dots.querySelectorAll("button:not(.nav)"));
  let cur = 0;
  function mark(i) {
    cur = i;
    dotBtns.forEach((b, k) => b.classList.toggle("on", k === i));
    const sc = scenes[i];
    if (sc && sc.contains(pb) && !autoplayed) { autoplayed = true; setTimeout(() => play(true), 600); }
    if (sc && !sc.contains(pb) && timer) play(false);
  }
  function go(i, instant) {
    i = Math.max(0, Math.min(scenes.length - 1, i));
    scenes[i].scrollIntoView({ behavior: instant ? "auto" : "smooth" });
    mark(i);
  }
  const io = new IntersectionObserver((ents) => {
    for (const e of ents) if (e.isIntersecting && e.intersectionRatio > 0.55) mark(scenes.indexOf(e.target));
  }, { threshold: [0.55, 0.9] });
  scenes.forEach((s) => io.observe(s));
  document.querySelector(".nav.prev").addEventListener("click", (e) => { e.stopPropagation(); go(cur - 1); });
  document.querySelector(".nav.next").addEventListener("click", (e) => { e.stopPropagation(); go(cur + 1); });
  document.addEventListener("keydown", (e) => {
    if (e.target && e.target.tagName === "INPUT" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    const inPlay = scenes[cur] && scenes[cur].contains(pb);
    if (e.key === " " && inPlay) { e.preventDefault(); play(!timer); return; }
    if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(e.key)) { e.preventDefault(); go(cur + 1); }
    else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key)) { e.preventDefault(); go(cur - 1); }
    else if (e.key === "Home") { e.preventDefault(); go(0); }
    else if (e.key === "End") { e.preventDefault(); go(scenes.length - 1); }
    else if (e.key === "p" && pb) play(!timer);
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("button, input, a, .g, .ctrl, nav, .prog, details")) return;
    if (window.getSelection && String(window.getSelection())) return;
    go(cur + 1);
  });
  const m = /^#s(\d+)$/.exec(location.hash);
  if (m) go(+m[1] - 1, true);
  else mark(0);
})();
