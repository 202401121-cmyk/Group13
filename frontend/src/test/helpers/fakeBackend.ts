/** A recorded stand-in for `fetch`, so adapter tests never call a real backend. */
import { createApiClient, type ApiClient } from "@/lib/api/client";

export const BASE = "http://backend.test";

export interface Call {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
}

type Handler = (call: Call, init: RequestInit) => Response | Promise<Response>;

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fetch that never answers, until its request is aborted. */
export const hang: Handler = (_call, init) =>
  new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  });

export function fakeBackend(handler: Handler): { client: ApiClient; calls: Call[] } {
  const calls: Call[] = [];
  const client = createApiClient(BASE, async (url, init) => {
    const call: Call = {
      method: init.method ?? "GET",
      path: url.slice(BASE.length),
      body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
      headers: (init.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    return handler(call, init);
  });
  return { client, calls };
}

/** Answers each request in turn from a list. */
export function scripted(...responses: (Response | Handler)[]): Handler {
  let i = 0;
  return (call, init) => {
    const next = responses[i++];
    if (!next) throw new Error(`no scripted answer for ${call.method} ${call.path}`);
    return next instanceof Response ? next : next(call, init);
  };
}
