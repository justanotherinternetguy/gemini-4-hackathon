"""Words and/or a photo of a sketch -> board spec JSON (Gemini), hard-validated, one repair round.

Run with our venv:

    .venv/bin/python -m pcb.create.describe --text "a 555 blinker ..." [--image sketch.jpg] --out DIR [--build]

Scope, stated plainly: this only proposes a parts list and netlist for a SMALL through-hole board
from a fixed catalogue. The netlist is the model's proposal; nothing here simulates or verifies
that the circuit works. `--build` then hands the spec to `pcb.create.build` (system python,
KiCad + Freerouting), where KiCad's rule check is the only judge of the layout.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import sys
import time
from pathlib import Path

from pcb.create import catalog

MODEL = "gemini-4-hydrogen"
ROOT = Path(__file__).resolve().parents[2]

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string", "description": "one sentence: what the board does"},
        "board": {"type": "object", "properties": {"width_mm": {"type": "number"}, "height_mm": {"type": "number"}},
                  "required": ["width_mm", "height_mm"]},
        "components": {"type": "array", "items": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "type": {"type": "string"}, "value": {"type": "string"},
                           "purpose": {"type": "string"}},
            "required": ["ref", "type", "value", "purpose"]}},
        "nets": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "pins": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "pins"]}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "board", "components", "nets", "assumptions"],
}

PROMPT = """You turn a description (and/or a photo of a hand sketch) of a SMALL through-hole circuit board
into a board spec JSON. A program will then place the parts, route the copper and run KiCad's rule check.

Part catalogue - `type` MUST be exactly one of these (nothing else exists):
{catalogue}

Rules:
- `ref`: R1, C1, D1, U1, J1, SW1, Q1 ... unique. `value`: e.g. "10k", "100n", "NE555", "ATtiny85", "red".
- `purpose`: one plain line saying what this part is for on this board.
- ICs are generic DIP footprints: use the REAL datasheet pin numbers of the named chip (e.g. NE555: 1 GND,
  2 TRIG, 3 OUT, 4 RESET, 5 CTRL, 6 THR, 7 DIS, 8 VCC; ATtiny85: 1 RESET, 2 PB3, 3 PB4, 4 GND, 5 PB0/MOSI,
  6 PB1/MISO, 7 PB2/SCK, 8 VCC). If you are not sure of a pinout, say so in `assumptions`.
- `nets`: each net has a short name (letters, digits, _ + -) and a list of pins written REF.pin (e.g. "U1.3").
  Every net needs at least 2 pins. A pin may be on at most ONE net. Every component must be on a net.
  Pins that stay unconnected are simply not listed.
- Polarity: led pin 1 = cathode, pin 2 = anode; electrolytic_cap pin 1 = plus, pin 2 = minus.
- `board`: a sensible outline in mm for these parts (the placer will grow it if too small).
- Keep it small: only the parts asked for plus what is strictly needed. If the request needs a part that is
  not in the catalogue, use the nearest catalogue part (e.g. a pin header as the connector) and say so in
  `assumptions`. List every other guess you made in `assumptions`.
{image_note}
Request:
{text}
"""


def emit(path: Path, type_: str, **kw):
    with open(path, "a") as f:
        f.write(json.dumps({"t": round(time.time(), 3), "type": type_, "src": "describe", **kw}) + "\n")
    print(f"[{type_}] " + json.dumps(kw)[:400], flush=True)


def ask(c, blocks):
    r = c.interactions.create(
        model=MODEL, input=blocks, generation_config={"thinking_level": "medium"},
        response_format={"type": "text", "mime_type": "application/json", "schema": SCHEMA}, timeout=90)
    return r.output_text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--text", default="")
    ap.add_argument("--image", default=None, help="photo of a sketch (jpg/png)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--build", action="store_true", help="then run pcb.create.build with /usr/bin/python3")
    a = ap.parse_args(argv)
    if not a.text and not a.image:
        ap.error("give --text and/or --image")

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    evp = out / "events.jsonl"
    evp.write_text("")
    (out / "request.json").write_text(json.dumps({"text": a.text, "image": a.image, "model": MODEL}, indent=2))

    from sizing import managed
    c = managed.client()
    image_note = ("The attached photo is the user's sketch: read the parts and the wires from it; where the "
                  "sketch and the text disagree, follow the text and note it in `assumptions`.\n") if a.image else ""
    blocks = [{"type": "text", "text": PROMPT.format(catalogue=catalog.catalogue_text(), image_note=image_note,
                                                      text=a.text or "(see the sketch)")}]
    if a.image:
        mime = mimetypes.guess_type(a.image)[0] or "image/jpeg"
        blocks.append({"type": "image", "mime_type": mime,
                       "data": base64.b64encode(Path(a.image).read_bytes()).decode()})

    raw_log, spec, errs = [], None, ["no reply"]
    for rnd in range(2):  # first answer + at most one repair round
        t0 = time.time()
        try:
            raw = ask(c, blocks)
        except Exception as e:
            raw, errs = None, [f"model call failed: {type(e).__name__}: {e}"]
            raw_log.append({"round": rnd, "error": errs[0]})
            emit(evp, "spec_valid", valid=False, round=rnd, errors=errs)
            break
        raw_log.append({"round": rnd, "seconds": round(time.time() - t0, 1), "raw": raw})
        try:
            spec = json.loads(raw)
            errs = catalog.validate(spec)
        except Exception as e:
            spec, errs = None, [f"reply is not valid JSON: {e}"]
        if spec is not None and rnd == 0:
            emit(evp, "understood", title=spec.get("title"), description=spec.get("description"),
                 components=[{k: x.get(k) for k in ("ref", "type", "value", "purpose")}
                             for x in spec.get("components", []) if isinstance(x, dict)],
                 nets=len(spec.get("nets", [])), assumptions=spec.get("assumptions", []),
                 from_image=bool(a.image), seconds=round(time.time() - t0, 1))
        emit(evp, "spec_valid", valid=not errs, round=rnd, errors=errs)
        if not errs:
            break
        blocks = blocks + [{"type": "text", "text": (
            "Your previous answer was:\n" + (raw or "") + "\n\nIt FAILED validation with these errors:\n- "
            + "\n- ".join(errs) + "\n\nReturn the complete corrected JSON.")}]
    (out / "describe_raw.json").write_text(json.dumps(raw_log, indent=2))

    if errs:
        if spec is not None:
            (out / "board.invalid.json").write_text(json.dumps(spec, indent=2))
        print("NOT VALID after one repair round; nothing built:\n- " + "\n- ".join(errs))
        return 2
    spec["source"] = {"text": a.text, "image": a.image, "model": MODEL,
                      "note": "netlist proposed by a language model; not verified by simulation"}
    (out / "board.json").write_text(json.dumps(spec, indent=2))
    print(f"spec written: {out / 'board.json'}  ({catalog.SCOPE_NOTE})")
    if a.build:
        return subprocess.call(["/usr/bin/python3", "-m", "pcb.create.build", "--spec", str(out / "board.json"),
                                "--out", str(out)], cwd=str(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
