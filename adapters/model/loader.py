"""Resolve config.backends + config.model.<phase> → concrete ModelBackend instances.
Model is never hardcoded — config.model.<phase> picks {backend, models} per phase.

backend "auto" resolves at call time: a local OpenAI-compatible server if one is reachable (free +
private) → the Claude CLI / subscription if installed (zero setup — every Claude Code user has it)
→ a cloud API key if set. A local server is a bonus, not a dependency, and ANY one works: discovery
probes a list of common servers (LM Studio / Ollama / llama.cpp / Jan) and reuses whatever model is
already loaded, so there is nothing to install or download."""
from __future__ import annotations
import os
import shutil

from .cloud import CloudBackend
from .local import LocalBackend, list_models
from .stub import StubBackend
from .subscription import SubscriptionBackend

# sensible per-(backend, phase) default models when config names none
DEFAULT_MODELS = {
    "local": "qwen/qwen3.6-35b-a3b",
    "cloud": "claude-sonnet-4-6",
    "subscription": {"extract": "haiku", "*": "sonnet"},  # extraction is high-volume/low-judgment
}

# Common OpenAI-compatible local servers, probed in order (first healthy wins). Bases include the
# OpenAI route prefix to match the existing baseUrl convention (GET {base}/models). Override or
# extend via backends.local.discover; backends.local.baseUrl is always tried FIRST (back-compat).
DEFAULT_DISCOVER = [
    "http://localhost:1234/v1",   # LM Studio
    "http://localhost:11434/v1",  # Ollama
    "http://localhost:8080/v1",   # llama.cpp (llama-server)
    "http://localhost:1337/v1",   # Jan
]


def _local_bases(cfg) -> list[str]:
    """Ordered, de-duplicated candidate base URLs: the configured baseUrl first (back-compat), then
    backends.local.discover (or the built-in default set)."""
    spec = (cfg.backends or {}).get("local", {})
    bases: list[str] = []
    for b in [spec.get("baseUrl"), *(spec.get("discover") or DEFAULT_DISCOVER)]:
        if b and b not in bases:
            bases.append(b)
    return bases


def _capable_score(model_id: str) -> tuple:
    """Heuristic 'most capable' rank for picking a model when none is configured: prefer the larger
    parameter count parsed from the name (…70b / 32b / 7b…), then the longer name as a tiebreak."""
    import re
    sizes = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*[bB]\b", model_id)]
    return (max(sizes) if sizes else 0.0, len(model_id))


def resolve_local_model(cfg, models: list[str], phase: str = "extract") -> str | None:
    """Pick the extraction model on a live server: the configured id if the server actually has it,
    else a sensible available one (largest/most-capable by name, else first). Returns None only when
    the server reports no models at all."""
    if not models:
        return None
    pc = (cfg.model or {}).get(phase, {})
    want = (pc.get("models", {}) or {}).get("local") or (cfg.backends or {}).get("local", {}).get("model")
    if want and want in models:
        return want
    return max(models, key=_capable_score)


def probe_local(cfg):
    """First healthy local server → (base_url, [model_ids]); None if none is up. 'Healthy' = GET
    {base}/models returns >=1 model. Cheap (~1.5s/probe), ordered, first-healthy-wins."""
    for base in _local_bases(cfg):
        models = list_models(base)
        if models:
            return base, models
    return None


def local_reachable(cfg, timeout=1.5) -> bool:
    return probe_local(cfg) is not None


def detect_backend(cfg) -> str:
    """The auto chain. Raises with an actionable message only when NOTHING is available."""
    if local_reachable(cfg):
        return "local"
    if shutil.which((cfg.backends or {}).get("subscription", {}).get("bin", "claude")):
        return "subscription"
    if os.environ.get((cfg.backends or {}).get("cloud", {}).get("apiKeyEnv", "ANTHROPIC_API_KEY")):
        return "cloud"
    raise RuntimeError(
        "no model backend available: start a local OpenAI-compatible server (e.g. LM Studio, "
        "Ollama, or llama.cpp), or install the `claude` CLI, or set ANTHROPIC_API_KEY")


def build_backend(name, cfg, model=None):
    spec = (cfg.backends or {}).get(name, {})
    if name == "local":
        # Point the client at the live server and a model it actually has, discovered at call time.
        found = probe_local(cfg)
        if found:
            base, models = found
            return LocalBackend(base_url=base,
                                model=model or resolve_local_model(cfg, models) or DEFAULT_MODELS["local"])
        # No server up: fall back to configured defaults (callers that pre-checked won't hit this).
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
