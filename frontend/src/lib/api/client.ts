/**
 * The one way the frontend talks to the ShopSense backend (backend/app/main.py, docs/api/presearch.md).
 *
 * - VITE_API_MODE: "mock" (the default) keeps every screen on its mock service; "live" calls the backend.
 * - VITE_API_BASE_URL: where the backend runs, default http://localhost:8000.
 *
 * Every call has a time limit and can be cancelled. Every failure becomes an ApiError whose message is a
 * plain sentence the screen can show as it is: the backend's own `detail` for a 4xx, otherwise ours.
 * Responses come back as `unknown`; the adapters check them against the contract (./schemas.ts) before use.
 */
import { REQUEST_TIMEOUT_MS } from "@/lib/interview/types";
import { RESEARCH_TIMEOUT_MS } from "@/lib/results/types";

export type ApiMode = "mock" | "live";

export interface ApiConfig {
  mode: ApiMode;
  baseUrl: string;
}

export const DEFAULT_BASE_URL = "http://localhost:8000";

/** Time limits, in ms: one interview turn (also intake and criteria), and one whole search. */
export const TIMEOUTS = { step: REQUEST_TIMEOUT_MS, research: RESEARCH_TIMEOUT_MS } as const;

interface ApiEnv {
  VITE_API_MODE?: string | undefined;
  VITE_API_BASE_URL?: string | undefined;
}

/** Reads the two settings. Anything but "live" means mock, so a typo never calls a backend by accident. */
export function apiConfig(env: ApiEnv = import.meta.env): ApiConfig {
  const mode: ApiMode = env.VITE_API_MODE?.trim().toLowerCase() === "live" ? "live" : "mock";
  const baseUrl = (env.VITE_API_BASE_URL?.trim() || DEFAULT_BASE_URL).replace(/\/+$/, "");
  return { mode, baseUrl };
}

export type ApiErrorKind =
  | "network" // the backend could not be reached
  | "timeout" // no answer within the time limit
  | "expired" // 404: the session has expired (or never existed)
  | "conflict" // 409: a step was called out of order
  | "invalid" // 422: the request or an edit was not valid
  | "server" // 5xx
  | "bad_response"; // the answer did not match the contract

export const MESSAGES = {
  network: "We couldn't reach ShopSense. Check your connection and try again.",
  timeout: "This is taking longer than expected. Please try again.",
  expired: "This session has expired. Please start a new search.",
  server: "Something went wrong on our side. Please try again.",
  badResponse: "ShopSense sent back something we couldn't read. Please try again.",
  other: "Something went wrong. Please try again.",
} as const;

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    message: string,
    public readonly status: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface CallOptions {
  timeoutMs: number;
  signal?: AbortSignal | undefined;
}

export interface ApiClient {
  get(path: string, opts: CallOptions): Promise<unknown>;
  post(path: string, body: unknown, opts: CallOptions): Promise<unknown>;
  put(path: string, body: unknown, opts: CallOptions): Promise<unknown>;
  /** A POST whose answer's headers matter too, such as the rubric route's X-Rubric-Note (US-09). */
  postWithHeaders(
    path: string,
    body: unknown,
    opts: CallOptions,
  ): Promise<{ data: unknown; headers: Headers }>;
  /** Gives the status too: for a run, 202 and 200 mean different things. */
  delete(path: string, opts: CallOptions): Promise<{ status: number; data: unknown }>;
}

type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export function createApiClient(
  baseUrl: string,
  fetchImpl: FetchLike = (i, init) => fetch(i, init),
): ApiClient {
  async function call(
    method: string,
    path: string,
    body: unknown,
    opts: CallOptions,
  ): Promise<{ status: number; data: unknown; headers: Headers }> {
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), opts.timeoutMs);
    const onAbort = () => timeout.abort();
    opts.signal?.addEventListener("abort", onAbort);
    let response: Response;
    let data: unknown;
    try {
      response = await fetchImpl(baseUrl + path, {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: timeout.signal,
      });
      // The limit covers the body too: an answer that stalls halfway must not leave a screen waiting for ever.
      data = await readJson(response);
    } catch (e) {
      if (opts.signal?.aborted) throw e; // the caller cancelled: let them see their own abort
      if (timeout.signal.aborted) throw new ApiError("timeout", MESSAGES.timeout);
      throw new ApiError("network", MESSAGES.network);
    } finally {
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
    }
    if (!response.ok) throw errorFor(response.status, data);
    if (data === undefined)
      throw new ApiError("bad_response", MESSAGES.badResponse, response.status);
    return { status: response.status, data, headers: response.headers };
  }
  const dataOf = async (answer: Promise<{ data: unknown }>) => (await answer).data;
  return {
    get: (path, opts) => dataOf(call("GET", path, undefined, opts)),
    post: (path, body, opts) => dataOf(call("POST", path, body, opts)),
    put: (path, body, opts) => dataOf(call("PUT", path, body, opts)),
    postWithHeaders: async (path, body, opts) => {
      const { data, headers } = await call("POST", path, body, opts);
      return { data, headers };
    },
    delete: async (path, opts) => {
      const { status, data } = await call("DELETE", path, undefined, opts);
      return { status, data };
    },
  };
}

/** The body, or undefined if it isn't JSON. A connection that fails while it is read isn't a bad answer. */
async function readJson(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch (e) {
    if (e instanceof Error && e.name === "SyntaxError") return undefined;
    throw e;
  }
}

/** The backend's errors are always {"detail": "<one plain sentence>"} (docs/api/presearch.md). */
function detailOf(data: unknown): string | null {
  if (typeof data !== "object" || data === null || !("detail" in data)) return null;
  const detail = (data as { detail: unknown }).detail;
  return typeof detail === "string" && detail.length > 0 && detail.length <= 300 ? detail : null;
}

function errorFor(status: number, data: unknown): ApiError {
  const detail = detailOf(data);
  if (status === 404) {
    // FastAPI answers an unknown route with "Not Found"; only the session routes send a sentence.
    return new ApiError(
      "expired",
      detail && detail !== "Not Found" ? detail : MESSAGES.expired,
      status,
    );
  }
  if (status === 409) return new ApiError("conflict", detail ?? MESSAGES.other, status);
  if (status === 422) return new ApiError("invalid", detail ?? MESSAGES.other, status);
  // A 503 is the backend saying it is full, in a sentence written for the shopper. Other 5xx bodies aren't.
  if (status === 503 && detail) return new ApiError("server", detail, status);
  if (status >= 500) return new ApiError("server", MESSAGES.server, status);
  return new ApiError("invalid", detail ?? MESSAGES.other, status);
}

/** A session ID as the contracts and the database define it (SessionId, public.requests). */
export const SESSION_ID_PATTERN = /^sess_[A-Za-z0-9]{6,40}$/;

/** A search run's ID as the contracts define it (SearchRunId). */
export const SEARCH_ID_PATTERN = /^srch_[a-f0-9]{24}$/;
