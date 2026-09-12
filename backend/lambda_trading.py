"""Lambda entrypoint for the scheduled jobs.

EventBridge Scheduler invokes this function with {"task": ...}:
  - "trading_cycle"   : 4-stage pipeline, every 30 min during market hours
  - "emergency_check" : drawdown monitor, every 5 min during market hours

Secrets are loaded from SSM before Sentry or any API client is initialised.
"""
import asyncio
import os

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

from config import load_secrets

load_secrets()  # must run before Sentry (SENTRY_DSN) and trading_core's clients

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    environment=os.environ.get("ENVIRONMENT", "development"),
    traces_sample_rate=0.1,
    integrations=[AwsLambdaIntegration()],
)

from trading_core import emergency_check, trading_cycle  # noqa: E402,F401

TASKS = ("trading_cycle", "emergency_check")


def handler(event, context):
    task = (event or {}).get("task", "trading_cycle")
    if task not in TASKS:
        raise ValueError(f"Unknown task: {task!r} (expected one of {sorted(TASKS)})")
    # Resolved at call time rather than bound at import, so the job actually
    # invoked is the module attribute (patchable in tests, and re-imported
    # cleanly on a warm container).
    asyncio.run(globals()[task]())
    return {"ok": True, "task": task}
