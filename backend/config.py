"""Secret loading and runtime configuration for Lambda.

Secrets live in SSM Parameter Store (SecureString) under a single prefix so
they never touch git or the CloudFormation template. `load_secrets()` pulls
them once per warm container and injects them into os.environ, so the existing
modules (grok_client, opus_client, trader, discord_notifier) keep reading
os.getenv(...) unchanged.

Locally, if the env vars are already set (e.g. via .env / dotenv), the SSM
call is skipped entirely.
"""
import os

# SSM parameter names (without prefix) that map 1:1 to env vars.
_SECRET_KEYS = [
    "GROK_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "DISCORD_WEBHOOK_ALERTS",
    "DISCORD_WEBHOOK_TRADES",
    "API_SHARED_SECRET",
    "SENTRY_DSN",
]

_SSM_PREFIX = os.getenv("SSM_PREFIX", "/grok-trade")

_loaded = False

DECISION_ENGINES = ("grok", "opus")

# Keys without which the bot cannot trade at all, regardless of engine.
_BASE_REQUIRED = ["GROK_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"]


def decision_engine() -> str:
    """Which model makes the Stage 2 trade decision.

    grok : Grok decides directly from its own market report (default)
    opus : Claude Opus decides from Grok's report (the original 4-stage design)
    """
    engine = os.getenv("DECISION_ENGINE", "grok").strip().lower()
    if engine not in DECISION_ENGINES:
        print(f"[config] WARNING: unknown DECISION_ENGINE={engine!r}, using 'grok'")
        return "grok"
    return engine


def required_keys() -> list:
    keys = list(_BASE_REQUIRED)
    if decision_engine() == "opus":
        keys.append("ANTHROPIC_API_KEY")
    return keys


def secrets_status() -> dict:
    """Which secrets resolved to a non-empty value. Names only, never values.

    Surfaced by /health so a misconfigured deploy is diagnosable without
    reading logs or decrypting anything.
    """
    return {k: bool(os.getenv(k)) for k in _SECRET_KEYS}


def missing_required() -> list:
    """Required secrets (for the active decision engine) that are absent or empty."""
    return [k for k in required_keys() if not os.getenv(k)]


def load_secrets():
    """Populate os.environ from SSM Parameter Store (idempotent)."""
    global _loaded
    if _loaded:
        return

    # If every key is explicitly present in the environment, don't hit SSM.
    # Presence (not truthiness) is the test, so an intentionally-empty value
    # (e.g. an unused Discord webhook in tests) still counts as configured.
    if all(k in os.environ for k in _SECRET_KEYS):
        _loaded = True
        return

    try:
        import boto3

        ssm = boto3.client("ssm")
        # get_parameters_by_path pulls the whole prefix in one (paginated) call.
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(
            Path=_SSM_PREFIX, Recursive=True, WithDecryption=True
        ):
            for param in page["Parameters"]:
                name = param["Name"].rsplit("/", 1)[-1]
                # Never override an explicitly-set env var (useful for local/testing).
                if name not in os.environ:
                    os.environ[name] = param["Value"]
    except Exception as e:  # noqa: BLE001 - surface but don't crash import
        print(f"[config] WARNING: could not load secrets from SSM: {e}")

    _loaded = True
