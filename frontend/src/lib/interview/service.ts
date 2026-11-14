import { ApiError, MESSAGES, apiConfig, createApiClient } from "@/lib/api/client";
import { createMockInterviewService } from "./mockService";
import type { InterviewService } from "./types";

let service: InterviewService | null = null;

/**
 * The live adapter, fetched as its own chunk when the service is made in live mode, so mock mode never downloads it
 * (D-45 budgets). Every method already returns a promise, so each simply waits for it. A chunk that fails to load is
 * the network failing: the screen says so, and the next call tries again.
 */
function liveService(baseUrl: string): InterviewService {
  let live: Promise<InterviewService> | null = null;
  const adapter = () =>
    (live ??= import("./liveAdapter").then(
      (m) => m.createLiveInterviewService(createApiClient(baseUrl)),
      () => {
        live = null;
        throw new ApiError("network", MESSAGES.network);
      },
    ));
  void adapter().catch(() => {
    /* the first call says so, and tries again */
  });
  return {
    getCriteria: async (brief, sessionId) => (await adapter()).getCriteria(brief, sessionId),
    turn: async (input) => (await adapter()).turn(input),
    loadRubric: async (sessionId, seed) => (await adapter()).loadRubric(sessionId, seed),
    editRubric: async (sessionId, edits) => (await adapter()).editRubric(sessionId, edits),
    parseExclusions: async (text) => (await adapter()).parseExclusions(text),
  };
}

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getInterviewService(): InterviewService {
  const config = apiConfig();
  service ??= config.mode === "live" ? liveService(config.baseUrl) : createMockInterviewService();
  return service;
}
