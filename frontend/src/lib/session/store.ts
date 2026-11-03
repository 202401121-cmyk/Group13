import { useEffect, useState } from "react";
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

export function createSession(brief: Brief): string {
  const id = (crypto.randomUUID?.() ?? `${Date.now()}${Math.random()}`).replace(/[^a-z0-9]/gi, "").slice(0, 10);
  saveSession({ id, brief });
  return id;
}

export function loadSession(id: string): Session | null {
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
