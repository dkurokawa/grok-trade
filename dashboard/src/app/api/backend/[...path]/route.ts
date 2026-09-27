import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side proxy for the bot's read-only endpoints.
 *
 * The browser never talks to the backend directly (see also ../control/route.ts
 * for the mutating /start /stop endpoints). Both the backend URL (API_URL) and
 * the shared secret (API_SHARED_SECRET) are server-only variables (no
 * NEXT_PUBLIC_ prefix), so neither reaches the browser bundle. Only a
 * fixed allow-list of GET paths is forwarded.
 */
const ALLOWED_PATHS = new Set(["status", "trades", "decisions", "pipeline"]);

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  const target = params.path.join("/");

  if (params.path.length !== 1 || !ALLOWED_PATHS.has(target)) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }

  const apiUrl = (process.env.API_URL || "http://localhost:8000").replace(/\/$/, "");
  const secret = process.env.API_SHARED_SECRET;

  if (!secret) {
    return NextResponse.json({ error: "API_SHARED_SECRET is not configured" }, { status: 503 });
  }

  try {
    const res = await fetch(`${apiUrl}/${target}${req.nextUrl.search}`, {
      headers: { "x-api-key": secret },
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Backend not reachable" }, { status: 502 });
  }
}
