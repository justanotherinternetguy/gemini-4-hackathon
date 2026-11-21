"""Multimodal intake: the non-text front door of the pipeline.

  roles : photo/screenshot of a schematic  -> which circuit it is + the role of every transistor
          (Gemini Decisions API, gemini-decision-1: closed-set choices with calibrated
          probabilities). The predicted role map defines the tuning knobs (devices sharing a role
          share a knob), so a wrong label produces a wrong search space.
  spec  : spoken (or typed) requirements    -> spec card (gemini-4-hydrogen, audio input)
  eval  : role-labelling accuracy on all three AMPICBench op-amps, with and without the image.

python -m sizing.intake roles --image photo.jpg --out roles.json [--circuit auto|diff|fc|tele] [--no-image]
python -m sizing.intake spec  (--audio clip.webm | --text "70 degrees phase margin, 15 MHz ...") --out spec.json
python -m sizing.intake eval
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import subprocess
import tempfile
import time
from pathlib import Path

from sizing import managed
from sizing import opamp2stg as oa

DECISION_MODEL = "gemini-decision-1"
SPEC_MODEL = "gemini-4-hydrogen"
SCHEMATICS = oa.ROOT / "third_party" / "AMPICBench" / "schematics"
SCHEM_PNG = {"diff": "Opamp_2stg_Diff_ClassA.png", "fc": "Opamp_2stg_FC_ClassA.png",
             "tele": "Opamp_2stg_Telescopic_ClassA.png"}
AMP_SUBCKTS = {"diff": ("Diffamp1", "ClassA_stg_PMOS"), "fc": ("FC_Amp", "ClassA_stg_PMOS"),
               "tele": ("Telescopic_Amp", "ClassA_stg_PMOS")}

TOPOLOGIES = {
    "diff": "Two-stage op-amp whose first stage is a plain differential pair with a PMOS current-mirror "
            "load (five-transistor stage, no cascodes in the signal path of stage 1).",
    "fc": "Two-stage op-amp whose first stage is a FOLDED cascode: the NMOS input pair's drains fold "
          "into PMOS cascodes, with PMOS current sources on top and NMOS cascode current sinks below.",
    "tele": "Two-stage op-amp whose first stage is a TELESCOPIC cascode: NMOS cascodes are stacked "
            "directly on top of the NMOS input pair, under a cascoded PMOS mirror load.",
}
ROLE_OPTIONS = {
    "in": "Differential input pair device: its gate is one of the op-amp inputs (vp / vm).",
    "tail": "Tail current source of the input pair (cascode or bottom device) connected to the input "
            "pair's common-source node.",
    "load": "First-stage PMOS current-mirror load device with its source at VDD, in a stage where the "
            "input pair's drain does NOT connect to this device's drain node.",
    "psrc": "PMOS current source at the fold node of a folded cascode: source at VDD and its drain node "
            "is also connected to an input-pair drain.",
    "pcas": "PMOS cascode device in the first stage: gate at the fixed bias Vbias_2, in series below a "
            "PMOS device whose source is at VDD.",
    "ncas": "NMOS cascode stacked directly on top of an input-pair device in the first stage "
            "(telescopic), gate at a bias voltage, drain at the stage output or the mirror node.",
    "nsink": "NMOS current-sink device (cascode or bottom) in the first stage of a folded cascode, "
             "below the PMOS cascodes; not the tail and not in the second stage.",
    "drv": "Second-stage common-source driver: PMOS with its gate at the first-stage output and its "
           "drain at vout.",
    "sink": "Second-stage NMOS current-sink load (cascode or bottom device) between vout and ground.",
    "other": "Bias-generation or replica-bias device that is not in the signal path.",
}


def _img_block(path: Path, resolution: str = "high") -> dict:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return {"type": "image", "data": base64.b64encode(path.read_bytes()).decode(),
            "mime_type": mime, "resolution": resolution}


def netlist_excerpt(circuit: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Subcircuits of the amplifier (not the bias network), geometry clutter stripped.
    Returns (text, [(subckt, instance, cleaned line)])."""
    src = oa.CIRCUITS[circuit]["src"].read_text()
    keep, devs, sub = [], [], None
    wanted = set(AMP_SUBCKTS[circuit])
    for line in src.splitlines():
        s = line.strip()
        if s.lower().startswith(".subckt"):
            sub = s.split()[1]
        if sub and (sub in wanted or sub.startswith("Opamp_2stg")):
            clean = re.sub(r"\s+(as|ad|ps|pd|nf)=\S+", "", re.sub(r"\{cds_\w+\([^)]*\)\}", "x", s))
            keep.append(clean)
            if sub in wanted and s[:2].upper() in ("XN", "XP"):
                devs.append((sub, s.split()[0], clean))
        if s.lower().startswith(".ends"):
            sub = None
    return "\n".join(keep), devs


