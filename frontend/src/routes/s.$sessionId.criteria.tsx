import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowRight, Info } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { BriefPanel } from "@/components/request/BriefPanel";
import { InlineError, MissingSession, PageLoading } from "@/components/interview/shared";
import { useInterview } from "@/lib/interview/useInterview";
import { briefCriteria } from "@/lib/interview/view";
import { inSentence } from "@/lib/text";

export const Route = createFileRoute("/s/$sessionId/criteria")({
  head: () => ({
    meta: [
      { title: "What matters for this product — ShopSense" },
      {
        name: "description",
        content:
          "See the criteria ShopSense will compare products on, then answer a few short questions.",
      },
      { property: "og:title", content: "What matters for this product — ShopSense" },
      { property: "og:description", content: "The criteria ShopSense compares products on." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "robots", content: "noindex" },
    ],
  }),
  component: CriteriaPage,
});

function CriteriaPage() {
  const { sessionId } = Route.useParams();
  const navigate = useNavigate();
  const { session, ready, state, retryCriteria } = useInterview(sessionId);

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
  const loading = state.phase === "loading-criteria";
  const failed = state.phase === "criteria-error";
  const started = state.step !== null;

  return (
    <PageShell>
      <div className="relative">
        <div className="relative mx-auto grid max-w-6xl gap-10 px-5 pb-16 pt-14 sm:px-6 md:pt-20 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0">
            <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
              Step 2 of 3
            </p>
            <h1 className="mt-3 font-serif text-4xl leading-tight text-foreground md:text-5xl">
              What matters for a {category}
            </h1>
            <p className="mt-3 max-w-xl text-muted-foreground md:text-lg">
              You'll answer up to 10 short questions about these. You can skip any of them.
            </p>

            {state.fallback && !loading && !failed && (
              <p className="mt-6 flex items-start gap-2 rounded-2xl bg-primary-soft px-4 py-3 text-sm text-primary-soft-foreground">
                <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                We're using a standard list for this kind of product.
              </p>
            )}

            <div className="mt-8" aria-live="polite" aria-busy={loading}>
              {loading && (
                <ul
                  aria-label="Loading criteria"
                  className="divide-y divide-border border-y border-border"
                >
                  {Array.from({ length: 6 }, (_, i) => (
                    <li key={i} className="space-y-2 py-5">
                      <div className="skeleton h-5 w-40" />
                      <div className="skeleton h-4 w-3/4" />
                    </li>
                  ))}
                </ul>
              )}
              {failed && (
                <InlineError
                  message={state.error ?? "We couldn't load the criteria."}
                  onRetry={retryCriteria}
                />
              )}
              {!loading && !failed && (
                <ul className="divide-y divide-border border-y border-border">
                  {state.criteria.map((c) => (
                    <li
                      key={c.id}
                      className="flex flex-col gap-1 py-5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6"
                    >
                      <div className="min-w-0">
                        <h2 className="flex flex-wrap items-center gap-2 font-medium text-foreground">
                          {c.title}
                          {c.valueForMoney && (
                            <span className="rounded-full bg-champagne px-2.5 py-0.5 text-[11px] font-semibold text-champagne-foreground">
                              Value for money
                            </span>
                          )}
                        </h2>
                        <p className="mt-1 text-sm text-muted-foreground">{c.definition}</p>
                      </div>
                      {c.lowerIsBetter && (
                        <span className="shrink-0 text-xs font-medium text-primary">
                          Lower is better
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-4">
              <PillButton
                size="lg"
                className="min-h-11"
                disabled={loading || failed}
                onClick={() => navigate({ to: "/s/$sessionId/questions", params: { sessionId } })}
              >
                {started ? "Continue the questions" : "Start the questions"}{" "}
                <ArrowRight className="h-4 w-4" aria-hidden />
              </PillButton>
              <Link
                to="/research"
                className="text-sm font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              >
                Change my request
              </Link>
            </div>
          </div>
          <BriefPanel
            brief={session.brief}
            criteria={loading || failed ? undefined : briefCriteria(state)}
          />
        </div>
      </div>
    </PageShell>
  );
}
