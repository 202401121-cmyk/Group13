import { apiConfig, createApiClient } from "@/lib/api/client";
import { createLiveResearchService } from "./liveAdapter";
import { createMockResearchService } from "./mockService";
import type { ResearchService } from "./types";

let service: ResearchService | null = null;

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getResearchService(): ResearchService {
  const config = apiConfig();
  service ??=
    config.mode === "live"
      ? createLiveResearchService(createApiClient(config.baseUrl), {
          baseUrl: config.baseUrl,
          openStream: (url) => new EventSource(url),
        })
      : createMockResearchService();
  return service;
}
