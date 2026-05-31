"""Resolve config.backends + config.model.<phase> → concrete ModelBackend instances.
Model is never hardcoded — config.model.<phase> picks {backend, models} per phase.

backend "auto" resolves at call time: local server if reachable (free + private) → the Claude
CLI / subscription if installed (zero setup — every Claude Code user has it) → a cloud API key
if set. LM Studio is a bonus, not a dependency."""
from __future__ import annotations
import os
import shutil
import urllib.request

from .cloud import CloudBackend
from .local import LocalBackend
from .stub import StubBackend
from .subscription import SubscriptionBackend

# sensible per-(backend, phase) default models when config names none
DEFAULT_MODELS = {
    "local": "qwen/qwen3.6-35b-a3b",
    "cloud": "claude-sonnet-4-6",
    "subscription": {"extract": "haiku", "*": "sonnet"},  # extraction is high-volume/low-judgment
}


def local_reachable(cfg, timeout=1.5) -> bool:
    base = (cfg.backends or {}).get("local", {}).get("baseUrl", "http://localhost:1234/v1")
    try:
        urllib.request.urlopen(f"{base.rstrip('/')}/models", timeout=timeout)
        return True
    except Exception:
        return False


def detect_backend(cfg) -> str:
    """The auto chain. Raises with an actionable message only when NOTHING is available."""
    if local_reachable(cfg):
        return "local"
    if shutil.which((cfg.backends or {}).get("subscription", {}).get("bin", "claude")):
        return "subscription"
    if os.environ.get((cfg.backends or {}).get("cloud", {}).get("apiKeyEnv", "ANTHROPIC_API_KEY")):
        return "cloud"
    raise RuntimeError(
        "no model backend available: start a local OpenAI-compatible server (e.g. LM Studio on "
        ":1234), or install the `claude` CLI, or set ANTHROPIC_API_KEY")


def build_backend(name, cfg, model=None):
    spec = (cfg.backends or {}).get(name, {})
    if name == "local":
        return LocalBackend(base_url=spec.get("baseUrl", "http://localhost:1234/v1"),
                            model=model or spec.get("model", DEFAULT_MODELS["local"]))
    if name == "cloud":
        return CloudBackend(base_url=spec.get("baseUrl", "https://api.anthropic.com"),
                            api_key_env=spec.get("apiKeyEnv", "ANTHROPIC_API_KEY"),
                            model=model or spec.get("model", DEFAULT_MODELS["cloud"]))
    if name == "subscription":
        return SubscriptionBackend(model=model or "sonnet", bin=spec.get("bin", "claude"))
    if name == "stub":
        return StubBackend()
    if name == "auto":
        return build_backend(detect_backend(cfg), cfg, model=model)
    raise ValueError(f"unknown model backend: {name!r}")


def backend_for_phase(cfg, phase, override=None):
    """Pick the backend+model for a phase from config (or an explicit override name).

    Per-phase config shape: {"backend": "auto"|name, "models": {backendName: modelId}, "model": id}
    ("model" applies only when the configured backend itself is chosen — a local model id must
    never be sent to a cloud backend)."""
    pc = (cfg.model or {}).get(phase, {})
    name = override or pc.get("backend", "auto")
    if name == "auto":
        name = detect_backend(cfg)
    models = pc.get("models", {})
    model = models.get(name)
    if model is None and pc.get("backend") == name:
        model = pc.get("model")
    if model is None and name == "subscription":
        sub = DEFAULT_MODELS["subscription"]
        model = sub.get(phase, sub["*"])
    return build_backend(name, cfg, model=model)
