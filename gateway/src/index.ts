/**
 * tutnext-gateway: forwards API traffic to the Python Workers (`tutnext`, `tutnext-bus`).
 *
 * The Python Worker fails in two characteristic ways (see docs/cloudflare-python-workers.md):
 *  - a cold start that collides with another request throws Pyodide's
 *    "Cannot enter a promising task" within a few hundred ms, before any handler runs;
 *  - a request occasionally hangs until the runtime cancels it (~25 s+).
 * The first is retried once (nothing has executed yet); the second is cut off with a 504.
 *
 * The FastAPI app itself answers many business errors with a JSON 500 (e.g. a wrong school
 * password on GET /schedule). Those are passed through untouched: retrying them would repeat
 * a school-system login, so only thrown errors and 5xx responses without the app's
 * `x-tutnext-app` marker (runtime failures) are retried.
 */

export interface Env {
  API: Fetcher; // tutnext: FastAPI (T-NEXT login, schedule, kadai, push, Live Activity, OAuth)
  BUS: Fetcher; // tutnext-bus: /bus routes (public timetable, temporary-schedule PDFs)
  ASSETS: Fetcher;
  DB: D1Database; // exact per-student counters (table rate_counters, migrations/0003)
}

// Per-student budget on LOGIN_ROUTES per 60 s window. The app makes ~5–7 such calls when it
// opens and bursts of ~20/min happen in normal use; this only bites scripted guessing. Counted exactly in D1 (the
// Workers Rate Limiting binding never enforced on this account in testing, 2026-10-04).
// Per-IP flooding is handled by the zone's WAF rate-limiting rule (docs §9).
const LOGIN_PER_USER_PER_MINUTE = 30; // 12 throttled a real user re-registering Live Activities (2026-10-05)

async function underUserBudget(db: D1Database, id: string): Promise<boolean> {
  try {
    const window = Math.floor(Date.now() / 60_000);
    const row = await db
      .prepare(
        "INSERT INTO rate_counters (key, window, count) VALUES (?1, ?2, 1) " +
          "ON CONFLICT(key) DO UPDATE SET count = CASE WHEN window = excluded.window THEN count + 1 ELSE 1 END, " +
          "window = excluded.window RETURNING count",
      )
      .bind(`login:${id}`, window)
      .first<{ count: number }>();
    return (row?.count ?? 0) <= LOGIN_PER_USER_PER_MINUTE;
  } catch (error) {
    console.error(JSON.stringify({ event: "rate_counter_error", error: String(error) }));
    return true; // a D1 hiccup must not take the API down
  }
}

// Every route the iOS app calls (TUTnextApp: grep AppConstants.backendBaseURL). Anything else,
// e.g. the constant .env/.git/phpinfo scans, is answered here and never wakes a Python isolate.
// Static pages (/, /policy, /user-agreement) are served by Assets before this Worker runs.
const ROUTES: Record<string, "API" | "BUS"> = {
  "GET /schedule": "API",
  "POST /schedule/later": "API",
  "POST /schedule/class_bulletin": "API",
  "POST /kadai": "API",
  "GET /tmail": "API",
  "POST /push/send": "API",
  "POST /push/unregister": "API",
  "POST /oauth/tokens": "API",
  "POST /oauth/revoke": "API",
  "POST /oauth/status": "API",
  "POST /live-activity/register": "API",
  "POST /live-activity/unregister": "API",
  "POST /live-activity/push-to-start": "API",
  "GET /bus/app_data": "BUS",
};

// Routes that make the API log in to T-NEXT with caller-supplied credentials. Without a
// per-student limit anyone could use us to brute-force or lock out a student's school
// account, or get Cloudflare's egress blocked by the school.
const LOGIN_ROUTES = new Set([
  "GET /schedule",
  "POST /schedule/later",
  "POST /schedule/class_bulletin",
  "POST /kadai",
  "POST /push/send",
  "POST /oauth/tokens",
  "POST /oauth/revoke",
  "POST /oauth/status",
  "POST /live-activity/register",
  "POST /live-activity/push-to-start",
]);

const MAX_BODY_BYTES = 16 * 1024; // the app's JSON bodies are < 2 KB

/** Read at most MAX_BODY_BYTES; null when larger (also for chunked bodies without content-length). */
async function readCappedBody(request: Request): Promise<ArrayBuffer | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return null;
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out.buffer;
}

/**
 * Student ID the request acts on, read exactly the way the API reads it (query for GET
 * /schedule, `loginUserId` for class_bulletin, `username` otherwise), or "ambiguous" when
 * the request carries several (FastAPI takes the last query value; keying the limit on
 * another one would let a caller dodge the per-student limit).
 */
