"""DynamoDB data layer (single-table design, on-demand).

Replaces the SQLAlchemy/Postgres models (trades, pipeline_log, system_state).

Table schema
------------
- pk (S): entity type -> "TRADE" | "PIPELINE" | "STATE"
- sk (S): sort key
    - TRADE / PIPELINE : time-sortable id "<UTC ISO>#<rand>" (also used as `id`)
    - STATE            : the state key, e.g. "scheduler_running"

Latest-N reads are a single Query on the partition, descending (ScanIndexForward=False).
Numbers are stored as Decimal (DynamoDB requirement) and converted back to float on read.

Pipeline items keep the pipeline_log column names (opus_output, risk_guard_passed, ...)
so the API and dashboard stay unchanged. `decision_engine` records which model
actually produced the decision, since the "opus_*" fields hold Grok's decision
when DECISION_ENGINE=grok.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.getenv("DDB_TABLE", "grok-trade")

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def _new_sort_id() -> str:
    """Time-sortable unique id: <UTC-ISO-microseconds>#<rand>.

    Lexicographic order == chronological order, so a descending Query
    returns newest-first without a secondary index.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    return f"{now}#{uuid.uuid4().hex[:8]}"


def _to_dynamo(value):
    """Recursively convert floats to Decimal for DynamoDB."""
    if isinstance(value, float):
        # str() avoids binary float artefacts (e.g. 0.1 -> 0.1, not 0.100000000...)
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value):
    """Recursively convert Decimal back to int/float for JSON responses."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def _put(pk: str, fields: dict, label: str):
    sk = _new_sort_id()
    item = {
        "pk": pk,
        "sk": sk,
        "id": sk,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    try:
        _get_table().put_item(Item=_to_dynamo(item))
        print(f"[DB] {label} logged")
    except Exception as e:  # noqa: BLE001 - logging is best-effort
        print(f"[DB] Error logging {label.lower()}: {e}")


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def log_trade(
    cycle_id, symbol, action, quantity, price, order_type, status,
    alpaca_order_id, stop_loss=None, take_profit=None,
):
    _put("TRADE", {
        "cycle_id": cycle_id,
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "price": price,
        "order_type": order_type,
        "status": status,
        "alpaca_order_id": alpaca_order_id,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }, "Trade")


def log_pipeline(
    cycle_id: str,
    decision_engine: str = None,
    grok_input: dict = None,
    grok_output: dict = None,
    grok_latency_ms: int = None,
    opus_skipped: bool = False,
    opus_input: dict = None,
    opus_output: dict = None,
    opus_latency_ms: int = None,
    opus_adjustments: list = None,
    rg_passed: bool = None,
    rg_reason: str = None,
    rg_adjustments: list = None,
    order_submitted: bool = False,
    alpaca_order_id: str = None,
    execution_result: dict = None,
):
    """1判断サイクル = 1アイテム（旧 pipeline_log テーブル相当）"""
    _put("PIPELINE", {
        "cycle_id": cycle_id,
        "decision_engine": decision_engine,
        "grok_input": grok_input,
        "grok_output": grok_output,
        "grok_latency_ms": grok_latency_ms,
        "opus_skipped": opus_skipped,
        "opus_input": opus_input,
        "opus_output": opus_output,
        "opus_latency_ms": opus_latency_ms,
        "opus_adjustments": opus_adjustments or [],
        "risk_guard_passed": rg_passed,
        "risk_guard_reason": rg_reason,
        "risk_guard_adjustments": rg_adjustments or [],
        "order_submitted": order_submitted,
        "alpaca_order_id": alpaca_order_id,
        "execution_result": execution_result,
    }, "Pipeline")


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def _query_latest(pk: str, limit: int):
    resp = _get_table().query(
        KeyConditionExpression=Key("pk").eq(pk),
        ScanIndexForward=False,  # newest first
        Limit=limit,
    )
    return [_from_dynamo(i) for i in resp.get("Items", [])]


def _pick(item: dict, keys: list) -> dict:
    return {k: item.get(k) for k in keys}


TRADE_FIELDS = [
    "id", "timestamp", "cycle_id", "symbol", "action", "quantity", "price",
    "order_type", "status", "alpaca_order_id", "stop_loss", "take_profit",
]

DECISION_FIELDS = [
    "id", "cycle_id", "timestamp", "decision_engine", "opus_skipped", "opus_output",
    "risk_guard_passed", "risk_guard_reason", "order_submitted",
]

PIPELINE_FIELDS = [
    "id", "cycle_id", "timestamp", "decision_engine",
    "grok_output", "grok_latency_ms",
    "opus_skipped", "opus_output", "opus_latency_ms", "opus_adjustments",
    "risk_guard_passed", "risk_guard_reason", "risk_guard_adjustments",
    "order_submitted", "alpaca_order_id", "execution_result",
]


def get_trades(limit: int = 50):
    return [_pick(t, TRADE_FIELDS) for t in _query_latest("TRADE", limit)]


def get_decisions(limit: int = 50):
    """Summary view of each pipeline cycle (the /decisions endpoint)."""
    return [_pick(d, DECISION_FIELDS) for d in _query_latest("PIPELINE", limit)]


def get_pipeline_logs(limit: int = 50):
    """Full view of each pipeline cycle (the /pipeline endpoint)."""
    return [_pick(d, PIPELINE_FIELDS) for d in _query_latest("PIPELINE", limit)]


# --------------------------------------------------------------------------
# System state (key/value)  -- replaces the old scheduler.running flag
# --------------------------------------------------------------------------
def get_scheduler_state() -> bool:
    """Fail closed: a read error means "assume stopped", not "assume running".

    A DynamoDB outage must never be silently read as "the bot is fine, keep
    trading" - that is backwards for a kill switch. The one case that still
    defaults to True is an item that genuinely does not exist yet (first
    deploy, before /stop has ever been called).
    """
    try:
        resp = _get_table().get_item(Key={"pk": "STATE", "sk": "scheduler_running"})
    except Exception as e:  # noqa: BLE001
        print(f"[DB] Error getting scheduler state: {e} - treating as stopped")
        return False
    item = resp.get("Item")
    if item is not None:
        return bool(item.get("running", True))
    return True  # no item yet: default to running


def set_scheduler_state(running: bool) -> None:
    """Raises on failure rather than swallowing it.

    Callers (the /stop /start endpoints, and the daily-loss auto-stop) must
    know when the flag did not actually change, since silently continuing as
    if it had is exactly the "kill switch that doesn't kill" bug this guards
    against.
    """
    _get_table().put_item(
        Item={
            "pk": "STATE",
            "sk": "scheduler_running",
            "running": running,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    print(f"[DB] Scheduler state saved: {running}")
