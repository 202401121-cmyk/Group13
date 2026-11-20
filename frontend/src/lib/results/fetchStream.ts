/**
 * A run's progress stream read with fetch, so it can carry the signed-in shopper's token (S4-10, US-30).
 *
 * The browser's EventSource can't send an Authorization header, and a signed-in shopper's run answers 404 to a
 * request without its owner's token. So a signed-in shopper's stream is read here instead: the same Server-Sent
 * Events, parsed by hand, behind the same small interface (EventSourceLike) the adapter already uses. A guest's stream
 * still uses EventSource.
 *
 * - The token goes in the header only: never in the address, a log or storage.
 * - A 404 gets one retry with a token refreshed through supabase-js; a second 404 closes the stream, and the adapter
 *   then reads the run, which says it can't be found.
 * - It never reconnects by itself. When the connection ends, it says "error" with readyState CLOSED, and the adapter
 *   reads the run and opens a new stream if the run is still going, as it does when an EventSource gives up.
 */
import { callHeaders, tokenOf, type BearerSource } from "@/lib/api/client";
import type { EventSourceLike } from "./liveAdapter";

export const CONNECTING = 0;
export const OPEN = 1;
export const CLOSED = 2;

type Listener = (e: { data?: unknown }) => void;
type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export class FetchEventStream implements EventSourceLike {
  readyState = CONNECTING;
  private readonly listeners = new Map<string, Set<Listener>>();
  private readonly abort = new AbortController();

  constructor(
    url: string,
    bearer: BearerSource,
    fetchImpl: FetchLike = (i, init) => fetch(i, init),
  ) {
    void this.read(url, bearer, fetchImpl);
  }

  addEventListener(type: string, listener: Listener): void {
    const set = this.listeners.get(type) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(type, set);
  }

  close(): void {
    this.readyState = CLOSED;
    this.abort.abort();
  }

  private emit(type: string, data?: unknown) {
    this.listeners.get(type)?.forEach((listener) => listener(data === undefined ? {} : { data }));
  }

  private async open(url: string, token: string | null, fetchImpl: FetchLike) {
    return fetchImpl(url, {
      headers: { ...callHeaders(token, false), Accept: "text/event-stream" },
      signal: this.abort.signal,
      cache: "no-store",
    });
  }

  private async read(url: string, bearer: BearerSource, fetchImpl: FetchLike) {
    try {
      const token = await tokenOf(bearer);
      let response = await this.open(url, token, fetchImpl);
      if (response.status === 404 && token) {
        const fresh = await tokenOf(bearer, true);
        if (fresh) response = await this.open(url, fresh, fetchImpl);
      }
      if (!response.ok || !response.body || response.status === 204) throw new Error("no stream");
      this.readyState = OPEN;
      await this.parse(response.body);
    } catch {
      /* a refused, failed or ended connection: said below, the same way */
    }
    if (this.abort.signal.aborted) return; // closed by the adapter: it says nothing more
    this.readyState = CLOSED;
    this.emit("error");
  }

  /** Server-Sent Events: "event:" and "data:" lines, a blank line ends each event, ":" lines are comments. */
  private async parse(body: ReadableStream<Uint8Array>) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let type = "message";
    let data: string[] = [];
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const ending = /\r\n|\r|\n/.exec(buffer);
        // A "\r" at the very end may be half of a "\r\n" split across two reads: wait for the next one.
        if (!ending || (ending[0] === "\r" && ending.index === buffer.length - 1)) break;
        const line = buffer.slice(0, ending.index);
        buffer = buffer.slice(ending.index + ending[0].length);
        if (line === "") {
          if (data.length > 0 && !this.abort.signal.aborted) this.emit(type, data.join("\n"));
          type = "message";
          data = [];
          continue;
        }
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") type = value || "message";
        else if (field === "data") data.push(value);
      }
    }
  }
}