def identify_circuit(c, image: Path) -> tuple[str, float, dict]:
    res = c.decisions.create(model=DECISION_MODEL, input=[_img_block(image)], questions={
        "topology": {"type": "choice", "instructions": "Which op-amp topology does this schematic show?",
                     "criteria": TOPOLOGIES}})
    a = res.answers["topology"]
    probs = dict(a.probabilities) if isinstance(a.probabilities, dict) else {}
    return a.choice, float(probs.get(a.choice, getattr(a, "confidence", 0.0) or 0.0)), probs


def label_roles(image: Path | None, circuit: str = "auto", use_image: bool = True) -> dict:
    c = managed.client()
    t0 = time.perf_counter()
    out = {"model": DECISION_MODEL, "image": str(image) if image else None, "used_image": bool(image and use_image)}
    if circuit == "auto":
        if not image:
            raise ValueError("circuit=auto needs an image")
        circuit, conf, probs = identify_circuit(c, image)
        out.update(circuit_confidence=round(conf, 4), circuit_probabilities=probs)
    out["circuit"] = circuit
    text, devs = netlist_excerpt(circuit)
    blocks = [{"type": "text", "text": "SPICE netlist of the op-amp (devices are subcircuit instances; "
               "pin order: drain gate source bulk):\n" + text}]
    if image and use_image:
        blocks.append(_img_block(image))
    questions = {}
    for sub, inst, line in devs:
        questions[f"{sub}__{inst}"] = {
            "type": "choice",
            "instructions": f"What is the role of transistor {inst} (drawn as {inst[1:]}) in subcircuit "
                            f"{sub}? Its netlist line is: {line}",
            "criteria": ROLE_OPTIONS}
    res = c.decisions.create(model=DECISION_MODEL, input=blocks, questions=questions)
    truth_map = oa.CIRCUITS[circuit]["role_map"]
    rows, correct = [], 0
    for sub, inst, _ in devs:
        a = res.answers[f"{sub}__{inst}"]
        probs = dict(a.probabilities) if isinstance(a.probabilities, dict) else {}
        truth = truth_map.get((sub, inst), "other")
        ok = a.choice == truth
        correct += ok
        rows.append({"subckt": sub, "inst": inst, "label": inst[1:], "role": a.choice,
                     "confidence": round(float(probs.get(a.choice, getattr(a, "confidence", 0) or 0)), 4),
                     "truth": truth, "correct": ok})
    out.update(devices=rows, n=len(rows), n_correct=correct, accuracy=round(correct / len(rows), 4),
               latency_s=round(time.perf_counter() - t0, 2))
    role_map = {(r["subckt"], r["inst"]): r["role"] for r in rows if r["role"] != "other"}
    try:
        name = oa.register_from_roles(circuit, role_map, name="_probe")
        out["knobs"] = list(oa.CIRCUITS[name]["space"])
    except Exception as e:
        out["knobs_error"] = str(e)
    try:
        u = res.usage
        out["input_tokens"] = getattr(u, "total_input_tokens", None)
    except Exception:
        pass
    return out


def roles_to_map(roles: dict) -> dict:
    return {(d["subckt"], d["inst"]): d["role"] for d in roles["devices"] if d["role"] != "other"}


# --------------------------------------------------------------------------- spec from voice / text

SPEC_SCHEMA = {"type": "object", "properties": {
    "transcript": {"type": "string", "description": "what the person said, verbatim"},
    "pm_deg_min": {"type": "number", "nullable": True, "description": "minimum phase margin in degrees"},
    "ugf_mhz_min": {"type": "number", "nullable": True, "description": "minimum unity-gain frequency / bandwidth in MHz"},
    "dc_gain_db_min": {"type": "number", "nullable": True, "description": "minimum DC gain in dB"},
    "i_core_ua_max": {"type": "number", "nullable": True, "description": "maximum current in microamps"},
    "notes": {"type": "array", "items": {"type": "string"},
              "description": "anything said that is not one of the four specs, or assumptions made"}},
    "required": ["transcript", "pm_deg_min", "ugf_mhz_min", "dc_gain_db_min", "i_core_ua_max", "notes"]}
SPEC_LIMITS = {"pm_deg_min": (45, 80), "ugf_mhz_min": (1, 60), "dc_gain_db_min": (40, 85), "i_core_ua_max": (10, 400)}
SPEC_PROMPT = ("An engineer is stating the requirements for an op-amp. Transcribe what they say and extract "
               "these four requirements if stated: minimum phase margin (degrees; 'stability'), minimum "
               "unity-gain frequency / gain-bandwidth (MHz; 'speed' or 'bandwidth'), minimum DC gain (dB), "
               "maximum supply/core current (microamps; 'power'). Convert units (e.g. 0.05 mA = 50 uA, "
               "20e6 Hz = 20 MHz). Use null for anything not stated. Do not invent numbers.")


