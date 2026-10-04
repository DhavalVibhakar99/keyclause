"""Minimal Anthropic Messages API client (standard library only).

- Tool use plus a schema check in code, so answers always have our JSON structure.
- Every response is cached on disk, keyed by a hash of the full request, so reruns
  are free and byte-for-byte reproducible.
- Every call is written to the audit log.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

from . import audit, config


class LLMError(RuntimeError):
    pass


def _cache_path(key):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"{key}.json"


def backend():
    """'api' if an API key is available, else 'claude_cli' if Claude Code is installed.
    Override with NAV_LLM_BACKEND=api|claude_cli."""
    forced = os.environ.get("NAV_LLM_BACKEND")
    if forced:
        return forced
    if os.environ.get("NAV_ANTHROPIC_API_KEY") or (config.ROOT / ".anthropic_key").exists():
        return "api"
    return "claude_cli" if _claude_bin() else "api"


def _claude_bin():
    import shutil
    p = shutil.which("claude")
    if p:
        return p
    home = os.path.expanduser("~/.local/bin/claude")
    return home if os.path.exists(home) else None


def call_tool(system, user, tool, *, label, max_tokens=None, use_cache=True):
    """Send one request whose answer must match `tool['input_schema']`. Returns the answer dict."""
    if backend() == "claude_cli":
        return _call_cli(system, user, tool, label=label, use_cache=use_cache)
    return _call_api(system, user, tool, label=label, max_tokens=max_tokens, use_cache=use_cache)


def _call_cli(system, user, tool, *, label, use_cache=True):
    """Claude Code non-interactive mode on the user's subscription (no API key).

    Document + task go in on stdin; --json-schema forces our exact structure, returned in
    `structured_output`. Runs from an empty folder with --permission-mode dontAsk so the
    model can only read and answer. Not --bare: bare mode ignores the subscription login.
    """
    import subprocess
    schema = json.dumps(tool["input_schema"], sort_keys=True)
    key_src = json.dumps({"backend": "claude_cli", "model": os.environ.get("NAV_CLI_MODEL", ""),
                          "system": system, "user": user, "schema": schema}, sort_keys=True)
    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    path = _cache_path(key)
    if use_cache and path.exists():
        resp = json.loads(path.read_text(encoding="utf-8"))
        audit.log("llm_cache_hit", label=label, request_hash=key, backend="claude_cli")
        return resp["structured_output"]
    exe = _claude_bin()
    if not exe:
        raise LLMError("Claude Code CLI not found; install it or provide .anthropic_key")
    workdir = config.CACHE_DIR / "cli_workdir"      # empty folder: no project context is loaded
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-p", "The document and the task are on stdin. Answer only with the structured output.",
           "--output-format", "json", "--json-schema", schema,
           "--system-prompt", system, "--permission-mode", "dontAsk"]
    if os.environ.get("NAV_CLI_MODEL"):
        cmd += ["--model", os.environ["NAV_CLI_MODEL"]]
    last = ""
    for attempt in range(3):
        t0 = time.time()
        try:
            r = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=workdir, timeout=900)
        except subprocess.TimeoutExpired:
            last = "timed out after 900s"
            audit.log("llm_cli_error", label=label, detail=last, attempt=attempt)
            continue
        try:
            resp = json.loads(r.stdout)
        except json.JSONDecodeError:
            last = (r.stderr or r.stdout)[:500]
            audit.log("llm_cli_error", label=label, detail=last, attempt=attempt, returncode=r.returncode)
            time.sleep(10 * (attempt + 1))
            continue
        out = resp.get("structured_output")
        if resp.get("is_error") or not isinstance(out, dict):
            last = str(resp.get("result") or resp.get("subtype"))[:500]
            audit.log("llm_cli_error", label=label, detail=last, attempt=attempt)
            time.sleep(10 * (attempt + 1))
            continue
        audit.log("llm_call", label=label, request_hash=key, backend="claude_cli",
                  prompt_version=config.PROMPT_VERSION, seconds=round(time.time() - t0, 1),
                  est_cost_usd=resp.get("total_cost_usd"), usage=resp.get("usage"))
        path.write_text(json.dumps(resp, ensure_ascii=False, indent=1), encoding="utf-8")
        return out
    raise LLMError(f"{label}: Claude Code CLI failed: {last}")


def _decode_stringified(answer, schema):
    """Large tool inputs sometimes arrive with a nested array/object JSON-encoded as a string.
    Decode those top-level fields (structure only; content is untouched)."""
    if not isinstance(answer, dict):
        return answer
    for k, sub in (schema.get("properties") or {}).items():
        t = sub.get("type")
        types = t if isinstance(t, list) else [t]
        if isinstance(answer.get(k), str) and ("array" in types or "object" in types):
            try:
                answer[k] = json.loads(answer[k])
            except json.JSONDecodeError:
                pass
    return answer


def _schema_problem(answer, schema):
    """Error message if `answer` does not match `schema`, else None."""
    from .schema_check import validate
    errs = validate(answer, schema)
    return "; ".join(errs[:3]) if errs else None


def _call_api(system, user, tool, *, label, max_tokens=None, use_cache=True):
    # Current models reject forced tool_choice ("tool"/"any") and non-default temperature,
    # and our extraction schema is too large for structured outputs. So: tool_choice auto,
    # an explicit instruction to call the tool, and the answer is checked against the
    # schema in code (retried, never cached, if it is missing or malformed).
    body = {
        "model": config.MODEL,
        "max_tokens": max_tokens or config.MAX_OUTPUT_TOKENS,
        "system": system + f"\n\nAnswer only by calling the {tool['name']} tool exactly once. Do not answer in plain text.",
        "messages": [{"role": "user", "content": user}],
        "tools": [tool],
        "tool_choice": {"type": "auto"},
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    path = _cache_path(key)
    last = ""
    for attempt in range(3):
        cached = use_cache and attempt == 0 and path.exists()
        if cached:
            resp = json.loads(path.read_text(encoding="utf-8"))
            audit.log("llm_cache_hit", label=label, request_hash=key)
        else:
            resp = _post(body, label, key)
        stop = resp.get("stop_reason")
        if stop == "max_tokens":
            audit.log("llm_truncated", label=label, request_hash=key)
            raise LLMError(f"{label}: response truncated at max_tokens")
        if stop == "refusal":
            raise LLMError(f"{label}: model declined ({resp.get('stop_details')})")
        calls = [b for b in resp.get("content", []) if b.get("type") == "tool_use" and b.get("name") == tool["name"]]
        if not calls:
            last = f"no {tool['name']} call (stop_reason={stop})"
        else:
            answer = _decode_stringified(calls[0]["input"], tool["input_schema"])
            problem = _schema_problem(answer, tool["input_schema"])
            if problem is None:
                if not cached:
                    path.write_text(json.dumps(resp, ensure_ascii=False, indent=1), encoding="utf-8")
                return answer
            last = f"answer does not match schema: {problem}"
        audit.log("llm_bad_answer", label=label, request_hash=key, detail=last, attempt=attempt)
    raise LLMError(f"{label}: {last}")


def _post(body, label, key):
    # Key comes from a local file so it is never exported in the shell that runs
    # Claude Code (an exported ANTHROPIC_API_KEY would make Claude Code itself bill it).
    key_file = config.ROOT / ".anthropic_key"
    api_key = os.environ.get("NAV_ANTHROPIC_API_KEY") or (
        key_file.read_text().strip() if key_file.exists() else "")
    if not api_key:
        raise LLMError("Put the API key in the file .anthropic_key in the project folder "
                       "(or set NAV_ANTHROPIC_API_KEY) to run extraction.")
    req = urllib.request.Request(
        config.API_BASE.rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    delay = 5
    for attempt in range(5):
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                resp = json.loads(r.read().decode("utf-8"))
            usage = resp.get("usage", {})
            audit.log("llm_call", label=label, request_hash=key, model=resp.get("model"),
                      prompt_version=config.PROMPT_VERSION, seconds=round(time.time() - t0, 1),
                      input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                      stop_reason=resp.get("stop_reason"))
            return resp
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            audit.log("llm_http_error", label=label, status=e.code, detail=detail, attempt=attempt)
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(delay)
                delay *= 2
                continue
            raise LLMError(f"{label}: HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            audit.log("llm_network_error", label=label, detail=str(e), attempt=attempt)
            time.sleep(delay)
            delay *= 2
    raise LLMError(f"{label}: gave up after retries")
