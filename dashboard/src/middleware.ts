import { NextRequest, NextResponse } from "next/server";

/**
 * HTTP Basic auth in front of the whole dashboard (pages and /api/*).
 *
 * The backend itself only trusts a shared secret that the dashboard's server
 * routes attach (see api/control and api/backend); nothing stops an outsider
 * from loading the dashboard UI itself unless this gate exists. Fails closed:
 * if either credential is unset, every request is rejected rather than left
 * open.
 */

function timingSafeEqual(a: string, b: string): boolean {
  const aBytes = new TextEncoder().encode(a);
  const bBytes = new TextEncoder().encode(b);
  const length = Math.max(aBytes.length, bBytes.length);
  let diff = aBytes.length ^ bBytes.length;
  for (let i = 0; i < length; i++) {
    diff |= (aBytes[i] ?? 0) ^ (bBytes[i] ?? 0);
  }
  return diff === 0;
}

export function middleware(req: NextRequest) {
  const user = process.env.DASHBOARD_USER;
  const password = process.env.DASHBOARD_PASSWORD;

  if (!user || !password) {
    return new NextResponse("Dashboard auth is not configured", { status: 503 });
  }

  const authHeader = req.headers.get("authorization");
  if (authHeader?.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(authHeader.slice("Basic ".length));
    } catch {
      decoded = "";
    }
    const sep = decoded.indexOf(":");
    const suppliedUser = sep === -1 ? decoded : decoded.slice(0, sep);
    const suppliedPassword = sep === -1 ? "" : decoded.slice(sep + 1);

    if (timingSafeEqual(suppliedUser, user) && timingSafeEqual(suppliedPassword, password)) {
      return NextResponse.next();
    }
  }

  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Grok Trade Dashboard"' },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
