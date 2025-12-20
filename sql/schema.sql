-- 取引ログ
CREATE TABLE trades (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  symbol VARCHAR(50),
  action VARCHAR(10),  -- buy/sell
  quantity DECIMAL,
  price DECIMAL,
  order_type VARCHAR(20),
  status VARCHAR(20),
  alpaca_order_id VARCHAR(100)
);

-- Grok思考ログ
CREATE TABLE decisions (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  market_context JSONB,    -- Grokへの入力
  grok_response TEXT,      -- 生レスポンス
  parsed_action JSONB,     -- パース結果
  executed BOOLEAN,
  blocked_reason TEXT      -- Risk Guardで止めた理由
);

-- 日次サマリー
CREATE TABLE daily_summary (
  date DATE PRIMARY KEY,
  starting_balance DECIMAL,
  ending_balance DECIMAL,
  pnl DECIMAL,
  trade_count INT,
  win_rate DECIMAL
);

-- システム状態
CREATE TABLE system_state (
  key VARCHAR(50) PRIMARY KEY,
  value JSONB,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX idx_trades_timestamp ON trades(timestamp);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_decisions_timestamp ON decisions(timestamp);
