"""Database models for trade logging - v2 with pipeline_log"""
import os
from datetime import datetime, date
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Date, Text, JSON,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    print(f"[DB] DATABASE_URL is set (length: {len(DATABASE_URL)}, starts with: {DATABASE_URL[:20]}...)")
else:
    print("[DB] WARNING: DATABASE_URL is not set!")

# Railway/Fly PostgreSQL uses postgres:// but SQLAlchemy requires postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    print("[DB] Converted postgres:// to postgresql://")

engine = None
SessionLocal = None

if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL)
        SessionLocal = sessionmaker(bind=engine)
        print("[DB] Engine created successfully")
    except Exception as e:
        print(f"[DB] ERROR creating engine: {e}")

Base = declarative_base()


# ========================
# Existing tables (maintained for backward compatibility)
# ========================

class Trade(Base):
    """取引ログ"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String(50))
    action = Column(String(10))
    quantity = Column(Float)
    price = Column(Float)
    order_type = Column(String(20))
    status = Column(String(20))
    alpaca_order_id = Column(String(100))
    # v2 additions
    cycle_id = Column(String(36), nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)


class Decision(Base):
    """Grok思考ログ（レガシー、pipeline_log で置換）"""
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    market_context = Column(JSON)
    grok_response = Column(Text)
    parsed_action = Column(JSON)
    executed = Column(Boolean, default=False)
    blocked_reason = Column(Text)


class DailySummary(Base):
    """日次サマリー"""
    __tablename__ = "daily_summary"

    date = Column(Date, primary_key=True)
    starting_balance = Column(Float)
    ending_balance = Column(Float)
    pnl = Column(Float)
    trade_count = Column(Integer)
    win_rate = Column(Float)
    # v2 additions
    opus_calls = Column(Integer, default=0)
    opus_skips = Column(Integer, default=0)
    risk_guard_blocks = Column(Integer, default=0)
    api_cost_estimate = Column(Float, default=0)


class SystemState(Base):
    """システム状態"""
    __tablename__ = "system_state"

    key = Column(String(50), primary_key=True)
    value = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ========================
# New: Pipeline Log (v2)
# ========================

class PipelineLog(Base):
    """パイプライン全体ログ（1判断サイクル = 1行）"""
    __tablename__ = "pipeline_log"

    id = Column(Integer, primary_key=True)
    cycle_id = Column(String(36), unique=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Stage 1: Grok
    grok_input = Column(JSON, nullable=True)
    grok_output = Column(JSON, nullable=True)
    grok_latency_ms = Column(Integer, nullable=True)
    opus_skipped = Column(Boolean, default=False)

    # Stage 2: Opus
    opus_input = Column(JSON, nullable=True)
    opus_output = Column(JSON, nullable=True)
    opus_latency_ms = Column(Integer, nullable=True)
    opus_adjustments = Column(JSON, default=[])

    # Stage 3: Risk Guard
    risk_guard_passed = Column(Boolean, nullable=True)
    risk_guard_reason = Column(Text, nullable=True)
    risk_guard_adjustments = Column(JSON, default=[])

    # Stage 4: Execution
    order_submitted = Column(Boolean, default=False)
    alpaca_order_id = Column(String(100), nullable=True)
    execution_result = Column(JSON, nullable=True)


def init_db():
    """テーブル作成"""
    if engine:
        try:
            Base.metadata.create_all(engine)
            print("[DB] Tables created successfully")
        except Exception as e:
            print(f"[DB] ERROR creating tables: {e}")
    else:
        print("[DB] WARNING: Cannot initialize DB - engine is None")


def get_session():
    """セッション取得"""
    if SessionLocal:
        return SessionLocal()
    return None
