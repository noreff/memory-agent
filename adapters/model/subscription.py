"""Subscription backend = run work on the Claude Code subscription via headless `claude -p`
(no API key). In-session / developer-in-the-loop use (see DESIGN.md 'прикол'); each call spawns a
full agent, so it is heavy — for bulk backfill prefer the engine/backfill.js Workflow. Default model
is sonnet to spare rate-limit budget."""
from __future__ import annotations
import subprocess

from .base import ModelBackend, Result, Task


class SubscriptionBackend(ModelBackend):
    name = "subscription"
    tools = True
    structured_output = "json"

    def __init__(self, model="sonnet", timeout=600, bin="claude"):
        self.model = model
        self.timeout = timeout
        self.bin = bin

    def run(self, task: Task) -> Result:
        prompt = f"{task.system}\n\n{task.prompt}" if task.system else task.prompt
        cmd = [self.bin, "-p", "--model", task.model or self.model]
        try:
            out = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                 timeout=self.timeout)
        except FileNotFoundError:
            raise RuntimeError("`claude` CLI not found — needed for the subscription backend")
        finish = "stop" if out.returncode == 0 else "error"
        return Result(text=(out.stdout or "").strip(), finish=finish, backend=self.name)
