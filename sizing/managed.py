"""Small reusable driver for Gemini Managed Agents (Antigravity agent) with client-side tools.

The agent runs in Google's hosted sandbox. When it calls one of OUR function tools, the
interaction pauses with status `requires_action`; we run the Python handler locally and resume
with the result (stateful: previous_interaction_id + same environment). Every event, tool call
and interaction id is logged to `run_dir`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from sizing import opamp2stg as oa

AGENT = "antigravity-preview-09-2026"


def load_key() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        for line in (oa.ROOT / ".env").read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()


def client():
    load_key()
    from google import genai
    return genai.Client(http_options={"api_version": "v1beta"})


def _stream(c, run_dir: Path, **kw):
    stream = c.interactions.create(stream=True, **kw)
    last = None
    for ev in stream:
        et = getattr(ev, "event_type", None) or getattr(ev, "type", None)
        if getattr(ev, "interaction", None) is not None:
            last = ev.interaction
        if et == "step.start":
            st = getattr(ev, "step", None)
            kind = getattr(st, "type", None)
            if kind == "function_call":
                print(f"  agent -> {getattr(st, 'name', '?')}()", flush=True)
            elif kind and "code_execution" in kind and kind.endswith("call"):
                print("  agent -> code_execution", flush=True)
        try:
            with open(run_dir / "events.jsonl", "a") as f:
                f.write(json.dumps(ev.model_dump(exclude_none=True), default=str)[:4000] + "\n")
        except Exception:
            pass
    if last is None:
        raise RuntimeError("stream ended without an interaction")
    return c.interactions.get(id=last.id)


def run(c, run_dir: Path, input_blocks, system: str, tools: list, handlers: dict[str, Callable],
        model: str, token_budget: int, wall_s: float, environment="remote") -> tuple:
    """Run an agent to completion, servicing calls to `handlers` locally. Returns
    (final interaction, turns, list of per-turn total_tokens)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"type": "antigravity", "model": model, "max_total_tokens": token_budget}
    t0 = time.time()
    it = _stream(c, run_dir, agent=AGENT, input=input_blocks, environment=environment,
                 agent_config=cfg, tools=tools, system_instruction=system)
    turns, usage = 1, []
    while True:
        usage.append(getattr(getattr(it, "usage", None), "total_tokens", None))
        with open(run_dir / "interactions.jsonl", "a") as f:
            f.write(json.dumps({"turn": turns, "id": it.id, "environment_id": it.environment_id,
                                "status": it.status, "total_tokens": usage[-1],
                                "t": round(time.time() - t0, 1)}) + "\n")
        if it.status != "requires_action":
            break
        if time.time() - t0 > wall_s:
            try:
                c.interactions.cancel(id=it.id)
            except Exception:
                pass
            raise TimeoutError(f"agent exceeded {wall_s}s wall clock")
        steps = it.steps or []
        done = {getattr(s, "call_id", None) for s in steps if s.type == "function_result"}
        calls = [s for s in steps if s.type == "function_call" and s.name in handlers and s.id not in done]
        results = []
        for call in calls:
            args = dict(call.arguments or {})
            try:
                res = handlers[call.name](args)
                err = False
            except Exception as e:   # report tool errors to the agent instead of crashing
                res, err = {"error": f"{type(e).__name__}: {e}"}, True
            with open(run_dir / "tool_calls.jsonl", "a") as f:
                f.write(json.dumps({"turn": turns, "tool": call.name, "args": args, "result": res,
                                    "is_error": err}, default=str) + "\n")
            item = {"type": "function_result", "name": call.name, "call_id": call.id, "result": res}
            if err:
                item["is_error"] = True
            results.append(item)
        turns += 1
        kw = dict(agent=AGENT, previous_interaction_id=it.id, environment=it.environment_id,
                  input=results, agent_config=cfg, tools=tools, system_instruction=system)
        try:
            it = _stream(c, run_dir, **kw)
        except Exception as e:   # some API versions reject re-sent tools/config on continuation
            if "400" in str(e) or "tools" in str(e).lower():
                kw.pop("tools"), kw.pop("agent_config")
                it = _stream(c, run_dir, **kw)
            else:
                raise
    return it, turns, usage