function studentId(routeKey: string, url: URL, body: ArrayBuffer | null): string | null | "ambiguous" {
  if (routeKey === "GET /schedule") {
    const all = url.searchParams.getAll("username");
    if (all.length > 1) return "ambiguous";
    return all[0] ? all[0].trim().toLowerCase() : null;
  }
  if (!body || body.byteLength === 0) return null;
  try {
    const json = JSON.parse(new TextDecoder().decode(body)) as Record<string, unknown>;
    const id = routeKey === "POST /schedule/class_bulletin" ? json.loginUserId : json.username;
    return typeof id === "string" && id.trim() ? id.trim().toLowerCase() : null;
  } catch {
    return null;
  }
}

/** Personal data must never sit in a shared cache; also basic hardening headers. */
function withSecurityHeaders(response: Response): Response {
  const out = new Response(response.body, response);
  out.headers.set("Cache-Control", "no-store");
  out.headers.set("X-Content-Type-Options", "nosniff");
  out.headers.set("Strict-Transport-Security", "max-age=31536000");
  out.headers.delete("x-tutnext-app"); // internal marker, only meaningful to this gateway
  return out;
}

function upstreamFor(env: Env, method: string, path: string): Fetcher | null {
  const target = ROUTES[`${method === "HEAD" ? "GET" : method} ${path}`];
  return target ? env[target] : null;
}

// Upstream budget per attempt. School-system scraping normally finishes in < 15 s.
const UPSTREAM_TIMEOUT_MS = 40_000;
// A failure this fast happened during Python start-up, before the route handler ran.
const FAST_FAILURE_MS = 3_000;
const RETRYABLE_STATUS = new Set([500, 502, 503]);

/** A 5xx produced by the Workers runtime, not by the FastAPI app (which stamps x-tutnext-app). */
function isRuntimeFailure(response: Response): boolean {
  return RETRYABLE_STATUS.has(response.status) && !response.headers.has("x-tutnext-app");
}

function jsonError(status: number, message: string): Response {
  return Response.json({ status: false, message }, { status });
}

async function callUpstream(upstreamWorker: Fetcher, request: Request, body: ArrayBuffer | null, attempt: number) {
  const headers = new Headers(request.headers);
  headers.set("x-tutnext-gateway-attempt", String(attempt));
  const upstream = new Request(request.url, { method: request.method, headers, body, redirect: "manual" });
  const started = Date.now();
  try {
    const response = await upstreamWorker.fetch(upstream, { signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS) });
    return { response, error: null as unknown, elapsed: Date.now() - started };
  } catch (error) {
    return { response: null, error, elapsed: Date.now() - started };
  }
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    const upstreamWorker = upstreamFor(env, request.method, path);
    if (!upstreamWorker) return withSecurityHeaders(jsonError(404, "Not Found"));

    // Bodies are small JSON payloads; buffer them (capped) so a retry can resend the same bytes.
    const body = request.method === "GET" || request.method === "HEAD" ? null : await readCappedBody(request);
    if (body === null && request.method !== "GET" && request.method !== "HEAD") {
      return withSecurityHeaders(jsonError(413, "Payload Too Large"));
    }

    const routeKey = `${request.method === "HEAD" ? "GET" : request.method} ${path}`;
    if (LOGIN_ROUTES.has(routeKey)) {
      const id = studentId(routeKey, url, body);
      if (id === "ambiguous") return withSecurityHeaders(jsonError(400, "Bad Request"));
      if (id !== null && !(await underUserBudget(env.DB, id))) {
        console.warn(JSON.stringify({ event: "rate_limited", route: routeKey, user: id }));
        return withSecurityHeaders(jsonError(429, "リクエストが多すぎます。しばらくしてから再度お試しください。"));
      }
    }

    let result = await callUpstream(upstreamWorker, request, body, 1);
    const fastFailure =
      result.elapsed < FAST_FAILURE_MS &&
      (result.error !== null || (result.response !== null && isRuntimeFailure(result.response)));
    if (fastFailure) {
      console.warn(
        JSON.stringify({ event: "retry", path, status: result.response?.status ?? null, error: String(result.error ?? ""), elapsed: result.elapsed }),
      );
      await result.response?.body?.cancel();
      result = await callUpstream(upstreamWorker, request, body, 2);
    }

    if (result.response) return withSecurityHeaders(result.response);

    const timedOut = result.error instanceof DOMException && result.error.name === "TimeoutError";
    console.error(JSON.stringify({ event: timedOut ? "timeout" : "upstream_error", path, error: String(result.error), elapsed: result.elapsed }));
    return withSecurityHeaders(timedOut
      ? jsonError(504, "サーバーの応答がタイムアウトしました。しばらくしてから再度お試しください。")
      : jsonError(502, "サーバーで一時的なエラーが発生しました。しばらくしてから再度お試しください。"));
  },
} satisfies ExportedHandler<Env>;
