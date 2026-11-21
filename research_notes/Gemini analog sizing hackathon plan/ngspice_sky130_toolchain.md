# ngspice + SKY130 toolchain for an LLM-guided two-stage Miller op-amp sizing loop (as of 2026-11-21)

Scope note: findings come from web sources (cited inline) plus a direct inspection of the PDK that is already installed on this laptop (`~/.ciel`, cited as "local inspection"). ngspice is **not** installed on the laptop yet (`which ngspice` returns nothing), so no simulation timings could be measured in this session. Optuna is also not installed. Docker and podman are both present (`/usr/bin/docker`, `/usr/bin/podman`). The machine has 16 logical CPUs (`nproc` = 16).

## Q1. Fastest way to get ngspice + SKY130 models on Arch Linux; gotchas; Docker alternative; simulation time

### Takeaway
Run `sudo pacman -S ngspice`. Arch `extra` ships ngspice 47-1, which is the newest upstream release (47, 2026-08-11). The SKY130 PDK is **already installed** on this laptop through ciel at `~/.ciel/sky130A`. That is a minimal 211 MB install that holds only `sky130_fd_pr`, the model library needed. Copy the PDK's own `spinit` to `~/.spiceinit` (or to each sim's working directory) so `ngbehavior=hsa`, `skywaterpdk`, `ng_nomodcheck` and KLU are turned on. IIC-OSIC-TOOLS is a heavy fallback (about 4 GB compressed, 20 GB on disk), and with a native setup already in place it is not worth the time.

