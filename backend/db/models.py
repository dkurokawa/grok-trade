"""Database models for trade logging"""
import os
from datetime import datetime, date
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Date, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Railway の内部URL を外部接続用に変換（ローカル開発用）
if DATABASE_URL and "railway.internal" in DATABASE_URL:
    # Railway 内部からは変換不要
    pass

engine = create_engine(DATABASE_URL) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine) if engine else None
Base = declarative_base()


class Trade(Base):
    """取引ログ"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String(50))
    action = Column(String(10))  # buy/sell
    quantity = Column(Float)
    price = Column(Float)
    order_type = Column(String(20))
    status = Column(String(20))
    alpaca_order_id = Column(String(100))


class Decision(Base):
    """Grok思考ログ"""
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    market_context = Column(JSON)  # Grokへの入力
    grok_response = Column(Text)  # 生レスポンス
    parsed_action = Column(JSON)  # パース結果
    executed = Column(Boolean, default=False)
    blocked_reason = Column(Text)  # Risk Guardで止めた理由


class DailySummary(Base):
    """日次サマリー"""
    __tablename__ = "daily_summary"

    date = Column(Date, primary_key=True)
    starting_balance = Column(Float)
    ending_balance = Column(Float)
    pnl = Column(Float)
    trade_count = Column(Integer)
    win_rate = Column(Float)


class SystemState(Base):
    """システム状態"""
    __tablename__ = "system_state"

    key = Column(String(50), primary_key=True)
    value = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    """テーブル作成"""
    if engine:
        Base.metadata.create_all(engine)
        print("[DB] Tables created")


def get_session():
    """セッション取得"""
    if SessionLocal:
        return SessionLocal()
    return None
