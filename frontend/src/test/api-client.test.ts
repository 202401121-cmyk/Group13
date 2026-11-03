import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, MESSAGES, TIMEOUTS, apiConfig } from "@/lib/api/client";
import { fakeBackend, hang, json } from "./helpers/fakeBackend";

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
