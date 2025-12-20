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

type MarketStatus = "pre" | "open" | "post";

function getMarketStatus(): { status: MarketStatus; label: string; color: string } {
  const now = new Date();
  const nyTime = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hours = nyTime.getHours();
  const minutes = nyTime.getMinutes();
  const day = nyTime.getDay();
  const time = hours * 60 + minutes;

  // 週末
  if (day === 0 || day === 6) {
    return { status: "post", label: "Weekend - Market Closed", color: "gray" };
  }

  const marketOpen = 9 * 60 + 30;  // 9:30 AM
  const marketClose = 16 * 60;      // 4:00 PM

  if (time < marketOpen) {
    return { status: "pre", label: "Pre-Market", color: "yellow" };
  } else if (time >= marketOpen && time < marketClose) {
    return { status: "open", label: "Market Open", color: "green" };
  } else {
    return { status: "post", label: "After Hours", color: "orange" };
  }
}

function getNextTradeCountdown(intervalSeconds: number): string {
  const now = new Date();
  const seconds = now.getSeconds();
  const minutes = now.getMinutes();
  const totalSeconds = minutes * 60 + seconds;
  const remaining = intervalSeconds - (totalSeconds % intervalSeconds);
  const mins = Math.floor(remaining / 60);
  const secs = remaining % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function getTimeUntilMarketOpen(): string {
  const now = new Date();
  const nyTime = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hours = nyTime.getHours();
  const minutes = nyTime.getMinutes();

  const marketOpenMinutes = 9 * 60 + 30;
  const currentMinutes = hours * 60 + minutes;

  let diffMinutes = marketOpenMinutes - currentMinutes;
  if (diffMinutes < 0) {
    diffMinutes += 24 * 60; // 翌日
  }

  const h = Math.floor(diffMinutes / 60);
  const m = diffMinutes % 60;
  return `${h}h ${m}m`;
}

function getTimeUntilMarketClose(): string {
  const now = new Date();
  const nyTime = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hours = nyTime.getHours();
  const minutes = nyTime.getMinutes();

  const marketCloseMinutes = 16 * 60;
  const currentMinutes = hours * 60 + minutes;

  const diffMinutes = marketCloseMinutes - currentMinutes;
  if (diffMinutes <= 0) return "Closed";

  const h = Math.floor(diffMinutes / 60);
  const m = diffMinutes % 60;
  return `${h}h ${m}m`;
}

export default function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [botRunning, setBotRunning] = useState(true);
  const [countdown, setCountdown] = useState("--:--");
  const [marketInfo, setMarketInfo] = useState(getMarketStatus());
  const [currentTime, setCurrentTime] = useState(new Date());

  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const TRADING_INTERVAL = 900; // 15分

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
    const statusInterval = setInterval(fetchStatus, 30000);
    return () => clearInterval(statusInterval);
  }, []);

  // 1秒ごとにカウントダウン更新
  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown(getNextTradeCountdown(TRADING_INTERVAL));
      setMarketInfo(getMarketStatus());
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-xl">Loading...</div>
      </div>
    );
  }

  const marketColorClass = {
    green: "bg-green-900 text-green-300 border-green-700",
    yellow: "bg-yellow-900 text-yellow-300 border-yellow-700",
    orange: "bg-orange-900 text-orange-300 border-orange-700",
    gray: "bg-gray-700 text-gray-300 border-gray-600",
  }[marketInfo.color];

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

      {/* Market Status & Bot Status */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Market Status */}
        <div className={`p-4 rounded-lg border ${marketColorClass}`}>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm opacity-75">US Market</div>
              <div className="text-xl font-bold">{marketInfo.label}</div>
            </div>
            <div className="text-right">
              <div className="text-sm opacity-75">
                NY: {currentTime.toLocaleString("en-US", {
                  timeZone: "America/New_York",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  hour12: false
                })}
              </div>
              {marketInfo.status === "pre" && (
                <div className="text-sm">Opens in {getTimeUntilMarketOpen()}</div>
              )}
              {marketInfo.status === "open" && (
                <div className="text-sm">Closes in {getTimeUntilMarketClose()}</div>
              )}
            </div>
          </div>
        </div>

        {/* Bot Status & Countdown */}
        <div className={`p-4 rounded-lg border ${
          botRunning ? "bg-green-900 border-green-700" : "bg-red-900 border-red-700"
        }`}>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm opacity-75">Bot Status</div>
              <div className="text-xl font-bold">
                {botRunning ? "● Running" : "○ Stopped"}
              </div>
            </div>
            {botRunning && marketInfo.status === "open" && (
              <div className="text-right">
                <div className="text-sm opacity-75">Next Trade In</div>
                <div className="text-2xl font-mono font-bold">{countdown}</div>
              </div>
            )}
            {botRunning && marketInfo.status !== "open" && (
              <div className="text-right">
                <div className="text-sm opacity-75">Waiting for</div>
                <div className="text-lg font-bold">Market Open</div>
              </div>
            )}
          </div>
        </div>
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
