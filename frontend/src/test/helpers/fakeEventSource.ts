/** A stand-in for the browser's EventSource, so stream tests never open a real connection. */
import type { EventSourceLike } from "@/lib/results/liveAdapter";

const CONNECTING = 0;
const OPEN = 1;
const CLOSED = 2;

type Listener = (e: { data?: unknown }) => void;

export class FakeEventSource implements EventSourceLike {
  readyState = OPEN;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.readyState = CLOSED;
  }

  private fire(type: string, event: { data?: unknown }) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  /** The backend sends one event, as JSON. */
  send(type: "progress" | "run", body: unknown) {
    this.fire(type, { data: JSON.stringify(body) });
  }

  /** The backend sends one event whose data is exactly this text. */
  sendRaw(type: "progress" | "run", data: string) {
    this.fire(type, { data });
  }

  /** The connection dropped, and the browser will reconnect by itself with Last-Event-ID. */
  drop() {
    this.readyState = CONNECTING;
    this.fire("error", {});
  }

  /** The browser has given up on this stream (a 204, a 404 or a refused connection), for good. */
  end() {
    this.readyState = CLOSED;
    this.fire("error", {});
  }
}

/** Opens fake streams and remembers each one, newest last. */
export function fakeStreams() {
  const opened: FakeEventSource[] = [];
  return {
    opened,
    open: (url: string) => {
      const stream = new FakeEventSource(url);
      opened.push(stream);
      return stream;
    },
  };
}
