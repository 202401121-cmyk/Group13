import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, MESSAGES, TIMEOUTS, apiConfig } from "@/lib/api/client";
import { fakeBackend, hang, json } from "./helpers/fakeBackend";
import { fakeBearer } from "./helpers/fakeAuth";

afterEach(() => {
  vi.useRealTimers();
});

describe("settings", () => {
  it("defaults to mock mode and the local backend", () => {
    expect(apiConfig({})).toEqual({ mode: "mock", baseUrl: "http://localhost:8000" });
  });
  it("only an explicit 'live' calls the backend; a typo stays on mock", () => {
    expect(apiConfig({ VITE_API_MODE: " Live " }).mode).toBe("live");
    expect(apiConfig({ VITE_API_MODE: "lvie" }).mode).toBe("mock");
  });
  it("drops a trailing slash from the base URL", () => {
    expect(apiConfig({ VITE_API_BASE_URL: "https://api.example.org/" }).baseUrl).toBe(
      "https://api.example.org",
    );
  });
  it("keeps the existing time limits", () => {
    expect(TIMEOUTS).toEqual({ step: 12_000, research: 170_000 });
  });
});

describe("requests", () => {
  it("sends JSON with the right method and returns the parsed answer", async () => {
    const { client, calls } = fakeBackend(() => json(200, { ok: true }));
    await expect(client.post("/api/x", { a: 1 }, { timeoutMs: 1000 })).resolves.toEqual({
      ok: true,
    });
    expect(calls[0]).toMatchObject({
      method: "POST",
      path: "/api/x",
      body: { a: 1 },
      headers: { "Content-Type": "application/json" },
    });
  });
  it("sends no body and no Content-Type when there is nothing to send", async () => {
    const { client, calls } = fakeBackend(() => json(200, {}));
    await client.post("/api/x", undefined, { timeoutMs: 1000 });
    expect(calls[0]?.body).toBeUndefined();
    expect(calls[0]?.headers).toEqual({});
  });
});

describe("errors become plain messages", () => {
  const cases: [number, unknown, ApiError["kind"], string][] = [
    [
      404,
      { detail: "This session has expired. Please start a new search." },
      "expired",
      "This session has expired. Please start a new search.",
    ],
    [404, { detail: "Not Found" }, "expired", MESSAGES.expired],
    [
      409,
      { detail: "Tell us what kind of product first." },
      "conflict",
      "Tell us what kind of product first.",
    ],
    [
      422,
      { detail: "Please check: text - String should have at most 1000 characters." },
      "invalid",
      "Please check: text - String should have at most 1000 characters.",
    ],
    [500, { detail: "Traceback (most recent call last) ..." }, "server", MESSAGES.server],
    [503, "not json", "server", MESSAGES.server],
    [
      503,
      { detail: "ShopSense is busy. Please try again." },
      "server",
      "ShopSense is busy. Please try again.",
    ],
  ];
  for (const [status, body, kind, message] of cases) {
    it(`${status} -> ${kind}`, async () => {
      const { client } = fakeBackend(() => json(status, body));
      const err = await client.get("/api/x", { timeoutMs: 1000 }).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err).toMatchObject({ kind, message, status });
    });
  }

  it("a backend that can't be reached is a network error", async () => {
    const { client } = fakeBackend(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(client.get("/api/x", { timeoutMs: 1000 })).rejects.toMatchObject({
      kind: "network",
      message: MESSAGES.network,
    });
  });

  it("an answer that isn't JSON is rejected", async () => {
    const { client } = fakeBackend(() => new Response("<html>", { status: 200 }));
    await expect(client.get("/api/x", { timeoutMs: 1000 })).rejects.toMatchObject({
      kind: "bad_response",
    });
  });
});

describe("an answer cut off halfway", () => {
  it("is a lost connection, not an unreadable answer", async () => {
    const cut = () =>
      ({
        ok: true,
        status: 200,
        json: () => Promise.reject(new TypeError("network error")),
      }) as Response;
    const { client } = fakeBackend(cut);
    await expect(client.get("/api/x", { timeoutMs: 1000 })).rejects.toMatchObject({
      kind: "network",
      message: MESSAGES.network,
    });
  });

  it("an answer that isn't JSON is still an unreadable one", async () => {
    const { client } = fakeBackend(() => new Response("<html>", { status: 200 }));
    await expect(client.get("/api/x", { timeoutMs: 1000 })).rejects.toMatchObject({
      kind: "bad_response",
    });
  });
});

