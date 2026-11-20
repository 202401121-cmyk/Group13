/**
 * S4-10 / US-30: a signed-in shopper's run is read with fetch and the Authorization header, because EventSource can't
 * send one. The stream parses Server-Sent Events as EventSource does, retries a 404 once with a refreshed token, and
 * says "error" with readyState CLOSED when the connection ends, so the adapter reads the run, as it does for an
 * EventSource that gave up.
 */
import { describe, expect, it } from "vitest";
import { CLOSED, FetchEventStream, OPEN } from "./fetchStream";
import { fakeBearer } from "@/test/helpers/fakeAuth";

const URL_ = "http://backend.test/api/search/runs/srch_" + "a".repeat(24) + "/events";

function sse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

interface Seen {
  type: string;
  data?: unknown;
}

function listen(stream: FetchEventStream): { seen: Seen[]; ended: Promise<void> } {
  const seen: Seen[] = [];
  let done: () => void = () => undefined;
  const ended = new Promise<void>((resolve) => (done = resolve));
  for (const type of ["progress", "run", "message"]) {
    stream.addEventListener(type, (e) => seen.push({ type, data: e.data }));
  }
  stream.addEventListener("error", () => {
    seen.push({ type: "error" });
    done();
  });
  return { seen, ended };
}

describe("a run's progress read with fetch", () => {
  it("sends the token in the header, never the address, and reads every event", async () => {
    const requests: { url: string; headers: Record<string, string> }[] = [];
    const stream = new FetchEventStream(URL_, fakeBearer("t1").bearer, async (url, init) => {
      requests.push({ url, headers: init.headers as Record<string, string> });
      return sse([
        ": ping\n\n",
        'id: 1\nevent: progress\ndata: {"a":1}\n\n',
        "id: 2\r\nevent: progress\r\ndata: line one\r\ndata: line two\r\n\r\n",
        "id: 3\nevent: run\nda",
        'ta: {"final":true}\n\n',
      ]);
    });
    const { seen, ended } = listen(stream);
    await ended;
    expect(requests).toEqual([
      { url: URL_, headers: { Authorization: "Bearer t1", Accept: "text/event-stream" } },
    ]);
    expect(seen).toEqual([
      { type: "progress", data: '{"a":1}' },
      { type: "progress", data: "line one\nline two" },
      { type: "run", data: '{"final":true}' },
      { type: "error" }, // the stream has ended: the adapter reads the run
    ]);
    expect(stream.readyState).toBe(CLOSED);
  });

  it("a CRLF split across two reads still ends one line, not two", async () => {
    const stream = new FetchEventStream(URL_, fakeBearer("t1").bearer, async () =>
      sse(["event: progress\r", "\ndata: {}\r", "\n\r", "\n"]),
    );
    const { seen, ended } = listen(stream);
    await ended;
    expect(seen).toEqual([{ type: "progress", data: "{}" }, { type: "error" }]);
  });

  it("is open while it reads", async () => {
    let release: () => void = () => undefined;
    const held = new ReadableStream<Uint8Array>({
      start(controller) {
        release = () => controller.close();
      },
    });
    const stream = new FetchEventStream(
      URL_,
      fakeBearer("t1").bearer,
      async () => new Response(held),
    );
    const { ended } = listen(stream);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stream.readyState).toBe(OPEN);
    release();
    await ended;
  });

  it("retries a 404 once with a refreshed token, then gives up", async () => {
    const sent: string[] = [];
    const { bearer, counts } = fakeBearer("old", "fresh");
    const stream = new FetchEventStream(URL_, bearer, async (_url, init) => {
      sent.push((init.headers as Record<string, string>)["Authorization"] ?? "");
      return new Response(JSON.stringify({ detail: "We couldn't find that search." }), {
        status: 404,
      });
    });
    const { seen, ended } = listen(stream);
    await ended;
    expect(sent).toEqual(["Bearer old", "Bearer fresh"]);
    expect(counts.refreshed).toBe(1);
    expect(seen).toEqual([{ type: "error" }]);
  });

  it("the refreshed token works", async () => {
    const stream = new FetchEventStream(
      URL_,
      fakeBearer("old", "fresh").bearer,
      async (_url, init) =>
        (init.headers as Record<string, string>)["Authorization"] === "Bearer fresh"
          ? sse(["event: run\ndata: {}\n\n"])
          : new Response("", { status: 404 }),
    );
    const { seen, ended } = listen(stream);
    await ended;
    expect(seen.map((s) => s.type)).toEqual(["run", "error"]);
  });

  it("a refused connection, and a 204, say error", async () => {
    for (const answer of [
      () => Promise.reject(new TypeError("offline")),
      async () => new Response(null, { status: 204 }),
    ]) {
      const stream = new FetchEventStream(URL_, fakeBearer("t1").bearer, answer);
      const { seen, ended } = listen(stream);
      await ended;
      expect(seen).toEqual([{ type: "error" }]);
    }
  });

  it("closed by the adapter, it says nothing more", async () => {
    let aborted = false;
    const stream = new FetchEventStream(
      URL_,
      fakeBearer("t1").bearer,
      (_url, init) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("Aborted", "AbortError"));
          }),
        ),
    );
    const { seen } = listen(stream);
    await new Promise((resolve) => setTimeout(resolve, 0));
    stream.close();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(aborted).toBe(true);
    expect(seen).toEqual([]);
    expect(stream.readyState).toBe(CLOSED);
  });
});
