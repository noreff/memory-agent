"""Cloud completion backend = Anthropic Messages API (or any OpenAI-compatible host via base_url).
Metered $ — needs an API key in the configured env var. For the subscription (no key), use the
subscription backend instead."""
from __future__ import annotations
import json
import os
import urllib.request

from .base import ModelBackend, Result, Task


class CloudBackend(ModelBackend):
    name = "cloud"
    tools = False
    structured_output = "json"

    def __init__(self, base_url="https://api.anthropic.com", api_key_env="ANTHROPIC_API_KEY",
                 model="claude-sonnet-4-6", timeout=300, version="2023-06-01"):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model
        self.timeout = timeout
        self.version = version

    def run(self, task: Task) -> Result:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"{self.api_key_env} not set — cloud backend needs an API key")
        payload = {
            "model": task.model or self.model, "max_tokens": task.max_tokens,
            "temperature": task.temperature,
            "messages": [{"role": "user", "content": task.prompt}],
        }
        if task.system:
            payload["system"] = task.system
        req = urllib.request.Request(
            f"{self.base_url}/v1/messages", data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": self.version})
        resp = json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        return Result(text=text.strip(), finish=resp.get("stop_reason", "stop"), backend=self.name)
