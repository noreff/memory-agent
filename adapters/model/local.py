"""Local completion backend = any OpenAI-compatible server (LM Studio, Ollama, llama.cpp, Jan, …).
Plain JSON, NEVER response_format json_schema: LM Studio's structured output (Outlines) doesn't
forward a repetition penalty to the MLX backend, sending MoE models into degenerate loops. Ask for
JSON, parse leniently in code."""
from __future__ import annotations
import json
import urllib.request

from .base import ModelBackend, Result, Task


def list_models(base_url: str, timeout: float = 1.5) -> list[str]:
    """GET {base}/models on an OpenAI-compatible server → list of model ids ([] if unreachable).
    Cheap discovery probe: a 200 with >=1 id means the server is healthy and has a usable model."""
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/models")
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return []
    return [m["id"] for m in resp.get("data", []) if isinstance(m, dict) and m.get("id")]


class LocalBackend(ModelBackend):
    name = "local"
    tools = False
    structured_output = "json"

    def __init__(self, base_url="http://localhost:1234/v1", model="qwen/qwen3.6-35b-a3b", timeout=900):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def run(self, task: Task) -> Result:
        messages = []
        if task.system:
            messages.append({"role": "system", "content": task.system})
        messages.append({"role": "user", "content": task.prompt})
        payload = {
            "model": task.model or self.model, "messages": messages,
            "temperature": task.temperature, "top_p": 0.9, "max_tokens": task.max_tokens,
        }
        if task.extra:  # e.g. anti-repeat penalties on a retry (plain JSON forwards these to MLX)
            payload.update(task.extra)
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())
        choice = resp["choices"][0]
        msg = choice["message"]
        text = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
        return Result(text=text, finish=choice.get("finish_reason", "stop"), backend=self.name)
