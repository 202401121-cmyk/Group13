import { apiConfig, createApiClient } from "@/lib/api/client";
import { createLiveRequestService } from "./liveAdapter";
import { createMockService } from "./mockService";
import type { RequestService } from "./types";

let service: RequestService | null = null;

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getRequestService(): RequestService {
  const config = apiConfig();
  service ??=
    config.mode === "live"
      ? createLiveRequestService(createApiClient(config.baseUrl))
      : createMockService();
  return service;
}