def _to_wav(path: Path) -> Path:
    if path.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg"):
        return path
    out = Path(tempfile.mkdtemp()) / "clip.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000", str(out)],
                   check=True)
    return out


def spec_from(audio: Path | None = None, text: str | None = None) -> dict:
    c = managed.client()
    t0 = time.perf_counter()
    if audio:
        wav = _to_wav(audio)
        mime = mimetypes.guess_type(str(wav))[0] or "audio/wav"
        blocks = [{"type": "text", "text": SPEC_PROMPT},
                  {"type": "audio", "data": base64.b64encode(wav.read_bytes()).decode(), "mime_type": mime}]
    else:
        blocks = [{"type": "text", "text": SPEC_PROMPT + "\n\nThe engineer wrote: " + (text or "")}]
    r = c.interactions.create(model=SPEC_MODEL, input=blocks, generation_config={"thinking_level": "low"},
                              response_format={"type": "text", "mime_type": "application/json", "schema": SPEC_SCHEMA},
                              timeout=90)
    d = json.loads(r.output_text)
    notes = list(d.get("notes") or [])
    dflt = oa.DEFAULT_SPEC
    vals = {}
    for k, (lo, hi) in SPEC_LIMITS.items():
        v = d.get(k)
        if v is None:
            continue
        cl = min(max(float(v), lo), hi)
        if cl != v:
            notes.append(f"{k}={v} is outside the supported range [{lo}, {hi}]; clamped to {cl}")
        vals[k] = cl
    spec = {"pm_deg": [">=", vals.get("pm_deg_min", dflt["pm_deg"][1])],
            "ugf_hz": [">=", vals["ugf_mhz_min"] * 1e6 if "ugf_mhz_min" in vals else dflt["ugf_hz"][1]],
            "dc_gain_db": [">=", vals.get("dc_gain_db_min", dflt["dc_gain_db"][1])],
            "i_core_a": ["<=", vals["i_core_ua_max"] * 1e-6 if "i_core_ua_max" in vals else dflt["i_core_a"][1]]}
    stated = [k for k in SPEC_LIMITS if k in vals]
    for k in SPEC_LIMITS:
        if k not in vals:
            notes.append(f"{k} not stated; using the default")
    return {"transcript": d.get("transcript", text or ""), "spec": spec, "stated": stated, "notes": notes,
            "target": {k: [op, b] for k, (op, b) in oa.guarded({k: tuple(v) for k, v in spec.items()}).items()},
            "model": SPEC_MODEL, "source": "audio" if audio else "text",
            "latency_s": round(time.perf_counter() - t0, 2)}


# --------------------------------------------------------------------------- CLI

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roles")
    r.add_argument("--image", type=Path)
    r.add_argument("--circuit", default="auto", choices=["auto", *SCHEM_PNG])
    r.add_argument("--no-image", action="store_true", help="ablation: netlist only")
    r.add_argument("--out", type=Path)
    s = sub.add_parser("spec")
    s.add_argument("--audio", type=Path)
    s.add_argument("--text")
    s.add_argument("--out", type=Path)
    e = sub.add_parser("eval")
    e.add_argument("--out", type=Path, default=oa.ROOT / "runs" / "intake_eval.json")
    a = ap.parse_args()
    if a.cmd == "roles":
        out = label_roles(a.image, a.circuit, use_image=not a.no_image)
    elif a.cmd == "spec":
        out = spec_from(a.audio, a.text)
    else:
        out = {"rows": []}
        for circ, png in SCHEM_PNG.items():
            img = SCHEMATICS / png
            ident, conf, _ = identify_circuit(managed.client(), img)
            for use_image in (True, False):
                res = label_roles(img, circ, use_image=use_image)
                wrong = [(d["subckt"], d["inst"], d["role"], d["truth"], d["confidence"])
                         for d in res["devices"] if not d["correct"]]
                out["rows"].append({"circuit": circ, "with_image": use_image, "n": res["n"],
                                    "n_correct": res["n_correct"], "accuracy": res["accuracy"],
                                    "identified_as": ident, "id_confidence": round(conf, 3),
                                    "mean_conf_correct": round(sum(d["confidence"] for d in res["devices"] if d["correct"]) / max(1, res["n_correct"]), 3),
                                    "mean_conf_wrong": round(sum(d["confidence"] for d in res["devices"] if not d["correct"]) / max(1, res["n"] - res["n_correct"]), 3),
                                    "wrong": wrong, "latency_s": res["latency_s"]})
                print(json.dumps(out["rows"][-1]))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1, default=str))
    if a.cmd != "eval":
        print(json.dumps(out, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()
