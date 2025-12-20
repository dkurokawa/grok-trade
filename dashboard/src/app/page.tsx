"use client";

import { useState, useEffect } from "react";

interface Account {
  cash: number;
  portfolio_value: number;
  buying_power: number;
  equity: number;
  daily_pnl: number;
}

interface Position {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
}

interface Status {
  account: Account;
  positions: Position[];
  scheduler_running: boolean;
}

export default function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [botRunning, setBotRunning] = useState(true);

  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/status`);
      if (!res.ok) throw new Error("Failed to fetch status");
      const data = await res.json();
      setStatus(data);
      setBotRunning(data.scheduler_running);
      setError(null);
    } catch (e) {
      setError("Backend not reachable");
    } finally {
      setLoading(false);
    }
  };

  const toggleBot = async () => {
    try {
      const endpoint = botRunning ? "/stop" : "/start";
      await fetch(`${API_URL}${endpoint}`, { method: "POST" });
      setBotRunning(!botRunning);
    } catch (e) {
      setError("Failed to toggle bot");
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000); // 30秒ごと更新
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-xl">Loading...</div>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold">🤖 Grok Trade Dashboard</h1>
        <button
          onClick={toggleBot}
          className={`px-6 py-3 rounded-lg font-bold text-lg transition-colors ${
            botRunning
              ? "bg-red-600 hover:bg-red-700"
              : "bg-green-600 hover:bg-green-700"
          }`}
        >
          {botRunning ? "🛑 STOP BOT" : "▶️ START BOT"}
        </button>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-900 border border-red-700 p-4 rounded-lg mb-6">
          ⚠️ {error}
        </div>
      )}

      {/* Status Badge */}
      <div className="mb-6">
        <span
          className={`px-4 py-2 rounded-full text-sm font-medium ${
            botRunning
              ? "bg-green-900 text-green-300"
              : "bg-red-900 text-red-300"
          }`}
        >
          {botRunning ? "● Bot Running" : "○ Bot Stopped"}
        </span>
      </div>

      {status && (
        <>
          {/* Account Summary */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
            <Card
              title="Portfolio Value"
              value={`$${status.account.portfolio_value.toLocaleString()}`}
            />
            <Card
              title="Cash"
              value={`$${status.account.cash.toLocaleString()}`}
            />
            <Card
              title="Daily P&L"
              value={`$${status.account.daily_pnl >= 0 ? "+" : ""}${status.account.daily_pnl.toLocaleString()}`}
              color={status.account.daily_pnl >= 0 ? "green" : "red"}
            />
            <Card
              title="Buying Power"
              value={`$${status.account.buying_power.toLocaleString()}`}
            />
          </div>

          {/* Positions */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-bold mb-4">📊 Positions</h2>
            {status.positions.length === 0 ? (
              <p className="text-gray-400">No open positions</p>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="text-left text-gray-400 border-b border-gray-700">
                    <th className="pb-2">Symbol</th>
                    <th className="pb-2">Qty</th>
                    <th className="pb-2">Avg Price</th>
                    <th className="pb-2">Market Value</th>
                    <th className="pb-2">P&L</th>
                    <th className="pb-2">%</th>
                  </tr>
                </thead>
                <tbody>
                  {status.positions.map((pos) => (
                    <tr key={pos.symbol} className="border-b border-gray-700">
                      <td className="py-3 font-bold">{pos.symbol}</td>
                      <td className="py-3">{pos.qty}</td>
                      <td className="py-3">${pos.avg_entry_price.toFixed(2)}</td>
                      <td className="py-3">${pos.market_value.toLocaleString()}</td>
                      <td
                        className={`py-3 ${
                          pos.unrealized_pl >= 0 ? "text-green-400" : "text-red-400"
                        }`}
                      >
                        ${pos.unrealized_pl >= 0 ? "+" : ""}
                        {pos.unrealized_pl.toFixed(2)}
                      </td>
                      <td
                        className={`py-3 ${
                          pos.unrealized_plpc >= 0 ? "text-green-400" : "text-red-400"
                        }`}
                      >
                        {pos.unrealized_plpc >= 0 ? "+" : ""}
                        {(pos.unrealized_plpc * 100).toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {/* Footer */}
      <div className="mt-8 text-center text-gray-500 text-sm">
        Last updated: {new Date().toLocaleString()} | Auto-refresh: 30s
      </div>
    </div>
  );
}

function Card({
  title,
  value,
  color = "white",
}: {
  title: string;
  value: string;
  color?: "white" | "green" | "red";
}) {
  const colorClass = {
    white: "text-white",
    green: "text-green-400",
    red: "text-red-400",
  }[color];

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="text-gray-400 text-sm mb-1">{title}</div>
      <div className={`text-2xl font-bold ${colorClass}`}>{value}</div>
    </div>
  );
}
