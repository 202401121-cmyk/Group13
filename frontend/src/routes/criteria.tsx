import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { PageShell } from "@/components/shopsense/PageShell";
import { pillButton } from "@/components/shopsense/pill-button";
import { CONFIRMED_KEY, CONFIRMED_SESSION_KEY, createSession } from "@/lib/session/store";
import type { Brief } from "@/lib/request/types";

export const Route = createFileRoute("/criteria")({
  head: () => ({
    meta: [
      { title: "Continue your search — ShopSense" },
      { name: "description", content: "Pick up your ShopSense search where you left off." },
      { property: "og:title", content: "Continue your search — ShopSense" },
      { property: "og:description", content: "Pick up your ShopSense search where you left off." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: LegacyCriteria,
});

/** Older link target: moves a confirmed brief into a session-scoped criteria page. */
function LegacyCriteria() {
  const navigate = useNavigate();
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(CONFIRMED_KEY);
      if (raw) {
        // Live mode must reuse the backend's session; mock mode makes a new one.
        const known = sessionStorage.getItem(CONFIRMED_SESSION_KEY) ?? undefined;
        const sessionId = createSession(JSON.parse(raw) as Brief, known);
        navigate({ to: "/s/$sessionId/criteria", params: { sessionId }, replace: true });
        return;
      }
    } catch {
      /* fall through */
    }
    setMissing(true);
  }, [navigate]);
  return (
    <PageShell>
      <section className="mx-auto max-w-3xl px-6 py-28">
        {missing && (
          <>
            <h1 className="font-serif text-5xl text-foreground">No brief yet</h1>
            <p className="mt-4 text-lg text-muted-foreground">
              Tell us what you're shopping for first.
            </p>
            <Link to="/research" className={`${pillButton()} mt-8`}>
              Describe what you need
            </Link>
          </>
        )}
      </section>
    </PageShell>
  );
}
