"""Gemini's read on one connection the agents could not make.

Two calls, both plain REST (no SDK needed, so this runs in whatever Python serves the viewer):

  * gemini-4-neon (gemini-4-hydrogen if it is slow) gets a close-up picture of the stuck area plus the facts the viewer already
    knows (which pads, which signal, what is in the way) and writes one or two sentences: why it
    is stuck and the most practical fix.
  * gemini-decision-1 (the Decisions API) answers one yes/no question about the same picture:
    could the agents fix this by moving at most one track? It returns a calibrated probability,
    which the page shows as a confidence, not as a promise.

The key is read from GEMINI_API_KEY, or from the .env file at the repository root.

    python pcb/gemini_explain.py picture.jpg "IC1.39" "P3.4" "N-000006" "the /+5V track on the front layer"
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta"
# Tried in order. Carbon was measured on 21 Nov and timed out at 90 s twice on this exact request,
# while Neon answered in about 9 s and Hydrogen in about 4 s, so it is not used for a live page.
TEXT_MODELS = ("gemini-4-neon", "gemini-4-hydrogen")
DECISION_MODEL = "gemini-decision-1"
ROOT = Path(__file__).resolve().parent.parent


def api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?(?:GEMINI_API_KEY|GOOGLE_API_KEY)\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                return m.group(1)
    return None


def _post(path: str, body: dict, key: str, timeout: float) -> dict:
    req = urllib.request.Request(f"{API}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300].replace(key, "<key>")
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def split_data_url(image: str | None) -> tuple[str, str] | None:
    """('image/jpeg', base64 text) from a data URL, or None."""
    m = re.match(r"data:(image/[a-z+]+);base64,(.+)", image or "", re.S)
    return (m.group(1), m.group(2)) if m else None


def facts(q: dict) -> str:
    return (f"Board: {q.get('board') or 'unknown'}. "
            f"Unmade connection: {q.get('from') or '?'} to {q.get('to') or '?'}"
            f"{' (signal ' + str(q['net']) + ')' if q.get('net') else ''}. "
            f"What the viewer found in the way: {q.get('blockers') or 'nothing identified'}.")


def explain_text(q: dict, key: str, model: str, timeout: float = 25) -> str:
    parts: list[dict] = [{"text": (
        "You are helping a circuit board engineer. A team of routing agents routed this board but "
        "could not make one connection. " + facts(q) + " "
        "The picture is a top-down close-up of that area; tracks drawn in red are the ones in the way. "
        "In one or two plain sentences, say why the connection is stuck and the most practical way to "
        "fix it. Use only what the picture and the facts above show. No preamble, no lists.")}]
    img = split_data_url(q.get("image"))
    if img:
        parts.append({"inline_data": {"mime_type": img[0], "data": img[1]}})
    out = _post(f"models/{model}:generateContent",
                {"contents": [{"role": "user", "parts": parts}],
                 # Low thinking on purpose. At the default, Carbon spent a 4,096-token budget on
                 # thinking alone and returned no text after about a minute; low answers in ~5 s.
                 "generationConfig": {"maxOutputTokens": 8192, "thinkingConfig": {"thinkingLevel": "low"}}},
                key, timeout)
    cand = (out.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])
                   if not p.get("thought"))
    text = " ".join(text.split())
    if not text:
        raise RuntimeError(f"no text returned (finish reason {cand.get('finishReason')})")
    return text


def fixable_offer(q: dict, key: str, timeout: float = 30) -> tuple[bool | None, float | None]:
    """(offer, confidence) from the Decisions API, or (None, None) when it cannot answer."""
    inp: list[dict] = [{"type": "text", "text": facts(q)}]
    img = split_data_url(q.get("image"))
    if img:
        inp.append({"type": "image", "data": img[1], "mime_type": img[0]})
    out = _post("decisions", {
        "model": DECISION_MODEL, "input": inp,
        "questions": {"fixable": {
            "type": "noul",
            "instructions": "Could routing agents make this connection by moving or re-routing at most one existing track?",
            "criteria": {"true_description": "One track is in the way and there is free space to move it or go around it.",
                         "false_description": "Several tracks or parts block the path, or there is no free space on any layer."}}}},
        key, timeout)
    ans = (out.get("answers") or {}).get("fixable") or {}
    p = ans.get("noul")
    if p is None:
        return None, None
    return bool(p >= 0.5), ans.get("confidence")


def explain(q: dict) -> dict:
    """What POST /explain returns. Never raises: a failed call leaves its field empty."""
    import threading
    key = api_key()
    if not key:
        return {"text": "", "offer": None, "confidence": None, "error": "no GEMINI_API_KEY"}
    out: dict = {"text": "", "offer": None, "confidence": None, "model": None, "decision_model": DECISION_MODEL}

    def text() -> None:
        errors = []
        for model in TEXT_MODELS:
            try:
                out["text"], out["model"] = explain_text(q, key, model), model
                return
            except Exception as e:
                errors.append(f"{model}: {type(e).__name__}: {e}"[:160])
        out["error"] = "; ".join(errors)

    def decision() -> None:
        try:
            out["offer"], out["confidence"] = fixable_offer(q, key)
        except Exception as e:
            out["decision_error"] = f"{type(e).__name__}: {e}"[:300]

    threads = [threading.Thread(target=text), threading.Thread(target=decision)]   # the two calls run side by side
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


if __name__ == "__main__":
    import sys
    pic = Path(sys.argv[1])
    mime = "image/png" if pic.suffix.lower() == ".png" else "image/jpeg"
    query = {"board": "test", "from": sys.argv[2], "to": sys.argv[3], "net": sys.argv[4], "blockers": sys.argv[5],
             "image": f"data:{mime};base64," + base64.b64encode(pic.read_bytes()).decode()}
    print(json.dumps(explain(query), indent=1))
