import { MESSAGES, apiConfig, createApiClient } from "@/lib/api/client";
import { createMockResearchService } from "./mockService";
import { ResearchError, type ResearchService } from "./types";

let service: ResearchService | null = null;

/**
 * The live adapter, fetched as its own chunk when the service is made in live mode, so mock mode never downloads it
 * (D-45 budgets), as the interview service does. A chunk that fails to load is the network failing: the screen says
 * so, and the next call tries again. "Where this comes from" never fails: it falls back to the claims it has.
 */
function liveService(baseUrl: string): ResearchService {
  let live: Promise<ResearchService> | null = null;
  const adapter = () =>
    (live ??= import("./liveAdapter").then(
      (m) =>
        m.createLiveResearchService(createApiClient(baseUrl), {
          baseUrl,
          openStream: (url) => new EventSource(url),
        }),
      () => {
        live = null;
        throw new ResearchError("network", MESSAGES.network);
      },
    ));
  void adapter().catch(() => {
    /* the first call says so, and tries again */
  });
  return {
    isMock: false,
    start: async (input) => (await adapter()).start(input),
    read: async (searchId, input) => (await adapter()).read(searchId, input),
    follow(searchId, input, handlers) {
      let closed = false;
      let stop: (() => void) | null = null;
      adapter().then(
        (s) => {
          if (!closed) stop = s.follow(searchId, input, handlers);
        },
        (error: unknown) => {
          if (!closed) handlers.onError(error);
        },
      );
      return () => {
        closed = true;
        stop?.();
      };
    },
    cancel: async (searchId) => (await adapter()).cancel(searchId),
    async evidence(searchId, productId, criterionId, fallback) {
      try {
        return await (await adapter()).evidence(searchId, productId, criterionId, fallback);
      } catch {
        return { state: null, claims: fallback, withheldReason: null, from: "citations" };
      }
    },
  };
}

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getResearchService(): ResearchService {
  const config = apiConfig();
  service ??= config.mode === "live" ? liveService(config.baseUrl) : createMockResearchService();
  return service;
}
