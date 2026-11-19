"""Agent calls through the local Claude Code CLI (its signed-in session; no API key).

The CLI runs in stream-json mode both ways. Before the prompt, a control request asks for summarized
thinking (thinking_display "summarized"), so the API returns the model's reasoning summaries; in print
mode the showThinkingSummaries setting doesn't reach the request. Every event the CLI emits (system,
assistant turns with their reasoning, tool calls, tool results, the final result) is written to the
transcript file as it arrives, so a run that fails or times out still leaves its transcript."""

import json
import os
import subprocess
import tempfile
import threading

MODEL = os.environ.get("AGENT_MODEL", "opus")
EFFORT = os.environ.get("AGENT_EFFORT", "xhigh")  # reasoning effort, set explicitly rather than left to the CLI default

CONTROLS = [
    {"type": "control_request", "request_id": "init", "request": {"subtype": "initialize"}},
    {"type": "control_request", "request_id": "thinking",
     "request": {"subtype": "set_max_thinking_tokens", "thinking_display": "summarized"}},
]


def command(system, schema, model=None, effort=None, tools=None, mcp_config=None):
    """The claude command line for one session. `tools` lists built-in tools and mcp__server__tool names,
    all allowed without prompting; `mcp_config` is a config file path, used alone (--strict-mcp-config)."""
    tools = list(tools or [])
    builtin = [t for t in tools if not t.startswith("mcp__")]
    return ["claude", "-p", "--model", model or MODEL, "--effort", effort or EFFORT, "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose",
            "--tools", ",".join(builtin), *(["--allowedTools", ",".join(tools)] if tools else []),
            *(["--mcp-config", mcp_config] if mcp_config else []), "--strict-mcp-config",
            "--setting-sources", "", "--no-session-persistence", "--system-prompt", system,
            "--json-schema", json.dumps(schema)]


def messages(prompt):
    return [*CONTROLS, {"type": "user", "message": {"role": "user", "content": prompt}}]


def ask(system, prompt, schema, transcript, model=None, effort=None, timeout=300, tools=None, mcp_config=None, env=None):
    """One structured call. Returns (the object matching `schema`, run stats). Writes the full event
    stream to `transcript` (JSON lines)."""
    cmd = command(system, schema, model, effort, tools, mcp_config)
    result, timed_out = None, threading.Event()
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryFile("w+") as err, open(transcript, "a") as out:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True, cwd=cwd,
                             env={**os.environ, **(env or {})})
        timer = threading.Timer(timeout, lambda: (timed_out.set(), p.kill()))
        timer.start()
        try:
            p.stdin.write("".join(json.dumps(m) + "\n" for m in messages(prompt)))
            p.stdin.close()            # the CLI finishes the queued turn, then exits at end of input
            for line in p.stdout:
                out.write(line)
                out.flush()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "result":
                    result = event
            p.wait()
        finally:
            timer.cancel()
        err.seek(0)
        stderr = err.read()
    if timed_out.is_set():
        raise TimeoutError(f"claude ran past {timeout}s and was stopped; transcript in {transcript}")
    if result is None:
        raise RuntimeError(f"claude exited {p.returncode} without a result: {stderr[-600:]}")
    stats = {k: result.get(k) for k in ("num_turns", "total_cost_usd", "duration_ms")} | {
        "thinking_tokens": {m: u.get("thinkingTokens") for m, u in (result.get("modelUsage") or {}).items()}}
    if result.get("is_error") or result.get("structured_output") is None:
        raise RuntimeError(f"claude returned no structured output ({result.get('subtype')}): {str(result.get('result'))[:600]}")
    return result["structured_output"], stats
