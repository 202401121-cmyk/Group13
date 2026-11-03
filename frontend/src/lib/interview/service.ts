import { createMockInterviewService } from "./mockService";
import type { InterviewService } from "./types";

let service: InterviewService | null = null;

/** Single entry point for the UI. Swap the mock for a real adapter here. */
export function getInterviewService(): InterviewService {
  service ??= createMockInterviewService();
  return service;
}
