"""Fixed part catalogue + hard spec validation. Pure stdlib: imported by both the venv
(`describe`) and the system python (`build`)."""
from __future__ import annotations

import re

SCOPE_NOTE = ("Small-board generator for through-hole/simple parts with automatic placement. "
              "The netlist was proposed by a language model and is NOT verified by simulation; "
              "KiCad's design rule check (DRC) is the only judge of the layout.")

# type -> (footprint library, footprint name, pin names, human label, pin meaning)
_FIXED = {
    "resistor": ("Resistor_THT", "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", ["1", "2"],
                 "axial resistor", "1, 2 (interchangeable)"),
    "capacitor": ("Capacitor_THT", "C_Disc_D5.0mm_W2.5mm_P5.00mm", ["1", "2"],
                  "ceramic disc capacitor", "1, 2 (interchangeable)"),
    "electrolytic_cap": ("Capacitor_THT", "CP_Radial_D5.0mm_P2.50mm", ["1", "2"],
                         "radial electrolytic capacitor", "1 = plus, 2 = minus"),
    "led": ("LED_THT", "LED_D5.0mm", ["1", "2"], "5 mm LED", "1 = cathode (K), 2 = anode (A)"),
    "dip8": ("Package_DIP", "DIP-8_W7.62mm", [str(i) for i in range(1, 9)],
             "DIP-8 IC", "1..8, standard DIP numbering"),
    "dip14": ("Package_DIP", "DIP-14_W7.62mm", [str(i) for i in range(1, 15)],
              "DIP-14 IC", "1..14, standard DIP numbering"),
    "dip16": ("Package_DIP", "DIP-16_W7.62mm", [str(i) for i in range(1, 17)],
              "DIP-16 IC", "1..16, standard DIP numbering"),
    "to220": ("Package_TO_SOT_THT", "TO-220-3_Vertical", ["1", "2", "3"],
              "TO-220 3-pin part (regulator/transistor)", "1, 2, 3 left to right, facing the label"),
    "screw_terminal_2": ("TerminalBlock_Phoenix",
                         "TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal", ["1", "2"],
                         "2-pin screw terminal", "1, 2"),
    "tactile_switch": ("Button_Switch_THT", "SW_PUSH_6mm", ["1", "2"],
                       "6 mm tactile push button", "1, 2 (the two sides of the contact)"),
}
HEADER_RE = re.compile(r"^pin_header_1x(\d{1,2})$")
HEADER_MIN, HEADER_MAX = 1, 12
IC_TYPES = {"dip8", "dip14", "dip16", "to220"}
CONNECTOR_TYPES = {"screw_terminal_2"}
TYPE_NAMES = sorted(_FIXED) + [f"pin_header_1x{n}" for n in range(HEADER_MIN, HEADER_MAX + 1)]


def lookup(type_: str):
    """-> dict(lib, footprint, pins, label, pin_note, role) or None if not in the catalogue."""
    if type_ in _FIXED:
        lib, fp, pins, label, note = _FIXED[type_]
        role = "ic" if type_ in IC_TYPES else "connector" if type_ in CONNECTOR_TYPES else "passive"
        return dict(lib=lib, footprint=fp, pins=pins, label=label, pin_note=note, role=role)
    m = HEADER_RE.match(type_ or "")
    if m and HEADER_MIN <= int(m.group(1)) <= HEADER_MAX:
        n = int(m.group(1))
        return dict(lib="Connector_PinHeader_2.54mm", footprint=f"PinHeader_1x{n:02d}_P2.54mm_Vertical",
                    pins=[str(i) for i in range(1, n + 1)], label=f"1x{n} pin header (2.54 mm)",
                    pin_note=f"1..{n} in a row", role="connector")
    return None


def catalogue_text() -> str:
    lines = []
    for t in sorted(_FIXED):
        e = lookup(t)
        lines.append(f"- {t}: {e['label']}; pins {e['pin_note']}")
    lines.append(f"- pin_header_1xN (N = {HEADER_MIN}..{HEADER_MAX}, e.g. pin_header_1x6): "
                 "single-row 2.54 mm pin header; pins 1..N")
    return "\n".join(lines)


def validate(spec) -> list[str]:
    """Hard validation. Returns a list of error strings (empty = valid)."""
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["spec is not a JSON object"]
    comps = spec.get("components")
    nets = spec.get("nets")
    if not isinstance(comps, list) or not comps:
        errs.append("components must be a non-empty list")
        comps = []
    if not isinstance(nets, list) or not nets:
        errs.append("nets must be a non-empty list of {name, pins}")
        nets = []
    if len(comps) > 30:
        errs.append(f"{len(comps)} components: this generator is for small boards (max 30)")
    pins_of: dict[str, list[str]] = {}
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            errs.append(f"components[{i}] is not an object")
            continue
        ref, typ = c.get("ref"), c.get("type")
        if not isinstance(ref, str) or not re.match(r"^[A-Z]{1,3}[0-9]{1,3}$", ref or ""):
            errs.append(f"components[{i}].ref {ref!r} must look like R1, C2, U1, J1, D1, SW1, Q1")
            continue
        if ref in pins_of:
            errs.append(f"duplicate component reference {ref}")
            continue
        e = lookup(typ) if isinstance(typ, str) else None
        if e is None:
            errs.append(f"{ref}: type {typ!r} is not in the catalogue ({', '.join(sorted(_FIXED))}, pin_header_1xN)")
            continue
        pins_of[ref] = e["pins"]
        if not isinstance(c.get("purpose"), str) or not c["purpose"].strip():
            errs.append(f"{ref}: missing one-line purpose")
    seen_pin: dict[str, str] = {}
    names = set()
    for i, n in enumerate(nets):
        if not isinstance(n, dict):
            errs.append(f"nets[{i}] is not an object")
            continue
        name, pins = n.get("name"), n.get("pins")
        if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9_+\-]{1,24}$", name or ""):
            errs.append(f"nets[{i}].name {name!r} must be 1-24 chars of letters/digits/_/+/-")
            continue
        if name in names:
            errs.append(f"duplicate net name {name}")
        names.add(name)
        if not isinstance(pins, list) or len(set(pins)) < 2:
            errs.append(f"net {name}: needs at least 2 distinct pins, has {pins!r}")
            continue
        for p in pins:
            if not isinstance(p, str) or p.count(".") != 1:
                errs.append(f"net {name}: pin {p!r} must be written REF.pin, e.g. U1.3")
                continue
            ref, pin = p.split(".")
            if ref not in pins_of:
                errs.append(f"net {name}: pin {p} refers to unknown component {ref}")
            elif pin not in pins_of[ref]:
                errs.append(f"net {name}: {ref} has no pin {pin!r} (valid: {', '.join(pins_of[ref])})")
            elif p in seen_pin and seen_pin[p] != name:
                errs.append(f"pin {p} is on two nets: {seen_pin[p]} and {name}")
            else:
                seen_pin[p] = name
    for ref in pins_of:
        if not any(k.startswith(ref + ".") for k in seen_pin):
            errs.append(f"component {ref} is not connected to any net")
    b = spec.get("board")
    if b is not None:
        if (not isinstance(b, dict) or not all(isinstance(b.get(k), (int, float)) for k in ("width_mm", "height_mm"))
                or not (10 <= b["width_mm"] <= 160 and 10 <= b["height_mm"] <= 160)):
            errs.append("board must be {width_mm, height_mm}, each between 10 and 160")
    return errs