describe("an answer that stalls", () => {
  it("is ended by the time limit, even after its headers have come", async () => {
    const stalled = (init: RequestInit) =>
      ({
        ok: true,
        status: 200,
        json: () =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      }) as Response;
    const { client } = fakeBackend((_call, init) => stalled(init));
    await expect(client.get("/api/x", { timeoutMs: 20 })).rejects.toMatchObject({
      kind: "timeout",
      message: MESSAGES.timeout,
    });
  });
});

describe("time limits", () => {
  it("gives up after the step limit", async () => {
    vi.useFakeTimers();
    const { client } = fakeBackend(hang);
    const pending = client
      .post("/api/x", {}, { timeoutMs: TIMEOUTS.step })
      .catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(TIMEOUTS.step - 1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toMatchObject({ kind: "timeout", message: MESSAGES.timeout });
  });

  it("gives up after the research limit", async () => {
    vi.useFakeTimers();
    const { client } = fakeBackend(hang);
    const pending = client
      .post("/api/search", {}, { timeoutMs: TIMEOUTS.research })
      .catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(TIMEOUTS.research);
    await expect(pending).resolves.toMatchObject({ kind: "timeout" });
  });

  it("a cancel from the caller is passed through, not turned into an error message", async () => {
    const { client } = fakeBackend(hang);
    const stop = new AbortController();
    const pending = client.get("/api/x", { timeoutMs: 10_000, signal: stop.signal });
    stop.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("PATCH, a DELETE with a body, and 204 (US-11)", () => {
  it("sends a PATCH with its JSON and gives back the answer's headers", async () => {
    const { client, calls } = fakeBackend(
      () =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json", "X-Memory-Note": "a note" },
        }),
    );
    const { data, headers } = await client.patch("/api/x/1", { weight: 6 }, { timeoutMs: 1000 });
    expect(data).toEqual({ ok: true });
    expect(headers.get("X-Memory-Note")).toBe("a note");
    expect(calls[0]).toMatchObject({ method: "PATCH", path: "/api/x/1", body: { weight: 6 } });
    expect(calls[0]!.headers["Content-Type"]).toBe("application/json");
  });

  it("sends a DELETE's body only when the route needs one", async () => {
    const { client, calls } = fakeBackend(() => new Response(null, { status: 204 }));
    await client.delete("/api/me", { timeoutMs: 1000 }, { confirm: "delete" });
    await client.delete("/api/x/1", { timeoutMs: 1000 });
    expect(calls[0]).toMatchObject({ method: "DELETE", body: { confirm: "delete" } });
    expect(calls[0]!.headers["Content-Type"]).toBe("application/json");
    expect(calls[1]!.body).toBeUndefined();
    expect(calls[1]!.headers["Content-Type"]).toBeUndefined();
  });

  it("takes 204 with no body as done, with no data", async () => {
    const { client } = fakeBackend(() => new Response(null, { status: 204 }));
    await expect(client.delete("/api/x/1", { timeoutMs: 1000 })).resolves.toEqual({
      status: 204,
      data: undefined,
    });
  });

  it("a token read earlier is sent at once, without asking the sign-in again, and keepalive goes with it", async () => {
    const { bearer, counts } = fakeBearer("now");
    let keepalive: boolean | undefined;
    const { client, calls } = fakeBackend((_call, init) => {
      keepalive = init.keepalive;
      return new Response(null, { status: 204 });
    }, bearer);
    const pending = client.delete("/api/x/1", {
      timeoutMs: 1000,
      token: "earlier",
      keepalive: true,
    });
    expect(calls).toHaveLength(1); // sent before anything was awaited
    await pending;
    expect(calls[0]!.headers["Authorization"]).toBe("Bearer earlier");
    expect(keepalive).toBe(true);
    expect(counts.token).toBe(0);
  });

  it("still refuses a 200 with no body", async () => {
    const { client } = fakeBackend(() => new Response(null, { status: 200 }));
    await expect(client.delete("/api/x/1", { timeoutMs: 1000 })).rejects.toMatchObject({
      kind: "bad_response",
    });
  });
});