### Cited Findings
- Arch package `ngspice` is in the **Extra** repo, version **47-1**, last updated **2026-08-19**. — [archlinux.org package search](https://archlinux.org/packages/?q=ngspice)
- Latest upstream release is **ngspice-47 (2026-08-11)**. It added a `pyplot` command (matplotlib plotting), small-signal noise for code models, PSS updates and a corrected memory measurement. ngspice-46 (2026-03-29) added `vec()`/`var()` functions and voltage-based truncation error correction, and enabled CIDER with KLU. ngspice-45 (2025-09-01) added `save nointernals` and an enhanced `.spiceinit` search path sequence. — [ngspice news](https://ngspice.sourceforge.io/news.html)
- ngspice-42 (2023-12-27) added KLU to Sparse and sped up "parsing of large PDKs (e.g. SkyWater)". ngspice-43 made KLU a standard configure option. OSDI/Verilog-A support (OpenVAF) has existed since ngspice-39. ngspice-44 added shared-library API functions to reset ngspice or skip reading `.spiceinit`. — [ngspice news](https://ngspice.sourceforge.io/news.html); [Debian NEWS file](https://sources.debian.org/src/ngspice/45.2%2Bds-1/NEWS)
- AnalogGym warns against the conda-provided ngspice-41 and recommends ngspice ≥ 42. — [AnalogGym docs](https://coda-team.github.io/AnalogGym)
- **Installed PDK (local inspection):** `~/.ciel/sky130A` is a symlink to `~/.ciel/ciel/sky130/versions/2fdf40d5736413bb321c31418462aefc17b919b8/sky130A`, and `sky130B` is also present. `~/.ciel/ciel/sky130/current` = `2fdf40d5736413bb321c31418462aefc17b919b8`. `libs.ref/` contains only `sky130_fd_pr`, with 675 SPICE files (48 MB). `libs.tech/ngspice` is 4.2 MB. The whole of `~/.ciel` is 211 MB. — local inspection of `/home/thinkies/.ciel`
- **The PDK's shipped `spinit` (local inspection)**, at `~/.ciel/sky130A/libs.tech/ngspice/spinit`:
  ```
  * .spiceinit for use with Skywater PDK and ngspice KLU
  set ngbehavior=hsa     ; set compatibility for reading PDK libs
  set skywaterpdk        ; skip some checks for faster lib loading
  set ng_nomodcheck      ; don't check the model parameters
  set num_threads=8      ; CPU processor cores available
  option noinit          ; don't print operating point data
  option klu             ; select KLU as matrix solver
  * optran 0 0 0 100p 2n 0 ; don't use dc operating point, but transient op
  ```
  An older open_pdks spinit had only `set ngbehavior=hsa` ("assert BSIM compatibility mode with nf vs. W") and `set ng_nomodcheck` ("speeds up loading time"). — [open_pdks sky130 spinit](https://foss-eda-tools.googlesource.com/third_party/opencircuitdesign.com/open_pdks/+/746f76dbee35bc2f7da0d0e071ebeb78d97c5b0f/sky130/custom/models/spinit)
- **Corner sections in `sky130.lib.spice` (local inspection):** `tt`, `sf`, `ff`, `ss`, `fs`, `ll`, `hh`, `hl`, `lh`, plus combined sections such as `sf_ll`, `ff_hh`, `ss_hl`. The `tt` section sets `mc_mm_switch=0` and `mc_pr_switch=0`, then includes `corners/tt.spice`, `r+c/res_typical__cap_typical.spice`, `r+c/res_typical__cap_typical__lin.spice` and `corners/tt/specialized_cells.spice`. — local inspection of `~/.ciel/sky130A/libs.tech/ngspice/sky130.lib.spice`
- **What a corner loads (local inspection):** `corners/tt.spice` includes about 28 device families: 1.8 V nfet/pfet, lvt/hvt, 3v3/5v0 nvt, g5v0d10v5, 20 V devices, ESD, npn, plus `../all.spice` and `tt/rf.spice`. The `sky130_fd_pr__nfet_01v8__tt.pm3.spice` file alone has 180 `.model` bins. `all.spice` sets `.option scale=1.0u`, so W and L on sky130 instances are in **microns** (W=1 means 1 µm). — local inspection
- ciel installs with `python3 -m pip install --user --upgrade --no-cache-dir ciel`. The default PDK root is `~/.ciel` (override with `PDK_ROOT` or `--pdk-root`). Commands are `ciel ls-remote --pdk-family sky130`, `ciel enable --pdk-family sky130 <open_pdks hash>` and `ciel ls --pdk-family sky130`. Ciel is "based on Volare". — [ciel GitHub](https://github.com/fossi-foundation/ciel); [Tiny Tapeout Magic guide](https://tinytapeout.com/guides/laying-standard-cells-with-magic/)
- **IIC-OSIC-TOOLS** ships sky130A, gf180mcuD, ihp-sg13g2 and ihp-sg13cmos5l, plus ngspice "with OSDI support". The image is about 4 GB compressed, and the README recommends at least 20 GB free. Start it with `./start_shell.sh` (shell only), `./start_vnc.sh` or `./start_x.sh`, or the one-line installer `curl -fsSL https://osic.tools/install.sh | bash`. Switch PDKs with `sak-pdk sky130A` or `sak-pdk gf180mcuD`. For sky130A it sets `SPICE_USERINIT_DIR=$PDKPATH/libs.tech/ngspice`. — [IIC-OSIC-TOOLS](https://github.com/iic-jku/IIC-OSIC-TOOLS)

### Inferences
- **Recommended setup recipe (about 10 min total):**
  ```bash
  sudo pacman -S --needed ngspice python python-pip          # ~1 min, ngspice 47
  export PDK_ROOT=$HOME/.ciel PDK=sky130A
  export SKY130=$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice
  cp $PDK_ROOT/sky130A/libs.tech/ngspice/spinit ~/.spiceinit
  sed -i 's/num_threads=8/num_threads=1/' ~/.spiceinit    # use 1 thread per process when running N parallel ngspice processes
  python -m venv .venv && . .venv/bin/activate
  pip install numpy scipy pandas optuna cma schemdraw matplotlib pygmid
  # Only if gf180 is needed later:
  pip install ciel && ciel ls-remote --pdk-family gf180mcu && ciel enable --pdk-family gf180mcu <hash>
  ```
  Netlists then use `.lib "/home/thinkies/.ciel/sky130A/libs.tech/ngspice/sky130.lib.spice" tt`. Swap `tt` for ff, ss, sf or fs to change the corner.
- **Model-loading speed hack.** The full `.lib ... tt` pulls in about 28 device families. A two-stage op-amp needs only `nfet_01v8`, `pfet_01v8` and maybe a sky130 resistor or MIM cap. Using ideal R and C for Rz and Cc is fine for sizing. A trimmed custom corner file loads much faster: it would include only `parameters/lod.spice`, the `all.spice` params, `sky130_fd_pr__nfet_01v8__<c>.pm3.spice`, `sky130_fd_pr__pfet_01v8__<c>.corner.spice` and their mismatch files, with `mc_mm_switch=0`. Test that a trimmed corner matches the full `.lib` result before relying on it.
- **Gotcha:** if `.spiceinit` is not found, sky130 models load slowly and `nf`/`W` handling may be wrong. ngspice reads `.spiceinit` from the current directory or `$HOME` (the search order was changed in ngspice-45). Putting it in `$HOME` covers all per-sim temporary directories.
- **Gotcha:** sky130 FETs are subcircuits (`X` instances). Instantiate them as `XM1 d g s b sky130_fd_pr__nfet_01v8 L=0.5 W=4 nf=1 ...`, never as `M1`.
- Do not use the Docker image unless native ngspice fails. Pulling 4 GB and debugging X11/VNC would cost 20 to 40 minutes for no benefit here.

### Gaps
- **No measured wall-clock time for one AC + transient run of a ~10-transistor sky130 op-amp.** ngspice was not installed, and no source gave a number. The team should time this in the first 15 minutes, separately for model load and for analysis (e.g. `time ngspice -b tb.sp`). I expect model parsing to dominate per-run time, so batching several analyses into one ngspice invocation matters. This is an expectation, not a measured result.
- I did not confirm whether `set skywaterpdk` is documented in the ngspice 47 manual. It appears in the PDK-shipped spinit.

## Q2. Driving ngspice from Python; corner switching; safe parallelism

### Takeaway
Use **subprocess batch mode**: `ngspice -b deck.sp` with a `.control` block that runs `op`, `ac` and `tran`, computes `meas` results, prints scalars and writes to files with `wrdata` or `print`. Give each job its own temporary directory and run jobs from a `concurrent.futures.ProcessPoolExecutor`. PySpice is effectively unmaintained (last release 1.5, 2021-05-15, "supports Ngspice up to version 34"), and shared-library mode needs one library instance per thread. Neither is worth the risk on hackathon day.

### Cited Findings
- PySpice latest is **1.5 (2021-05-15)**. Its release notes say it supports "Ngspice up to version 34", and the maintainer calls it a spare-time project. — [PyPI PySpice](https://pypi.org/project/PySpice/)
- PySpice can simulate through a subprocess ("server mode") or the shared library. Its README flags a shared-library locale bug that needs `setlocale(LC_NUMERIC, "C")`. — [PyPI PySpice](https://pypi.python.org/pypi/PySpice/1.1.3)
- ngspice can be built as a shared library (`libngspice.so`). Its parallel mode has a master program load several ngspice library instances, each in its own thread. — [ngspice shared library](https://ngspice.sourceforge.io/shared.html); [ngspice parallel](https://ngspice.sourceforge.io/parallel.html)
- CircuitGenome's SPICE-verification module runs decks with `ngspice -b`, collects data through `wrdata` (or stdout from `print`), and reads per-device operating points through `@m.xdut.<ref>[...]`. — [CircuitGenome spice_sim docs](https://circuitgenome.readthedocs.io/en/latest/api/sizer/shared/spice_sim.html)
- AnalogGym switches corners by changing which PDK corner file is included, and switches V/T with `.PARAM supply_voltage=...` and `.temp ...`. — [AnalogGym docs](https://coda-team.github.io/AnalogGym)
- AMPICBench switches corners by editing the corner name at the end of the `.lib` line and the `.temp` value. — [AMPICBench](https://github.com/AMPICLab/AMPICBench)

### Inferences
- **Pattern for the sim farm:**
  ```python
  # sim.py: one function = one (design, corner, temp, vdd) job
  import subprocess, tempfile, pathlib, re
  LIB = "/home/thinkies/.ciel/sky130A/libs.tech/ngspice/sky130.lib.spice"
  def run(deck_text: str, timeout=60) -> dict:
      with tempfile.TemporaryDirectory() as d:
          p = pathlib.Path(d, "tb.sp"); p.write_text(deck_text)
          r = subprocess.run(["ngspice", "-b", "tb.sp"], cwd=d, capture_output=True,
                             text=True, timeout=timeout)
          out = r.stdout
          # 'meas' results print as "name = value"; or use 'echo RES name $&name' in .control
          return {k: float(v) for k, v in re.findall(r"^RES (\w+) ([-+0-9.eE]+)", out, re.M)}
  # from concurrent.futures import ProcessPoolExecutor; with ProcessPoolExecutor(14) as ex: ...
  ```
  The template sets `.lib "{LIB}" {corner}`, `.temp {T}` and `.param vdd={1.8*k}`. A PVT sweep of 5 corners × 3 temps × 3 supplies is 45 decks per candidate. On 14 workers that takes about 4 rounds of single-sim latency.
- **Cheaper PVT strategy:** use a reduced "worst-case" set during optimization, for example ss/-40/1.62 V, ss/125/1.62 V, ff/-40/1.98 V, ff/125/1.98 V, sf and fs at extremes, plus tt/27/1.8 V. Run the full 45 only on candidates that pass. This keeps the BO baseline and the LLM agent on equal simulation budgets, but count every deck run toward the budget.
- Use `subprocess` with a timeout. Non-converging sizes sometimes hang, and a crashed ngspice process cannot take down the Python driver. Treat a missing measurement as a constraint violation with a large penalty.
- Set `num_threads=1` in `.spiceinit` when running N processes in parallel so threads do not oversubscribe the CPUs.

### Gaps
- I found no published benchmark comparing subprocess against shared-library throughput for sky130 decks.

## Q3. Reusable open-source SKY130 two-stage op-amp netlists and testbenches

### Takeaway
The best drop-in source is **AMPICBench** (October 2026, ngspice, sky130A). It provides two-stage class-A op-amp testbenches with stability (STB / Tian) analysis, `corners.json` files with tt/ff/ss/fs/sf × −40/27/125 °C and saturation-margin reporting, a `run_sims.py` script and a `draw_schematics.py` script. It is licensed **CC BY-NC 4.0**, which is fine for a non-commercial hackathon. AnalogGym gives sky130 testbench templates and a performance-extraction script. Older MPW shuttle repos (avsd_opamp, Cmosopamp, caravel_fulgor_opamp) have plain two-stage Miller designs, but they are less polished.

### Cited Findings
- **AMPICBench** — https://github.com/AMPICLab/AMPICBench. ngspice netlists in sky130A that use `skywater-pdk-libs-sky130_fd_pr`, converted from Cadence ADE Spectre testbenches.
  - Folders: `Opamps/sim_Diffamp1/`, `sim_Telescopic_Amp/`, `sim_FC_Amp/`, `sim_Opamp1_stb/`, `sim_Opamp_2stg_Diff_ClassA_STB/`, `sim_Opamp_2stg_FC_ClassA_STB/` and `sim_Opamp_2stg_Telescopic_ClassA_STB/`. Bias circuits are in `Bias_Circuits/sim_Bias_gen/` and `sim_Cascode_bias/`. Device characterization is in `Sky130_char/nmos_char`, `pmos_char`. Tools are `tools/run_sims.py` and `tools/draw_schematics.py`, and `schematics/` holds PNG, PDF and SVG files.
  - The `_stb` testbenches sweep loop-gain AC from 10 Hz to 10 GHz with Tian double injection and measure UGF, PM, DC gain and gain margin. Each run writes `op_nodes.txt` and `op_devices.txt`. Each circuit has `corners.json` (tt/ff/ss/fs/sf at −40/27/125 °C) with the worst saturation margin in mV and the devices in triode.
  - Reported results (ngspice 42, tt, 27 °C):

    | Circuit | DC gain | UGF | PM |
    |---|---|---|---|
    | Opamp1_stb | 60.6 dB | 79.0 MHz | 69.2° |
    | 2stg Diff ClassA | 60.6 dB | 126 MHz | 31.5° |
    | 2stg FC ClassA | 73.9 dB | 71.3 MHz | 32.0° |
    | 2stg Telescopic ClassA | 81.9 dB | 68.7 MHz | 49.3° |

  - The netlists hard-code `.lib "/opt/pdks/sky130A/libs.tech/ngspice/sky130.lib.spice" tt`, so the path needs editing. License is CC BY-NC 4.0. — [AMPICBench](https://github.com/AMPICLab/AMPICBench)
- **OPERA (closely related prior work, arXiv, 2026-10-29)**: "OPamp Design by an Expert-Taught Reasoning Agent" by Saxena and Mandal, an ISCAS 2027 submission. An LLM agent is taught gm/ID sizing and gLayout. It sized six SKY130 op-amps, including a Miller two-stage, against GBW ≥ 50 MHz, PM ≥ 60°, SR 25 V/µs into 1 pF and gain 30 to 60 dB. All 15 PVT corners were met post-layout (worst case GBW ≥ 53.7 MHz, PM ≥ 62.1°, SR ≥ 27.9 V/µs). It links the AMPICBench netlists. This is useful as spec targets and as a "related work" slide. The abstract does not name a BO baseline. — [arXiv 2610.39395](https://arxiv.org/abs/2610.39395)
- **AnalogGym** — https://github.com/CODA-Team/AnalogGym (paper https://arxiv.org/abs/2409.08534). Has 30 topologies, including AMPs and LDOs, in sky130 with ngspice. Repo folders are `netlist`, `testbench`, `design variables` and `schematic`. Amplifier metric extraction is in `AnalogGym/Amplifier/perf_extraction_amp.py`. The testbench template:
  ```
  .include ./mosfet_model/sky130_pdk/libs.tech/ngspice/corners/tt.spice
  .PARAM supply_voltage = 1.3
  .temp 27
  .PARAM PARAM_CLOAD = 10p
  ```
  AMP metrics are PSRR, CMRR, gain, FOM, settling time, area, noise, TC and Vos. The page says slew and settling must come from transient analysis. — [AnalogGym docs](https://coda-team.github.io/AnalogGym)
- **CircuitGenome** — https://github.com/analog-ml/CircuitGenome (`pip install circuitgenome`, MIT). It generates op-amp topologies (1-, 2- and 3-stage, NMC/RNMC) with gm/Id initial sizing, OR-Tools CP-SAT and ngspice verification. Example: `circuitgenome design --spec spec_gf180.yaml --topology two_stage_opamp_single_ended --output-dir designs/ --limit 200 --workers 4`. Its spice_sim module already implements AC gain/GBW/PM (DC loop closed with a large L, `PM = 180° + phase`, PM accepted only in (0°, 180°]), unity-gain-pulse slew, inverting −1 DC-sweep swing, CMRR/PSRR, and per-device `id`/`vds`/`vdsat` readout. Its sky130 device handle is `msky130_fd_pr__nfet_01v8`. — [CircuitGenome README](https://github.com/analog-ml/CircuitGenome); [spice_sim docs](https://circuitgenome.readthedocs.io/en/latest/api/sizer/shared/spice_sim.html)
- **MPW shuttle two-stage op-amps (older, 2021–2022):**
  - avsd_opamp (MPW-3 slot 021, "Two Stage CMOS Operational Amplifier using open source SkyWater 130nm", GitHub rohinthram/avsd_opamp) — [shuttle mirror](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-003/slot-021)
  - Cmosopamp (MPW-3 slot 023, GitHub MadhuriKadam9/Cmosopamp). Ships `Opamp_TRAN.cir.out`, `opamp2_tran.spice` and `opamp2_ac.spice` ngspice testbenches. — [shuttle mirror](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-003/slot-023)
  - caravel_fulgor_opamp (MPW-1 slot 019, GitHub diegohernando/caravel_fulgor_opamp), "several two stages operational amplifiers with Miller compensation" on sky130. — [shuttle mirror](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-001/slot-019)
  - An xschem `amp_tb.spice` testbench exists in MPW-6 slot 028. — [amp_tb.spice](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-006/slot-028/+/235b08b0d31aba81f7e9ed908bd5fdc0cac3c395/xschem/amp_tb.spice)
- Cornell C2S2 has blog posts on a SKY130 two-stage OTA with a constant-gm reference, but no testbench link was found. — [C2S2 blog](https://c2s2.engineering.cornell.edu/blogposts/SP23/ConstantGmCurrentReferenceTapeIn)

### Inferences
- **Fastest path:** clone AMPICBench, take `sim_Opamp_2stg_Diff_ClassA_STB` (diff pair + class-A second stage, the classic Miller topology), change the `.lib` path to `~/.ciel/sky130A/...`, and parameterize W/L/Ibias/Cc/Rz with `.param`. Its tt PM of 31.5° gives the optimizer a real problem to solve, since 60° is needed. Expect 30 to 60 minutes of adaptation. Check whether its compensation includes a nulling resistor; the README does not say.
- Borrow the measurement code from CircuitGenome's `spice_sim` (MIT) rather than writing it from scratch.
- Watch the license: AMPICBench is NC. That is fine for a hackathon demo, but say so if the project is released.

### Gaps
- I did not open AMPICBench's netlists to confirm Miller Cc/Rz presence, device types (it mentions LVT characterization) or bias levels.
- I did not verify that the older MPW repos still run on current ngspice and PDK versions.
- No Boise State course SKY130 two-stage op-amp deck was found in this session.

## Q4. gm/Id tooling for SKY130

### Takeaway
No ready-made, verified SKY130 gm/Id lookup-table download was found. Generate the tables yourself. Options are pygmid's sweep mode (`pip install pygmid`, Lookup API with `look_up` and `look_upVGS`), medwatt/gmid (`pip install git+https://github.com/medwatt/gmid.git`, supports ngspice), or a 40-line custom ngspice DC sweep that saves `@m.xm1.msky130_fd_pr__nfet_01v8[gm|id|gds|cgg|vdsat|vth]` over VGS, VDS and L. AMPICBench's `Sky130_char/nmos_char` and `pmos_char` are characterization decks that can serve as a template.

### Cited Findings
- **pygmid**: `pip install pygmid`. It is a Python port of Murmann's gm/ID starter kit. `Lookup` loads `.mat` (MATLAB) or `.pkl` (pygmid characterization script) files. API: `from pygmid import Lookup as lk; NCH = lk('180nch.mat'); NCH.look_up('ID', vds=..., vgs=...); NCH.look_upVGS(GM_ID=10, VDS=0.6, VSB=0.1, L=0.18)`. The README says the sweep CLI is "in a state of flux" and has no documentation. — [pygmid README](https://raw.githubusercontent.com/dreoilin/pygmid/main/README.md)
- The IHP docs show pygmid sweep usage: `python3 -m pygmid --mode sweep --config <...>/IHP130nm.cfg`. The config is in the IHP repo's `ngspice` branch at `tests/pygmid/sweep/IHP130nm.cfg`. pygmid "calls ngspice in the cwd". — [IHP open PDK docs, pygmid](https://ihp-open-pdk-docs.readthedocs.io/en/main/analog/pygmid.html)
- iic-jku's analog-circuit-design course uses pygmid-style `.mat` tables (e.g. `sg13_lv_nmos.mat`, `lookup('GDS', L=0.13, ...)`) for IHP SG13G2, not sky130. — [iic-jku techsweep page](https://iic-jku.github.io/analog-circuit-design/gmid/techsweep_sg13_plots_triode-preview.html)
- **medwatt/gmid**: `pip install git+https://github.com/medwatt/gmid.git` (Python ≥ 3.9). It runs DC sweeps over L and terminal voltages with ngspice, HSPICE or Spectre, stores ID, gm, gds and caps, and produces one table per technology and corner. It also has a multi-corner optimizer that writes a netlist. No sky130 example appears in its README. — [medwatt/gmid](https://github.com/medwatt/gmid)
- Boris Murmann committed a `sky130_gm.ipynb` Colab notebook (gm and ID-VG derivatives) to Google's sky130-raw-data repo. — [sky130-raw-data](https://foss-eda-tools.googlesource.com/skywater-pdk/sky130-raw-data/+/1248a33f483e9f74df96e3540ecec81ff3673d40/notebook); [GitHub mirror](https://github.com/google/skywater-pdk-sky130-raw-data)
- CircuitGenome has a `gmid_lut` module. — [gmid_lut source](https://circuitgenome.readthedocs.io/en/stable/_modules/circuitgenome/sizer/shared/gmid_lut.html)
- The OPERA paper's agent sizes from gm/ID lookup tables (spec → gm → gm/ID per role → W). — [arXiv 2610.39395](https://arxiv.org/abs/2610.39395)

### Inferences
- **Do-it-yourself table (recommended, about 30 to 45 min to write and run):** for each of nfet_01v8 and pfet_01v8, instantiate one device with W=10 (µm, because of scale=1u) and nf=1, then loop over L ∈ {0.15, 0.18, 0.25, 0.35, 0.5, 0.75, 1, 1.5, 2, 4}. For each L, run `dc VGS 0 1.8 0.01 VDS 0 1.8 0.05` at VSB=0 (optionally a few VSB values) and save `@m.xm1.msky130_fd_pr__nfet_01v8[id]`, `[gm]`, `[gds]`, `[gmbs]`, `[cgg]`, `[vth]` and `[vdsat]`. Write the results to a numpy `.npz` keyed by (L, VGS, VDS) and interpolate with `scipy.interpolate.RegularGridInterpolator`. That avoids pygmid's undocumented config format. With 10 L values × 2 devices, that is 20 ngspice runs, which should take minutes, not hours. This is an estimate; time it.
- **Gotcha:** sky130 nfet_01v8 is binned (180 bins in tt). Sweeping one W and extrapolating ID ∝ W is approximately valid only within a bin. Pick W within the range you will use, or sweep 2 or 3 W values.
- Generate tt tables first. Per-corner tables are only needed if the agent reasons with gm/Id at corners; final verification is by simulation anyway.

### Gaps
- No prebuilt sky130 `.mat`, `.pkl` or `.npz` gm/Id table with a verifiable provenance was found.
- No published generation time for sky130 gm/Id tables was found.

## Q5. Measuring PM, GBW, slew, swing and saturation margins in ngspice with SKY130

### Takeaway
Use `.control`-block `meas` on AC data (`vdb`, `vp` or `cph`) for gain, GBW and PM, `meas tran` for slew, and a DC sweep for swing. Read device operating points with `@m.x<inst>.msky130_fd_pr__nfet_01v8[vdsat]`, `[vds]`, `[vgs]`, `[vth]`, `[id]` and `[gm]`. The extra `m.` prefix and the inner `msky130_fd_pr__...` name exist because sky130 FETs are subcircuit-wrapped BSIM4 devices. Names must be lowercase.

### Cited Findings
- Per-device op-point readout uses `@m.xdut.<ref>[...]` (returns `id`, `vds` and `vdsat`). For sky130 the BSIM4 device sits one level deeper under the name `msky130_fd_pr__nfet_01v8`. — [CircuitGenome spice_sim docs](https://circuitgenome.readthedocs.io/en/latest/api/sizer/shared/spice_sim.html)
- sky130 xschem netlists save device parameters with paths like `@m.x1.m...[param]`, and internal names must be lowercase. — [MPW-5 slot 020 test_nmos.spice](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-005/slot-020/+/1633fa20e4e515867f24c5dc6cf58e52db5f08bc/xschem/test_nmos.spice); [n_diffamp.spice](https://foss-eda-tools.googlesource.com/third_party/shuttle/sky130/mpw-005/slot-020/+/1633fa20e4e515867f24c5dc6cf58e52db5f08bc/xschem/n_diffamp.spice)
- CircuitGenome's measurement approach: an open-loop AC bench with a large L closing the DC loop and a large C AC-grounding the inverting input, gain read at 1 mHz, GBW at the 0 dB crossing, `PM = 180° + phase` with validity in (0°, 180°], slew from a unity-gain pulse, and swing from an inverting −1 DC sweep. — [CircuitGenome spice_sim docs](https://circuitgenome.readthedocs.io/en/latest/api/sizer/shared/spice_sim.html)
- AMPICBench `_stb` benches use Tian double injection for loop gain and measure UGF, PM, DC gain and GM. Its `corners.json` reports "worst saturation margin in mV" and the triode devices. — [AMPICBench](https://github.com/AMPICLab/AMPICBench)

### Inferences
- **Skeleton testbench** (from working ngspice knowledge, **not executed in this session**, so validate early):
  ```spice
  * two-stage Miller TB
  .lib "/home/thinkies/.ciel/sky130A/libs.tech/ngspice/sky130.lib.spice" {CORNER}
  .temp {TEMP}
  .param vdd={VDD} vcm=0.9 cl=2p
  .include opamp.sp                      $ .subckt opamp inp inn out vdd vss ...
  VDD vdd 0 {vdd}
  VCM cm 0 {vcm}
  * open-loop with DC feedback via big L / big C (CircuitGenome style)
  Vin inp cm dc 0 ac 1
  Lfb out inn 1T
  Cfb inn 0 1T
  XOA inp inn out vdd 0 opamp
  CL out 0 {cl}
  .control
    op
    let p_mw = -i(VDD)*{VDD}*1e3
    let vdsat1 = @m.xoa.xm1.msky130_fd_pr__nfet_01v8[vdsat]
    let vds1   = @m.xoa.xm1.msky130_fd_pr__nfet_01v8[vds]
    echo RES p_mw $&p_mw
    echo RES sat1 $&vds1 $&vdsat1          $ margin = |vds|-|vdsat|, require > ~50-100 mV
    ac dec 40 1 10G
    let gdb = vdb(out)
    let ph  = cph(out)*180/pi             $ cph = continuous (unwrapped) phase
    meas ac a0  find gdb at=1
    meas ac gbw when gdb=0
    meas ac phu find ph when gdb=0
    let pm = 180 + phu
    echo RES a0 $&a0
    echo RES gbw $&gbw
    echo RES pm $&pm
  .endc
  .end
  ```
  For slew, use a separate unity-gain-follower deck with `Vin` a PULSE of 0.5 → 1.3 V and `tran 1n 2u`. Measure `meas tran sr_r deriv v(out) at=...`, or time the 10 % to 90 % rise with `trig`/`targ` and compute `0.8*ΔV/t`. For swing, run a DC sweep in an inverting-gain or open-loop configuration and find where small-signal gain drops by 10 %. Alternatively define swing at the op point as `[VDD − |VDSAT_p2|, VDSAT_n2]` from device readouts. That is cheap and is commonly used in sizing loops.
- For pfets, `vds` and `vdsat` are negative. Use absolute values. A robust saturation check is `|vds| − |vdsat| ≥ margin` and `|vgs| − |vth| ≥ some overdrive`, or read the BSIM4 `[region]` parameter if it is exposed. Not verified for sky130 here.
- Save only what is needed (`save` the out/in nodes plus the `@m...` parameters). This keeps rawfile I/O small. ngspice-45's `save nointernals` also helps.
- For PM, prefer a loop-gain measurement (Tian or Middlebrook, as in AMPICBench) if the team has time. The big-L/big-C open-loop method is adequate for a single-ended Miller amp in unity-gain feedback.

### Gaps
- The exact sky130 parameter names (`vdsat` vs `vdss`, and whether `region` is exposed) were not verified against the ngspice 47 manual in this session. Test with `showmod`/`show` on one device first: `show @m.xoa.xm1.msky130_fd_pr__nfet_01v8` lists all available parameters.

## Q6. BO and black-box baselines for 100–1000 simulations with constraints

### Takeaway
**Optuna** is the fastest to wire up. `GPSampler(constraints_func=...)` gives real constrained GP-BO, with inequality constraints since v4.2 and constrained multi-objective since v4.5. `TPESampler(constraints_func=...)` is a cheap second baseline. Constraint values ≤ 0 mean feasible, so each spec slack maps directly. CMA-ES (`pip install cma`, or Optuna's `CmaEsSampler`) is a third option, but it handles constraints only through penalties. BoTorch/Ax is more powerful but slower to set up.

### Cited Findings
- `optuna.samplers.GPSampler(*, seed=None, independent_sampler=None, n_startup_trials=10, deterministic_objective=False, constraints_func=None, warn_independent_sampling=True)`. A trial is feasible when all constraint values are ≤ 0. `constraints_func` is called only for successful trials, not failed or pruned ones. — [Optuna v4.5 GPSampler docs](https://optuna.readthedocs.io/en/v4.5.0/reference/samplers/generated/optuna.samplers.GPSampler.html)
- Optuna v4.2 added inequality constraints to GPSampler, and v4.5 added constrained multi-objective GPSampler. An older source-code comment says multi-objective is unsupported, but that predates v4.5. — [Optuna v4.5 release article](https://medium.com/optuna/optuna-v4-5-81e78d8e077a); [Introducing GPSampler](https://medium.com/optuna/introducing-optunas-native-gpsampler-0aa9aa3b4840)
- Optuna wrote about improving GPSampler parallelization by considering running trials. — [Optuna blog](https://medium.com/p/10d42aeb5d49)
- The BoTorchSampler integration exists for Optuna. — [BoTorchSampler docs](https://himktoptuna.readthedocs.io/en/stable/reference/generated/optuna.integration.BoTorchSampler.html)

### Inferences
- Minimal baseline:
  ```python
  import optuna
  def objective(t):
      x = dict(W1=t.suggest_float("W1",1,50,log=True), L1=t.suggest_float("L1",0.15,2,log=True),
               Ib=t.suggest_float("Ib",5e-6,200e-6,log=True), Cc=t.suggest_float("Cc",0.2e-12,5e-12,log=True),
               Rz=t.suggest_float("Rz",100,20e3,log=True))  # ...
      m = worst_case_over_pvt(x)                  # dict of worst-case metrics (counts N sims toward budget)
      t.set_user_attr("c", [60-m["pm"], 1e7-m["gbw"], 60-m["a0_db"], ...])  # <=0 feasible
      return m["power"]
  sampler = optuna.samplers.GPSampler(constraints_func=lambda t: t.user_attrs["c"], seed=0)
  optuna.create_study(sampler=sampler).optimize(objective, n_trials=200, n_jobs=1)
  ```
  Normalize constraint slacks (e.g. divide by the spec value) so the GP sees comparable scales. On a crash, return a large objective and set constraint values to large positive numbers instead of raising. Raised trials are FAILED and are skipped by `constraints_func`.
- GP-BO cost grows with trial count. At ≤ 1000 trials it is fine, but expect the sampler to take seconds per suggestion late in a run. TPE is near-instant.
- Budget accounting: if each "trial" runs 45 PVT decks, a 1000-sim budget is only about 22 full-PVT trials. Define the budget in simulator invocations and apply it identically to BO and the LLM agent. For example, both could use the reduced worst-case corner set described in Q2.

### Gaps
- No head-to-head benchmark of Optuna GP vs BoTorch vs CMA-ES on sky130 op-amp sizing was found.

## Q7. GF180MCU availability in ngspice (process-migration stretch goal)

### Takeaway
GF180MCU models are ngspice-native. Install them with ciel. MOSFETs are 3.3 V `nfet_03v3`/`pfet_03v3` (or 6 V `nfet_06v0`/`pfet_06v0`). Select the MOSFET corner with `.lib ".../sm141064.ngspice" typical|ff|ss|fs|sf`. Resistors, MIM caps, BJTs and diodes have separate corner sections. Supply is 3.3 V instead of 1.8 V, so specs and swing must be rescaled.

### Cited Findings
- Install: `pip install ciel` and `ciel enable --pdk gf180mcu 7b70722e33c03fcb5dabcf4d479fb0822d9251c9` (example hash). This installs gf180mcuA–D under `~/.ciel/`. — [VLSIDA gf180mcu tutorial](https://vlsida.github.io/chip-tutorials/gf180mcu.html). The current ciel CLI form is `ciel enable --pdk-family gf180mcu <hash>`, with `ciel ls-remote --pdk-family gf180mcu` to list versions. — [ciel GitHub](https://github.com/fossi-foundation/ciel)
- Library lines: `.lib "$PDK_ROOT/gf180mcuC/libs.tech/ngspice/sm141064.ngspice" typical`. MOSFET corners are `typical`, `ff`, `ss`, `fs` and `sf`, with separate `res_*`, `mimcap_*`, `bjt_*` and `diode_*` sections. MIM caps are in `sm141064_mim.ngspice`. `design.ngspice` (Monte Carlo switches, mismatch, flicker-noise corner) should be included once at the top. Devices: `nfet_03v3`, `pfet_03v3`, `nfet_03v3_dss`, `pfet_03v3_dss`, `nfet_06v0`, `pfet_06v0`, `nfet_06v0_nvt`. — [VLSIDA gf180mcu tutorial](https://vlsida.github.io/chip-tutorials/gf180mcu.html)
- The upstream `gf180mcu_fd_pr/models/ngspice/` contains `design.ngspice`, `sm141064.ngspice` and `smbb000149.ngspice`. — [gf180mcu_fd_pr models/ngspice](https://foss-eda-tools.googlesource.com/globalfoundries-pdk/libs/gf180mcu_fd_pr/+/refs/heads/main/models/ngspice)
- IIC-OSIC-TOOLS includes `gf180mcuD`. — [IIC-OSIC-TOOLS](https://github.com/iic-jku/IIC-OSIC-TOOLS)
- CircuitGenome's example spec is `spec_gf180.yaml`, and its default device handle `m0` is for GF180. Its pipeline already runs on GF180. — [CircuitGenome](https://github.com/analog-ml/CircuitGenome); [spice_sim docs](https://circuitgenome.readthedocs.io/en/latest/api/sizer/shared/spice_sim.html)
- **Local inspection:** gf180mcu is **not** installed in `~/.ciel` yet. Only sky130A and sky130B are present.

### Inferences
- The migration work is: swap the `.lib` lines, rename the device cells, check the device op-point path (it differs from the sky130 `msky130_fd_pr__...` naming; CircuitGenome uses `m0`), change VDD to 3.3 V and Lmin to 0.28 µm, check the W/L units convention, and regenerate gm/Id tables. Budget about 1 to 1.5 hours if the sky130 flow is clean. The `.spiceinit` sky130 flags are not needed for gf180.
- Check whether gf180 MOSFET models are plain `.model` cards or subcircuits by opening `sm141064.ngspice` before writing `@m...` paths.

### Gaps
- No verified `.spiceinit` requirements for gf180 in ngspice 47.
- The exact current gf180mcu open_pdks hash was not retrieved. Run `ciel ls-remote --pdk-family gf180mcu` on the day.
- The gf180 device op-point access path was not verified.

## Q8. Rendering a netlist back to a schematic image

### Takeaway
AMPICBench already ships a `tools/draw_schematics.py` and pre-rendered PNG/PDF/SVG schematics for its op-amps. For a fixed two-stage Miller topology, a hand-placed **schemdraw** template that only re-labels W/L, Ib, Cc and Rz values is the fastest and most reliable option. Automatic layout from an arbitrary netlist is not worth attempting today.

### Cited Findings
- AMPICBench contains `schematics/` (PNG, PDF, SVG) and `tools/draw_schematics.py`. — [AMPICBench](https://github.com/AMPICLab/AMPICBench)

### Inferences
- schemdraw (`pip install schemdraw`) has `elm.NFet`/`elm.PFet`, `elm.Capacitor` and `elm.Resistor`, and can render to SVG or PNG via matplotlib. A ~60-line function that draws the 7–8 transistor Miller op-amp at fixed coordinates and annotates each device with `W/L` from the optimizer's current dict would make a good demo visual. That is about 45 minutes of work. This is based on general schemdraw knowledge; the API details were not re-verified in this session.
- Alternative: xschem is in the PDK (`~/.ciel/sky130A/libs.tech/xschem` exists, local inspection), but it is not scriptable to images without a GUI or X setup.

### Gaps
- I did not check AMPICBench's `draw_schematics.py` implementation (library used, whether it is netlist-driven).
- No current schemdraw version or API docs were fetched in this session.
