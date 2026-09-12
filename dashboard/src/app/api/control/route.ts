import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side proxy for the bot's protected /start and /stop endpoints.
 *
 * The shared secret stays on the server (API_SHARED_SECRET, NOT a NEXT_PUBLIC_*
 * variable) so it never reaches the browser bundle.
 */
export async function POST(req: NextRequest) {
  const { action } = await req.json().catch(() => ({ action: null }));

  if (action !== "start" && action !== "stop") {
    return NextResponse.json({ error: "action must be 'start' or 'stop'" }, { status: 400 });
  }

  const apiUrl = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
  const secret = process.env.API_SHARED_SECRET;

  if (!secret) {
    return NextResponse.json({ error: "API_SHARED_SECRET is not configured" }, { status: 503 });
  }

  try {
    const res = await fetch(`${apiUrl}/${action}`, {
      method: "POST",
      headers: { "x-api-key": secret },
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Backend not reachable" }, { status: 502 });
  }
}
