import { apiConfig, createApiClient } from "@/lib/api/client";
import { createLiveInterviewService } from "./liveAdapter";
import { createMockInterviewService } from "./mockService";
import type { InterviewService } from "./types";

let service: InterviewService | null = null;

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getInterviewService(): InterviewService {
  const config = apiConfig();
  service ??=
    config.mode === "live"
      ? createLiveInterviewService(createApiClient(config.baseUrl))
      : createMockInterviewService();
  return service;
}
