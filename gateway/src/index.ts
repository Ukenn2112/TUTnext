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
}

function upstreamFor(env: Env, path: string): Fetcher {
  return path === "/bus" || path.startsWith("/bus/") ? env.BUS : env.API;
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
    // Bodies are small JSON payloads; buffer them so a retry can resend the same bytes.
    const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();
    const path = new URL(request.url).pathname;
    const upstreamWorker = upstreamFor(env, path);

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

    if (result.response) return result.response;

    const timedOut = result.error instanceof DOMException && result.error.name === "TimeoutError";
    console.error(JSON.stringify({ event: timedOut ? "timeout" : "upstream_error", path, error: String(result.error), elapsed: result.elapsed }));
    return timedOut
      ? jsonError(504, "サーバーの応答がタイムアウトしました。しばらくしてから再度お試しください。")
      : jsonError(502, "サーバーで一時的なエラーが発生しました。しばらくしてから再度お試しください。");
  },
} satisfies ExportedHandler<Env>;
