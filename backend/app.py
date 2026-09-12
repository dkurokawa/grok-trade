"""API Lambda: dashboard-facing HTTP API (FastAPI wrapped by Mangum).

Served via a Lambda Function URL. Routes match the old Fly service except that
scheduler state and logs now live in DynamoDB, and /start /stop require a
shared secret (previously anyone could stop the bot).
"""
import os
from datetime import datetime
from typing import Optional

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from config import load_secrets

load_secrets()  # populate os.environ from SSM before Sentry and the clients

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    environment=os.environ.get("ENVIRONMENT", "development"),
    traces_sample_rate=0.1,
    integrations=[FastApiIntegration()],
)

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from mangum import Mangum  # noqa: E402

from config import decision_engine, missing_required, secrets_status  # noqa: E402
from db import (  # noqa: E402
    get_decisions,
    get_pipeline_logs,
    get_scheduler_state,
    get_trades,
    set_scheduler_state,
)
from discord_notifier import DiscordNotifier  # noqa: E402
from trader import Trader  # noqa: E402

# Clients are built on first use, not at import. Alpaca raises if its keys are
# missing, and constructing at import would take the whole API down with it -
# including /health, the one endpoint needed to diagnose that very problem.
trader = None
notifier = None


def _get_trader():
    global trader
    if trader is None:
        trader = Trader()
    return trader


def _get_notifier():
    global notifier
    if notifier is None:
        notifier = DiscordNotifier()
    return notifier


# DynamoDB rejects Limit < 1; keep the old "any int is accepted" behaviour.
MAX_LIMIT = 1000

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番では Dashboard の URL に限定推奨
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_secret(x_api_key: Optional[str]):
    """Guard mutating endpoints with a shared secret header.

    The secret is read per request rather than snapshotted at import: if the
    cold-start SSM fetch failed, caching the empty value would wedge /stop -
    the kill switch - at 503 for the whole life of that container.
    """
    secret = os.getenv("API_SHARED_SECRET", "")
    if not secret:
        load_secrets()  # retry SSM; a no-op once it has succeeded
        secret = os.getenv("API_SHARED_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="API secret not configured")
    if x_api_key != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


@app.get("/health")
async def health():
    missing = missing_required()
    return {
        # "degraded" means the API is up but the bot cannot trade until the
        # listed secrets are registered in SSM.
        "status": "ok" if not missing else "degraded",
        "scheduler_running": get_scheduler_state(),
        "timestamp": datetime.now().isoformat(),
        "decision_engine": decision_engine(),
        "secrets": secrets_status(),
        "missing_secrets": missing,
    }


@app.get("/status")
async def status():
    missing = [k for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY") if not os.getenv(k)]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing secrets in SSM: {', '.join(missing)}",
        )
    t = _get_trader()
    return {
        "account": t.get_account(),
        "positions": t.get_positions(),
        "scheduler_running": get_scheduler_state(),
    }


@app.post("/stop")
async def stop(x_api_key: Optional[str] = Header(default=None)):
    """緊急停止（要シークレット）"""
    _require_secret(x_api_key)
    set_scheduler_state(False)
    await _get_notifier().notify_system_stop("Manual stop via API")
    return {"status": "stopped"}


@app.post("/start")
async def start(x_api_key: Optional[str] = Header(default=None)):
    """再開（要シークレット）"""
    _require_secret(x_api_key)
    set_scheduler_state(True)
    await _get_notifier().notify_alert("Bot resumed", "info")
    return {"status": "running"}


@app.get("/trades")
async def trades(limit: int = 50):
    """取引履歴取得"""
    try:
        return {"trades": get_trades(_clamp(limit))}
    except Exception as e:
        return {"trades": [], "error": str(e)}


@app.get("/decisions")
async def decisions(limit: int = 50):
    """パイプラインの判断サマリー"""
    try:
        return {"decisions": get_decisions(_clamp(limit))}
    except Exception as e:
        return {"decisions": [], "error": str(e)}


@app.get("/pipeline")
async def pipeline(limit: int = 50):
    """パイプライン全体ログ取得"""
    try:
        return {"logs": get_pipeline_logs(_clamp(limit))}
    except Exception as e:
        return {"logs": [], "error": str(e)}


# Lambda entrypoint
handler = Mangum(app)
