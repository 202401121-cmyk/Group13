import { createMockService } from "./mockService";
import type { RequestService } from "./types";

let service: RequestService | null = null;

/** Single entry point for the UI. Swap the mock for a real adapter here. */
export function getRequestService(): RequestService {
  service ??= createMockService();
  return service;
}
