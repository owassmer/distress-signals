"""A run's transcript as a readable page: out/runs/<run_id>.md from out/runs/<run_id>.jsonl.

Each turn shows the model's reasoning summary, what it said, the tools it called with their arguments,
and what came back (shortened; the JSON lines hold everything)."""

import json
from pathlib import Path

RESULT_CHARS = 1500
ARG_CHARS = 1200


def _short(text, n):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[:n] + f" … [{len(text) - n:,} more characters in the JSON lines]"


def _result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(x.get("text", "") if x.get("type") == "text" else f"[{x.get('type')}]" for x in c)
    return c if isinstance(c, str) else json.dumps(c, default=str)


def _quote(text):
    return "\n".join("> " + line if line.strip() else ">" for line in text.strip().splitlines())


def render(jsonl, md=None, title=None, assignment=None):
    """Write the readable transcript next to the JSON lines and return its path. The assignment (system
    prompt and prompt) heads it, folded."""
    jsonl = Path(jsonl)
    md = Path(md) if md else jsonl.with_suffix(".md")
    assignment = Path(assignment) if assignment else jsonl.with_suffix(".assignment.md")
    events = []
    for line in jsonl.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    names, lines, turn, reasoning_turns = {}, [f"# {title or jsonl.stem}", ""], 0, 0
    for e in events:
        t = e.get("type")
        if t == "system" and e.get("subtype") == "init":
            mcp = [s.get("name") + (f" ({s.get('status')})" if s.get("status") else "") for s in e.get("mcp_servers", [])]
            lines += [f"Model `{e.get('model')}`, Claude Code {e.get('claude_code_version')}. MCP servers: "
                      f"{', '.join(mcp) or 'none'}. Tools: {', '.join(e.get('tools', []))}.", ""]
        elif t == "assistant" and not e.get("parent_tool_use_id"):
            turn += 1
            lines.append(f"## Step {turn}")
            for b in e["message"].get("content", []):
                if b.get("type") == "thinking":
                    if b.get("thinking", "").strip():
                        reasoning_turns += 1
                        lines += ["**Reasoning summary**", "", _quote(b["thinking"]), ""]
                    else:
                        lines += ["*Reasoning happened here; no summary was returned.*", ""]
                elif b.get("type") == "redacted_thinking":
                    lines += ["*Reasoning redacted by the API.*", ""]
                elif b.get("type") == "text" and b.get("text", "").strip():
                    lines += [b["text"].strip(), ""]
                elif b.get("type") == "tool_use":
                    names[b["id"]] = b["name"]
                    lines += [f"**Calls `{b['name']}`**", "", "```json", _short(b.get("input"), ARG_CHARS), "```", ""]
        elif t == "user" and not e.get("parent_tool_use_id"):
            content = e.get("message", {}).get("content")
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_result":
                    flag = " (error)" if b.get("is_error") else ""
                    lines += [f"**`{names.get(b.get('tool_use_id'), 'tool')}` returned{flag}**", "", "```",
                              _short(_result_text(b), RESULT_CHARS), "```", ""]
        elif t == "result":
            lines += ["## Result", "", f"{e.get('subtype')}: {e.get('num_turns')} turns, "
                      f"{(e.get('duration_ms') or 0) / 60000:.1f} minutes, ${e.get('total_cost_usd') or 0:.2f} reported usage.", ""]
            if e.get("structured_output") is not None:
                lines += ["```json", json.dumps(e["structured_output"], indent=1, ensure_ascii=False), "```", ""]
            elif e.get("result"):
                lines += [str(e["result"]), ""]
    head = [f"{turn} steps, {reasoning_turns} with a reasoning summary. Full events: `{jsonl.name}`.\n"]
    if assignment.exists():
        head += ["<details><summary>The assignment: system prompt and prompt</summary>", "",
                 assignment.read_text().split("\n", 1)[1].strip(), "", "</details>", ""]
    lines[2:2] = head
    md.write_text("\n".join(lines))
    return md
