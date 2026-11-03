import { createMockResearchService } from "./mockService";
import type { ResearchService } from "./types";

let service: ResearchService | null = null;

/** Single entry point for the UI. Swap the mock for a real adapter here. */
export function getResearchService(): ResearchService {
  service ??= createMockResearchService();
  return service;
}
