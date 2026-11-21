# Multimodal inputs and demo artifacts for a "datasheet to silicon" SKY130 op-amp sizing agent (as of 2026-11-21)

Method note: every TI datasheet listed below was downloaded with curl from the `ti.com/lit/ds/symlink/<part>.pdf` URL on 2026-11-21 (all returned HTTP 200, `application/pdf`). The spec values were read from `pdftotext -layout` output of those PDFs, so they are primary-source values. analog.com PDF URLs timed out from curl (likely bot blocking), and the Microchip `ww1.microchip.com` URL returned an HTML page, not a PDF, so those vendors are less verified (see Gaps). Claims about NEMESIS, OmniSch and the Nau/Krummenauer/Zimmermann SMPS study come from the user's own notes. I did not re-verify them.

## Q1. Which commercial op-amp datasheets are plausible SKY130 two-stage Miller targets at 1.8 V, and which specs map to a testbench?

### Takeaway
The best "hero" datasheets are TI TLV9001 (1 MHz, 60 µA), TLV6001 (1 MHz, 75 µA), TLV9041 (350 kHz, 10 µA) and OPA379 (90 kHz, 2.9 µA). All four are 1.8 V-capable CMOS parts with GBW and IQ in the range that a two-stage Miller op-amp can reach. A two-stage Miller circuit cannot meet their rail-to-rail I/O, their 100+ dB AOL into 2 to 10 kΩ loads, or their EMI, shutdown and offset/noise specs. Drop those specs or scale them, and show the scaling on screen as an explicit "translation" step.

### Cited Findings

Candidate table. Values are typ unless noted. "Conditions" are the datasheet's own. All PDF links were verified as downloadable.

