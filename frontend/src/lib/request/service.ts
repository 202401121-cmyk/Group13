import { apiConfig, createApiClient } from "@/lib/api/client";
import { bearerSource } from "@/lib/auth/service";
import { createLiveRequestService } from "./liveAdapter";
import { createMockService } from "./mockService";
import type { RequestService } from "./types";

let service: RequestService | null = null;

/** Single entry point for the UI: the mock, or the backend when VITE_API_MODE is "live". */
export function getRequestService(): RequestService {
  const config = apiConfig();
  service ??=
    config.mode === "live"
      ? createLiveRequestService(createApiClient(config.baseUrl, undefined, bearerSource))
      : createMockService();
  return service;
}
