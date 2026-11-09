import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { ArrowRight, Loader2 } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { MissingSession, PageLoading } from "@/components/interview/shared";
import { BriefSections } from "@/components/results/BriefSections";
import { researchInput } from "@/lib/results/view";
import { useInterview } from "@/lib/interview/useInterview";
import { interviewReducer, toInterviewSnapshot } from "@/lib/interview/reducer";
import { saveSession } from "@/lib/session/store";
import { rubricForBrief, weightRows } from "@/lib/results/rubric";
import {
  leaveResearch,
  researchServiceInfo,
  startResearch,
  useResearch,
} from "@/lib/results/controller";
import { MOCK_SCENARIOS } from "@/lib/results/mockService";
import { inSentence } from "@/lib/text";

export const Route = createFileRoute("/s/$sessionId/brief")({
  head: () => ({
    meta: [
      { title: "Your brief — ShopSense" },
      {
        name: "description",
        content: "Check your shopping brief before ShopSense starts research.",
      },
      { property: "og:title", content: "Your brief — ShopSense" },
      { property: "og:description", content: "Check your shopping brief before research starts." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "robots", content: "noindex" },
    ],
  }),
  component: BriefReview,
});

function BriefReview() {
  const { sessionId } = Route.useParams();
  const navigate = useNavigate();
  const router = useRouter();
  const { session, ready, state } = useInterview(sessionId);
  const research = useResearch(sessionId);
  const [starting, setStarting] = useState(false);
  const [scenario, setScenario] = useState<string>("success");
  const info = researchServiceInfo();

  // Fetch the results page's code while the shopper reads the brief. Otherwise, on a slow connection,
  // "Start research" waits for it and the page then jumps well after the tap (CLS).
  useEffect(() => {
    void router.preloadRoute({ to: "/s/$sessionId/results", params: { sessionId } }).catch(() => {
      /* a failed preload only means the page loads on click, as before */
    });
  }, [router, sessionId]);

  if (session === null)
    return (
      <PageShell>
        <MissingSession />
      </PageShell>
    );
  if (!session || !ready)
    return (
      <PageShell>
        <PageLoading />
      </PageShell>
    );

  const category = inSentence(session.brief.product ?? "product");
  // Live mode shows the backend's own weights, which exist only once the interview is finished.
  const rubric = rubricForBrief(state, !info.isMock);
  const rows = weightRows(rubric ?? []);

  function change(criterionId: string) {
    if (!session) return;
    const next = interviewReducer(state, { type: "GO_BACK", criterionId });
    saveSession({ ...session, interview: toInterviewSnapshot(next) });
    void navigate({ to: "/s/$sessionId/questions", params: { sessionId } });
  }

  function start() {
    if (!session || starting || !rubric) return;
    setStarting(true);
    // A run being stopped or read again isn't the search the shopper is asking for now. One that is simply
    // still going is left alone, and the results page shows it.
    if (research.status === "stopping" || research.status === "attaching") {
      void leaveResearch(sessionId);
    }
    startResearch(
      researchInput(sessionId, session.brief, rubric, info.isMock ? scenario : undefined),
    );
    saveSession({ ...session, stage: "research" });
    // Paint the busy button first, then draw the results page in the next frame. The results page's code
    // is already loaded, so drawing it inside this tap made the tap's response slow (INP).
    requestAnimationFrame(() => {
      setTimeout(() => void navigate({ to: "/s/$sessionId/results", params: { sessionId } }), 0);
    });
  }

  return (
    <PageShell>
      <div className="relative">
        <section className="relative mx-auto max-w-[760px] px-5 pb-20 pt-14 sm:px-6 md:pt-20">
          <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
            Step 3 of 3
          </p>
          <h1 className="mt-3 break-words font-serif text-4xl leading-tight text-foreground md:text-5xl">
            Your brief for a {category}
          </h1>
          <p className="mt-3 text-muted-foreground md:text-lg">
            Check this before we start. Research takes up to 2½ minutes.
          </p>

          <div className="mt-10">
            <BriefSections brief={session.brief} rows={rows} onChange={change} />
          </div>

          {!rubric && (
            <p role="status" className="mt-8 rounded-2xl bg-secondary px-4 py-3 text-foreground">
              Finish the questions first. Then you'll see how much each one counts, and you can
              start the research.
            </p>
          )}

          {info.isMock && (
            <div className="mt-10 rounded-2xl border border-dashed border-border bg-card/60 p-4">
              <label htmlFor="scenario" className="text-sm font-semibold text-foreground">
                Preview scenario{" "}
                <span className="font-normal text-muted-foreground">
                  (mock data, no live search)
                </span>
              </label>
              <select
                id="scenario"
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                className="mt-2 block min-h-11 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring"
              >
                {MOCK_SCENARIOS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="mt-8 flex flex-col-reverse gap-3 sm:flex-row sm:items-center">
            <Link
              to="/s/$sessionId/questions"
              params={{ sessionId }}
              className={`${pillButton({ variant: "outline" })} min-h-11`}
            >
              Back to the questions
            </Link>
            <PillButton
              onClick={start}
              disabled={starting || !rubric}
              aria-busy={starting}
              className="min-h-11"
            >
              {starting ? (
                <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
              ) : null}
              Start research
              {!starting && <ArrowRight className="h-4 w-4" aria-hidden />}
            </PillButton>
          </div>
        </section>
      </div>
    </PageShell>
  );
}