| Part | Supply | GBW | Slew rate | AOL | IQ (per ch) | Phase margin / load | Direct PDF |
|---|---|---|---|---|---|---|---|
| TI TLV9001 | 1.8–5.5 V | 1 MHz (VS=5 V) | 2 V/µs (VS=5 V) | 100 dB (VS=1.8 V, RL=10 kΩ); 115 dB (1.8 V, RL=2 kΩ); 130 dB (5.5 V) | 60 µA typ, 77 µA max (VS=5.5 V) | 78° (VS=5.5 V, G=1); settling spec uses CL=100 pF | [tlv9001.pdf](https://www.ti.com/lit/ds/symlink/tlv9001.pdf) |
| TI TLV6001 | 1.8–5.5 V | 1 MHz | 0.5 V/µs | 90 min / 110 typ dB (RL=2 kΩ) | 75 µA typ, 100 µA max (VS=5 V) | 65° (VS=5 V, G=+1) | [tlv6001.pdf](https://www.ti.com/lit/ds/symlink/tlv6001.pdf) |
| TI TLV9041 | 1.2–5.5 V | 350 kHz (RL=1 MΩ) | 0.2 V/µs (CL=10 pF) | 98 dB (1.2 V, RL=10 kΩ); 105 dB (1.2 V, RL=100 kΩ) | 10 µA typ, 13 µA max | 65° (G=+1, RL=100 kΩ, CL=10 pF); rated for 100 pF cap load | [tlv9041.pdf](https://www.ti.com/lit/ds/symlink/tlv9041.pdf) |
| TI OPA379 | 1.8–5.5 V | 90 kHz | 0.03 V/µs (G=+1) | 100 min / 120 typ dB (VS=5 V, RL=25 kΩ) | 2.9 µA typ, 5.5 µA max | not extracted | [opa379.pdf](https://www.ti.com/lit/ds/symlink/opa379.pdf) |
| TI TLV9061 | 1.8–5.5 V | 10 MHz (VS=5 V, G=+1) | 6.5 V/µs | 100 dB (1.8 V, RL=10 kΩ) | 538 µA typ, 750 µA max | 55° (VS=5 V, G=+1); datasheet text mentions a 100 pF load | [tlv9061.pdf](https://www.ti.com/lit/ds/symlink/tlv9061.pdf) |
| TI TLV9051 | 1.8–6.0 V | 5 MHz (VS=5.5 V) | 15 V/µs (CL=130 pF) | 106 dB (1.8 V, RL=10 kΩ) | 330 µA typ, 450 µA max | 60° (VS=5.5 V, G=+1) | [tlv9051.pdf](https://www.ti.com/lit/ds/symlink/tlv9051.pdf) |
| TI TLV521 (nanopower) | 1.8–5.5 V | 6 kHz (CL=20 pF, RL=100 kΩ) | 2.5 V/ms (front page); table lists 2.9 for the falling edge | 80 min / 110 typ dB | 350 nA typ, 500 nA max (V+=3.3 V) | 73°, gain margin 19 dB (CL=20 pF, RL=100 kΩ) | [tlv521.pdf](https://www.ti.com/lit/ds/symlink/tlv521.pdf) |
| TI LPV521 (nanopower) | 1.8 V min ("1.8V" in title) | 6.2 kHz | 2.4 V/ms | not extracted | 351 nA typ, 400 nA max | not extracted | [lpv521.pdf](https://www.ti.com/lit/ds/symlink/lpv521.pdf) |
| TI TLV8541 (nanopower) | 1.7–3.6 V | 8 kHz | not extracted | not extracted | 500 nA | not extracted | [tlv8541.pdf](https://www.ti.com/lit/ds/symlink/tlv8541.pdf) |
| Microchip MCP6001 | 1.8–6.0 V (family spec) | 1 MHz | 0.6 V/µs (memory, unverified) | not verified | 100 µA typ (50 min, 170 max, VDD=5.5 V) | 90° (G=+1, 60 pF); 45° with 500 pF | Mirror copy of the official PDF: [MCP6004.pdf at USC](https://ece-classes.usc.edu/ee459/library/datasheets/MCP6004.pdf). Product page: https://www.microchip.com/en-us/product/mcp6001 |

- The values in the table come from the TI PDFs linked in each row, specifically their electrical-characteristics tables (TLV9001 table header: "For VS = 1.8 V to 5.5 V, TA = 25°C, RL = 10 kΩ connected to VS / 2 ... unless otherwise noted") — [TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf)
- The TLV9041 electrical table is measured with "G = 10 V/V, RF = 180 kΩ, CF = 0.24 pF, RL = 100 kΩ" as default conditions. This is a reminder that table-wide conditions sit in a header sentence, not in the rows — [TLV9041](https://www.ti.com/lit/ds/symlink/tlv9041.pdf)
- MCP6001: GBW 1 MHz; PM 90° typ at G=+1 with 60 pF; 45° with 500 pF; IQ 50/100/170 µA at VDD = 5.5 V. These come from search snippets of mirrored copies of the official datasheet, and the official revision was not opened — [USC mirror](https://ece-classes.usc.edu/ee459/library/datasheets/MCP6004.pdf), [DigiKey highlight](https://www.digikey.com/en/product-highlight/m/microchip-technology/general-purpose-mcp6001-2-op-amps)
- Microchip's old `ww1.microchip.com/downloads/en/DeviceDoc/...DS20001733L.pdf` URL returned HTML, not a PDF, to curl on 2026-11-21, and the aemDocuments path returned 403. Download it by hand from the product page in a browser. (This is my own test.)
- TLV521 is internally a parallel NMOS + PMOS input pair (rail-to-rail input), "When VCM is near V+, the NMOS pair is on..." This architecture is unlike a single-pair Miller OTA — [TLV521](https://www.ti.com/lit/ds/symlink/tlv521.pdf)

How datasheet specs map to a SKY130 ngspice testbench:

| Datasheet spec | Testbench measure | Keep / scale / drop |
|---|---|---|
| GBW / UGF | AC open-loop: frequency where \|A\|=0 dB (`.meas ac ... WHEN vdb(out)=0`) | Keep. Run at VDD=1.8 V, not the datasheet's 5 V |
| Phase margin (G=+1, CL) | AC: 180° + phase at UGF, with the datasheet's CL (10 pF for TLV9041, 60 pF for MCP6001) | Keep, and copy CL from the datasheet condition |
| Slew rate | Transient: unity-gain follower, step input, dV/dt 10 to 90% | Keep. Scale the step to fit within 1.8 V |
| AOL | AC DC gain | Scale. 100 to 130 dB into 2 to 10 kΩ is not reachable with an unbuffered two-stage. Use RL=open or 1 MΩ and a target of about 70 to 80 dB |
| IQ | DC: current from VDD (`i(vdd)`) | Keep. Note that datasheet IQ is per channel at VS=5.5 V |
| Supply range | Corner sweep at 1.62/1.8/1.98 V | Replace with SKY130 corners (tt/ff/ss/sf/fs, −40/27/125 °C) |
| CMRR, PSRR | AC with CM or supply injection | Optional (easy to add) |
| Output swing | DC sweep / transient clipping | Scale. A two-stage CMOS output can swing near the rails, but only with a light load |
| Rail-to-rail input / VCM range | DC sweep of VCM | Drop. A single diff pair cannot do it |
| Offset (VOS), drift, noise, 1/f, EMIRR, shutdown, ESD, IB | n/a or needs Monte Carlo / noise analysis | Drop (offset needs mismatch MC; noise is an optional `.noise` stretch) |
| Settling time | Transient | Optional |

### Inferences
- The most narratively compelling pick is TLV9001: a 1 MHz, 60 µA, 78° PM, 1.8 V-capable part. "Clone the TLV9001 core specs in open-source silicon" is a one-line pitch. TLV9041 (10 µA, 350 kHz, PM 65° at 10 pF) is the safest to meet at every corner. Use TLV9041 or TLV6001 as the live demo and keep TLV9001 as the hero slide if time allows.
- TLV9051 (15 V/µs into 130 pF) and TLV9061 (10 MHz) are "stretch/failure" examples. Slew of 15 V/µs into 130 pF needs about 2 mA of slewing current (I = C·SR), against a 330 µA datasheet IQ. A two-stage class-A design cannot do that. Showing the agent flag a spec as "infeasible in SKY130 two-stage, here is why" is itself a good demo moment.
- The nanopower parts (TLV521, LPV521, TLV8541) need subthreshold design and transients lasting milliseconds. Leakage at ff/125 °C will also eat the nA budget. Avoid them for a 6.5-hour build.
- Datasheet typ values are mostly quoted at 5 to 5.5 V and 25 °C. The agent should treat "typ" as the target and "min/max" as the hard spec, applied at 1.8 V across corners. Show this policy explicitly.

### Gaps
- ADI/Maxim parts (e.g., ADA4505, MAX4470, MAX40007) could not be verified. analog.com PDF URLs timed out from curl, and I did not confirm the specs or links.
- MCP6041 (600 nA class) specs and a working official PDF link were not verified.
- MCP6001 slew rate and AOL were not verified from a primary source.
- OPA379 phase margin and its CL condition were not extracted.

## Q2. JSON spec schema for datasheet extraction, and LLM table-extraction pitfalls

### Takeaway
Extract one record per (parameter, condition) row with separate min/typ/max fields, the unit as printed, the normalized SI value, the table-wide default conditions, and the page number and row text for provenance. Then run a deterministic validator (unit sanity, min ≤ typ ≤ max, a whitelist of mappable parameters) before anything reaches the sizing loop. The main pitfalls are multi-row conditions, table-wide header conditions, mixed units (V/ms vs V/µs), per-channel vs total values, and multiple part variants per row.

### Cited Findings
- Gemini PDF input: up to 50 MB and 1,000 pages per PDF, inline or via the Files API. Pages are processed by native vision and rendered at about 2 MP / 288 DPI, plus embedded PDF text at no extra token cost. Gemini 4/3 use about 280/560/1,120 tokens per page at low/medium (default)/high resolution. Files API uploads are stored for 48 hours. Code examples use model `gemini-4-hydrogen`. Advice: "For a single page, place the text prompt after the document" — [Gemini API docs: document processing](https://aistudio.google.com/docs/document-processing)
- That doc page links to a structured-output guide but does not itself describe `response_schema`. Confirm the JSON-schema parameter name in the structured-output docs before coding — [Gemini docs](https://aistudio.google.com/docs/document-processing)
- In the TLV9001 datasheet, a single AOL parameter has four condition rows (1.8 V/10 kΩ: 100 dB; 1.8 V/2 kΩ: 115 dB; 5.5 V/2 kΩ: 130 dB). In pdftotext output the numbers land on separate lines from the conditions, so text-only extraction easily mismatches value and condition — [TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf) (my own extraction)
- The TLV9001 IQ row is split by part variant ("TLV9001, TLV9001S ... 60 / 77 µA") with an over-temperature sub-row (85 µA max) — [TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf)
- Units vary between parts. TLV521 gives slew rate in V/ms (2.5 V/ms on the front page, 2.9 for the falling edge in the table, so the front page and the table disagree). GBW appears in kHz and in MHz — [TLV521](https://www.ti.com/lit/ds/symlink/tlv521.pdf)
- The table-wide default conditions differ by part: TLV9001 uses RL = 10 kΩ, and TLV9041 uses G = 10 V/V, RF = 180 kΩ, CF = 0.24 pF, RL = 100 kΩ — [TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf), [TLV9041](https://www.ti.com/lit/ds/symlink/tlv9041.pdf)
- Related work on LLM datasheet extraction: D2S-FLOW (LLM parameter extraction from datasheets for SPICE model generation) — [arXiv 2502.16540](https://arxiv.org/abs/2502.16540v2); DocEDA (layout analysis plus LLM chain-of-thought to extract parameters from circuit documents and design analog circuits) — [arXiv 2412.05301](https://arxiv.org/pdf/2412.05301); multimodal HEMT model extraction from datasheets, noting that generic PDF tools lose information on datasheet layouts — [arXiv 2507.21430](https://arxiv.org/pdf/2507.21430); AMSbench (MLLM benchmark for AMS circuits) — [arXiv 2505.24138](https://arxiv.org/pdf/2505.24138)

Proposed schema (one file per datasheet):

```json
{
  "part": "TLV9001",
  "vendor": "Texas Instruments",
  "source_pdf": "https://www.ti.com/lit/ds/symlink/tlv9001.pdf",
  "default_conditions": {"VS_V": [1.8, 5.5], "TA_C": 25, "RL_ohm": 10000, "RL_to": "VS/2", "VCM": "VS/2"},
  "specs": [
    {
      "id": "aol_1v8_10k",
      "param": "open_loop_gain",          // enum: gbw, phase_margin, slew_rate, open_loop_gain, iq, cmrr, psrr, output_swing, vos, noise, ...
      "symbol": "AOL",
      "min": null, "typ": 100, "max": null,
      "unit_printed": "dB",
      "si": {"typ": 100, "unit": "dB"},
      "conditions": {"VS_V": 1.8, "RL_ohm": 10000, "VO_range": "(V-)+0.04 .. (V+)-0.04"},
      "temp_range": null,                  // e.g. [-40,125] for over-temperature rows
      "variant": null,                     // e.g. "TLV9001S"
      "per_channel": true,
      "page": 7,
      "row_text": "AOL Open-loop voltage gain VS = 1.8 V ... RL = 10 kΩ 100 dB",
      "confidence": 0.9
    }
  ],
  "sky130_target": {                       // produced by a separate translate step, not by extraction
    "vdd_V": 1.8, "corners": ["tt","ff","ss","sf","fs"], "temps_C": [-40, 27, 125],
    "gbw_Hz": {"min": 1.0e6}, "pm_deg": {"min": 60}, "sr_V_per_us": {"min": 2.0},
    "aol_dB": {"min": 70, "note": "scaled: datasheet 100 dB into 10 kΩ not achievable unbuffered"},
    "iq_A": {"max": 77e-6}, "cl_F": 100e-12,
    "dropped": ["rail_to_rail_input", "vos", "emirr", "shutdown", "noise"]
  }
}
```

### Inferences
- Use two passes. (1) Gemini reads the electrical-characteristics pages as images and returns the `specs` list under a strict JSON schema. (2) Python checks min ≤ typ ≤ max, normalizes units (µ/u, V/ms → V/µs), and cross-checks each number against `pdfplumber`/`pdftotext` text of the same page. A value that does not appear in the page's text layer is flagged as a hallucination. This exploits the fact that these TI PDFs have a real text layer.
- Ask for `page` and `row_text` on every record. They give provenance for the demo ("click a spec, see the datasheet row") and make errors easy to catch live.
- Keep the "translate to SKY130 targets" step separate from extraction, and print what was dropped and why. Judges will probe this, and it shows engineering judgment rather than blind copying.
- Restrict extraction to pages that contain "Electrical Characteristics", using `page_options` slicing or by pre-splitting the PDF. This saves tokens and avoids pulling typical-performance graph values (TLV9001 is 8 MB and many pages).

### Gaps
- I found no benchmark that reports Gemini's accuracy specifically on op-amp electrical-characteristics tables. Treat accuracy as unknown and validate on the chosen datasheet before the demo.
- The structured-output parameter names for the current Gemini 4 SDK were not verified.

## Q3. Schematic image sources and rendering a netlist back to a schematic in Python

### Takeaway
Do not depend on copyrighted textbook figures. Use three self-made or open inputs: (a) a hand-drawn photo the team makes on paper or a whiteboard (the most "camera"-native and fully legitimate input), (b) an xschem screenshot from an open-source SKY130 two-stage Miller repo or the team's own xschem file, and (c) a schemdraw-rendered figure produced by the tool itself. For rendering, schemdraw 0.23 has MOSFET elements and needs no LaTeX, so it is the fastest path. lcapy can draw nmos/pmos but needs LaTeX/CircuiTikZ. netlistsvg is digital-first, and its analog skin does not confirm MOSFET symbols.

### Cited Findings
- Open SKY130 two-stage Miller op-amp projects with xschem folders: caravel_fulgor_opamp (test chip with several two-stage Miller op-amps, upstream GitHub diegohernando/caravel_fulgor_opamp) — [Google Git mirror slot-019](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-001/slot-019); rohinthram/avsd_opamp (two-stage CMOS op-amp, SKY130, `xschem/` folder) — [slot-021 mirror](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-003/slot-021); sloci_caravel compensated op-amp `.sch` — [opamp_realcomp3_usefinger.sch](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-007/slot-036/+/HEAD/xschem/opamp_realcomp3_usefinger.sch). Licenses were not checked. Efabless shuttle repos are typically Apache-2.0, but verify this.
- A student write-up of a two-stage CMOS OTA with schematic figures — [squareybrow.github.io two_stage_cmos_ota](https://squareybrow.github.io/two_stage_cmos_ota/). License unknown, so use it only as a reference, not as a demo input.
- Wikimedia Commons has `File:Op-Amp Internal.svg`, but it is the 741-style BJT internal diagram, not a CMOS two-stage — [Wikipedia file page](https://www.wikipedia.org/wiki/File:Op-Amp_Internal.svg). I found no Commons SVG of a CMOS two-stage Miller op-amp.
- xschem CLI: `--png` and `--svg` export a schematic, and `-q/--quit` quits after doing things. Some versions document `--plotfile <file>` as the output file for export — [xschem man page (Ubuntu noble)](https://manpages.ubuntu.com/manpages/noble/man1/xschem.1.html), [Debian trixie man page](https://dyn.manpages.debian.org/trixie/xschem/xschem.1). A plausible headless command is `xschem --svg --plotfile out.svg -q opamp.sch`, but I have not tested it. xschem may also need an X display (use `xvfb-run` if it does).
- schemdraw 0.23 (installed and tested on 2026-11-21) provides `NFet, PFet, NMos, PMos, AnalogNFet, AnalogPFet, AnalogBiasedFet, Opamp`. `AnalogNFet` exposes the anchors `gate, source, drain, center`, and `.label()` plus `Drawing.save('x.svg')` work without LaTeX (my own test). Op-amp gallery: [schemdraw Opamp Circuits](https://schemdraw.readthedocs.io/en/latest/gallery/opamp.html)
- lcapy: semi-automatic placement. "Each component requires a specified orientation: up, down, left, right." It draws MOSFETs (`kind` nmos/pmos/nfet/pfet), which "can be drawn but not analyzed". Output formats are tex/schtex/pgf/png/svg/pdf, and it "can generate high quality schematics from a netlist using the Circuitikz LaTeX package", so it needs LaTeX — [lcapy schematics docs](https://lcapy.readthedocs.io/en/latest/schematics.html)
- netlistsvg "draws an SVG schematic from a yosys JSON netlist". Its analog skin example uses `r_v` and `q_pnp`, the README does not confirm MOSFET symbols, and there is no SPICE input — [netlistsvg README](https://github.com/nturley/netlistsvg)

### Inferences
- The fastest reliable path is a hand-written schemdraw template of the fixed 8-transistor two-stage Miller topology: M1/M2 diff pair, M3/M4 mirror load, M5 tail, M6 second stage, M7 second-stage bias, M8 bias reference, plus Cc and an optional Rz. Each device's fill or label color comes from the role JSON the model produced. The topology is fixed, so auto-layout is unnecessary. This takes about 45 to 60 minutes and avoids LaTeX installs.
- Demo flow for the "camera" input: someone sketches the two-stage op-amp on paper, photographs it with a phone webcam, and Gemini returns a role map keyed to the device names in the netlist (netlist = ground truth). The tool then re-renders a clean color-coded schematic. This matches the user's NEMESIS / OmniSch evidence: use the image to label roles, never to extract the full netlist.
- Keep the xschem screenshot as backup input. If headless xschem export fails, a manual screenshot is fine.

### Gaps
- License terms of the SKY130 shuttle repos were not verified.
- Headless xschem export was not tested.

## Q4. Visual outputs that are not a dashboard; what past AI-for-EDA demos looked like

### Takeaway
Build the demo as a story driven by an input (PDF or photo in, silicon out), not as a panel of widgets. Strong artifacts are: (1) the datasheet page with the extracted rows highlighted; (2) the role-colored schematic generated from the photo; (3) a Bode "before/after" overlay against the datasheet's GBW/PM targets; (4) a corner "fan" plot (all PVT corners' Bode or step responses overlaid, with a pass/fail band); (5) a final "datasheet vs SKY130" comparison spec sheet rendered as a PDF in datasheet style (a generated document is itself a multimodal output); (6) spoken narration of each agent iteration.

### Cited Findings
- Gemini understands non-speech sounds, and real-time voice and video go through the Live API — [Gemini audio docs](https://aistudio.google.com/docs/audio)
- Prior multimodal circuit work converts schematic images to netlists: AMSNet (a partly automated schematic-to-netlist pipeline) — [arXiv 2405.09045](https://arxiv.org/abs/2405.09045v2); AMSnet 2.0 (2,686 circuits with schematic images and netlists) — [arXiv 2505.09155](https://arxiv.org/html/2505.09155v1); Masala-CHAI (automated SPICE netlist extraction from about 7,500 textbook schematics; fine-tuning on it improved AnalogCoder Pass@1 by 46%) — [arXiv 2411.14299](https://arxiv.org/html/2411.14299v5); AnalogCoder-Pro (a multimodal LLM framework for joint topology and sizing) — [arXiv 2508.02518](https://arxiv.org/pdf/2508.02518v1)

### Inferences
- Concrete artifact list, in priority order for a 6.5-hour build:
  1. **Highlighted datasheet page.** Render the electrical-characteristics page to PNG (`pdftoppm`) and draw boxes on the rows used. Get the coordinates by locating `row_text` with pdfplumber, not from Gemini bounding boxes. Cost is about 30 minutes.
  2. **Role-colored schematic** (schemdraw, see Q3).
  3. **Bode overlay per iteration** (matplotlib). Draw a horizontal line at 0 dB, a vertical marker at the datasheet GBW, and annotate PM. Animate the iterations as a GIF or sequence. Cost is about 30 minutes.
  4. **Corner fan plot.** Overlay all 15 (5 process × 3 temperature) Bode curves or follower step responses, shading the region that meets the spec, with the worst corner in red. This is the most "silicon-real" visual.
  5. **Generated "SKY130 clone" datasheet PDF.** One page with the same table layout as the TI part, columns "TLV9001 typ | SKY130 worst-corner | status", plus the schematic and Bode. This closes the "datasheet in, datasheet out" loop.
  6. **Voice narration.** Gemini TTS or the Live API speaks each iteration ("PM is 48° at ss/125 °C, increasing Cc to 1.2 pF"). The narration is a byproduct of the agent's reasoning, so it is cheap.
- A dashboard ban likely means that live plots should appear inside a linear narrative (input → reasoning → artifact), not as a grid of KPIs. A single-page "lab notebook" or a chat-style transcript with embedded images avoids looking like a dashboard.

### Gaps
- I did not find documented examples or videos of what won past AI-for-EDA hackathons or demos. That evidence is missing, not negative.
- Whether Gemini 4 returns reliable bounding boxes for PDF table rows was not checked.

## Q5. Is audio (a filter or preamp judged by listening) a better multimodal angle?

### Takeaway
Not as the primary angle. Datasheet PDF plus schematic photo is more central to the sizing task, matches the evidence in the user's notes (datasheet retrieval plus SPICE helped a lot in the SMPS study), and is lower risk. Audio works best as a cheap add-on: (a) voice input for the spec ("make me a TLV9001 but at half the power"), (b) spoken narration, and optionally (c) a short "ear test" that pushes a real audio clip through the designed amplifier's transient simulation.

### Cited Findings
- Gemini accepts WAV, MP3, FLAC and other formats, up to 9.5 hours per prompt, at 32 tokens per second of audio. It "understands non-speech sounds (birdsong, sirens, etc.)", and the docs say nothing about judging audio fidelity or distortion — [Gemini audio docs](https://aistudio.google.com/docs/audio)
- The user's own notes cite the SMPS study (Nau, Krummenauer, Zimmermann), where datasheet retrieval plus SPICE raised the solve rate from 15% to 91%. This supports the datasheet angle. (From the user's notes, not re-verified.)

### Inferences
- Feasibility of the audio ear test: a 1 to 2 second clip as a PWL source in an ngspice transient with a µs-scale timestep is about 10^6 points. That is probably tens of seconds to minutes per run, so it is fine for one final "before/after" playback but too slow for the optimization loop. The audible differences an op-amp makes (slew-induced distortion and clipping at high amplitude or frequency) appear only if the design is deliberately underpowered. Phase-margin ringing is mostly inaudible. Judging by ear is therefore a weak objective for a spec-driven sizing agent.
- Gemini's ability to rate distortion or THD by listening is undocumented. Compute THD with an FFT in Python and use audio only as a human-facing "listen to it" moment.
- Recommended allocation: put the core 5 hours into datasheet → spec → sizing → corner verification → schematic-role photo. Spend at most 30 to 45 minutes on voice input and narration via the Live API or TTS. Treat the audio ear test as a stretch goal, only if the core is done by T−1.5 h.

### Gaps
- No measured ngspice runtime for an audio-length transient of a SKY130 two-stage op-amp. Benchmark it quickly before committing.
- No source on whether hackathon judges at this event weight audio over documents. The event rules only say "voice, camera, video, images, documents or generated media".

## Risks summary (cross-cutting)

### Takeaway
The biggest risks are wrong datasheet numbers being extracted silently, unachievable specs (AOL into heavy loads, rail-to-rail input, high slew into large CL) causing endless failed iterations, and the schematic-image step becoming a netlist-extraction attempt (which OmniSch, per the user's notes, says is unreliable).

### Cited Findings
- Datasheet tables split one parameter across several condition and variant rows, and units vary (V/ms vs V/µs) — [TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf), [TLV521](https://www.ti.com/lit/ds/symlink/tlv521.pdf)
- Microchip's legacy datasheet URL did not return a PDF to a scripted client, and analog.com timed out (my own test, 2026-11-21). Pre-download every PDF into the repo before the demo, and do not fetch live.

### Inferences
- Mitigations: pre-cache the PDFs; validate extracted values against the PDF text layer; use a hard-coded spec-translation table (keep, scale, drop) with on-screen justification; cap optimizer iterations and add an "infeasible, here's why" exit; label roles from the image only with the netlist as ground truth; keep a pre-recorded fallback run of the full pipeline in case the network or API fails during judging.
