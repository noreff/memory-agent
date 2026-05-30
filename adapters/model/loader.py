"""Resolve config.backends + config.model.<phase> → concrete ModelBackend instances.
Model is never hardcoded — config.model.<phase> picks {backend, model} per phase."""
from __future__ import annotations

from .cloud import CloudBackend
from .local import LocalBackend
from .stub import StubBackend
from .subscription import SubscriptionBackend


def build_backend(name, cfg, model=None):
    spec = (cfg.backends or {}).get(name, {})
    if name == "local":
        return LocalBackend(base_url=spec.get("baseUrl", "http://localhost:1234/v1"),
                            model=model or "qwen/qwen3.6-35b-a3b")
    if name == "cloud":
        return CloudBackend(base_url=spec.get("baseUrl", "https://api.anthropic.com"),
                            api_key_env=spec.get("apiKeyEnv", "ANTHROPIC_API_KEY"),
                            model=model or "claude-sonnet-4-6")
    if name == "subscription":
        return SubscriptionBackend(model=model or "sonnet")
    if name == "stub":
        return StubBackend()
    raise ValueError(f"unknown model backend: {name!r}")


def backend_for_phase(cfg, phase, override=None):
    """Pick the backend+model for a phase from config (or an explicit override name)."""
    pc = (cfg.model or {}).get(phase, {})
    name = override or pc.get("backend", "local")
    return build_backend(name, cfg, model=pc.get("model"))
