import { useEffect, useState } from "react";
import { SESSION_ID_PATTERN } from "@/lib/api/client";
import type { Brief } from "@/lib/request/types";
import type { InterviewState } from "@/lib/interview/reducer";

export interface Session {
  id: string;
  brief: Brief;
  interview?: Partial<InterviewState>;
  /** Background stage of this search. */
  stage?: "interview" | "research" | "results";
}

const key = (id: string) => `shopsense.session.${id}`;

/** The last confirmed brief, and (live mode) its backend session, for the older /criteria link. */
export const CONFIRMED_KEY = "shopsense.brief.confirmed.v1";
export const CONFIRMED_SESSION_KEY = "shopsense.brief.confirmed.session.v1";

/** A new session ID in the backend's own format: "sess_" + 24 hex characters (backend/app/api/sessions.py). */
export function newSessionId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return "sess_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The backend, the contracts and the database all reject any other shape. */
export function isSessionId(id: string): boolean {
  return SESSION_ID_PATTERN.test(id);
}

/** Starts a session for a confirmed brief. In live mode `id` is the session the backend gave at intake. */
export function createSession(brief: Brief, id: string = newSessionId()): string {
  saveSession({ id, brief });
  return id;
}

export function loadSession(id: string): Session | null {
  if (!isSessionId(id)) return null;
  try {
    const raw = sessionStorage.getItem(key(id));
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session) {
  try {
    sessionStorage.setItem(key(s.id), JSON.stringify(s));
  } catch {
    /* storage unavailable */
  }
}

/** Reads the session after hydration. `undefined` = still loading, `null` = missing. */
export function useSession(id: string): Session | null | undefined {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => setSession(loadSession(id)), [id]);
  return session;
}
