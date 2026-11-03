import { useEffect, useRef, useState } from "react";
import { createFileRoute, Link, useBlocker, useNavigate } from "@tanstack/react-router";
import { Check } from "lucide-react";
import { toast } from "sonner";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { MissingSession, PageLoading } from "@/components/interview/shared";
import { BriefSections } from "@/components/results/BriefSections";
import { Disclosure, Notice, StatusBadge } from "@/components/results/primitives";
import { ResultCard } from "@/components/results/ResultCard";
import { formatCheckTime, tiesFor } from "@/lib/results/view";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useInterview } from "@/lib/interview/useInterview";
import { loadSession, saveSession, type Session } from "@/lib/session/store";
import { rubricForBrief, weightRows, type WeightRow } from "@/lib/results/rubric";
import {
  getResearchState,
  researchServiceInfo,
  resetResearch,
  retryResearch,
  stopResearch,
  useResearch,
} from "@/lib/results/controller";
import { formatElapsed, isStageDone, type ResearchState } from "@/lib/results/machine";
import { RESEARCH_STAGES, type ResearchResult } from "@/lib/results/types";

export const Route = createFileRoute("/s/$sessionId/results")({
  head: () => ({
    meta: [
      { title: "Your results — ShopSense" },
      { name: "description", content: "ShopSense research results matched to your brief." },
      { property: "og:title", content: "Your results — ShopSense" },
      { property: "og:description", content: "Research results matched to your brief." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "robots", content: "noindex" },
    ],
  }),
  component: ResultsPage,
});

function ResultsPage() {
  const { sessionId } = Route.useParams();
  const navigate = useNavigate();
  const { session, ready, state: interview } = useInterview(sessionId);
  const research = useResearch(sessionId);

  const blocker = useBlocker({
    shouldBlockFn: () => {
      const st = getResearchState(sessionId).status;
      return st === "researching" || st === "starting";
    },
    enableBeforeUnload: false,
    withResolver: true,
  });

  // Tab title.
  useEffect(() => {
    if (research.status === "researching") document.title = "Researching... | ShopSense";
    else if (research.status === "done") document.title = "Results ready | ShopSense";
  }, [research.status]);

  // Background stage.
  useEffect(() => {
    if (research.status !== "done") return;
    const s = loadSession(sessionId);
    if (s && s.stage !== "results") saveSession({ ...s, stage: "results" });
  }, [research.status, sessionId]);

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

  const category = (session.brief.product ?? "product").toLowerCase();
  const rows = weightRows(rubricForBrief(interview, !researchServiceInfo().isMock) ?? []);

  function stop() {
    stopResearch(sessionId);
    toast("Research stopped");
  }
  function backToBrief() {
    resetResearch(sessionId);
    void navigate({ to: "/s/$sessionId/brief", params: { sessionId } });
  }

  const summary = <BriefSummary session={session} rows={rows} />;

  return (
    <PageShell>
      <div className="mx-auto grid max-w-6xl gap-8 px-4 pb-20 pt-10 sm:px-6 md:pt-14 lg:grid-cols-[minmax(0,760px)_20rem] lg:justify-between">
        <div className="min-w-0">
          {(research.status === "researching" || research.status === "starting") && (
            <Waiting
              state={research}
              category={category}
              onStop={() => {
                stop();
                void navigate({ to: "/s/$sessionId/brief", params: { sessionId } });
              }}
            />
          )}
          {research.status === "timedOut" && (
            <Failure
              category={category}
              message="Research took too long. Your brief is safe. Try again, or try again later."
              onRetry={() => retryResearch(sessionId)}
              onBack={backToBrief}
            />
          )}
          {research.status === "error" && (
            <Failure
              category={category}
              message={
                research.kind === "network"
                  ? "We lost the connection while researching. Your brief is safe."
                  : "Something went wrong while researching. Your brief is safe."
              }
              onRetry={() => retryResearch(sessionId)}
              onBack={backToBrief}
            />
          )}
          {research.status === "done" && (
            <Results result={research.result} category={category} sessionId={sessionId} />
          )}
          {(research.status === "briefReady" || research.status === "stopping") && (
            <Gone sessionId={sessionId} hasBrief={Boolean(session.brief)} />
          )}
        </div>
        {research.status !== "briefReady" && <div className="min-w-0 lg:pt-2">{summary}</div>}
      </div>

      <AlertDialog open={blocker.status === "blocked"}>
        <AlertDialogContent
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            document.getElementById("keep-researching")?.focus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Stop research?</AlertDialogTitle>
            <AlertDialogDescription>
              Leaving this page stops the research. Your brief and answers are kept.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel id="keep-researching" onClick={() => blocker.reset?.()}>
              Keep researching
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                stop();
                blocker.proceed?.();
              }}
            >
              Stop research
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageShell>
  );
}

function BriefSummary({ session, rows }: { session: Session; rows: WeightRow[] }) {
  return (
    <aside aria-label="Your brief" className="lg:sticky lg:top-28">
      <Disclosure
        summary={<span className="font-serif text-xl font-normal">Your brief</span>}
        className="bg-surface shadow-soft"
      >
        <BriefSections brief={session.brief} rows={rows} compact />
        <Link
          to="/s/$sessionId/brief"
          params={{ sessionId: session.id }}
          className="mt-5 inline-flex min-h-11 items-center text-sm font-medium text-primary hover:underline"
        >
          Back to your brief
        </Link>
      </Disclosure>
    </aside>
  );
}

function Waiting({
  state,
  category,
  onStop,
}: {
  state: ResearchState;
  category: string;
  onStop: () => void;
}) {
  const info = researchServiceInfo();
  const startedAt = state.status === "researching" ? state.startedAt : Date.now();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const doneCount = state.status === "researching" ? state.events.length : 0;
  const lastDone =
    state.status === "researching" && state.events.length
      ? RESEARCH_STAGES[state.events.at(-1)!.stage]
      : null;

  return (
    <section aria-labelledby="research-title">
      <h1
        id="research-title"
        className="break-words font-serif text-4xl leading-tight text-foreground md:text-5xl"
      >
        Researching {category}s for you
      </h1>
      <p className="mt-3 text-muted-foreground md:text-lg">
        This can take up to 2½ minutes. You can keep this page in the background.
      </p>
      {info.isMock && (
        <p className="mt-4">
          <StatusBadge variant="neutral">Preview: simulated progress from mock data</StatusBadge>
        </p>
      )}

      <div className="mt-8 rounded-2xl border border-border bg-card p-6 shadow-soft sm:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="font-serif text-2xl text-foreground">What ShopSense is doing</h2>
          <p className="font-mono text-xs tabular-nums text-muted-foreground">
            <span className="sr-only">Elapsed time: </span>
            {formatElapsed(now - startedAt)}
          </p>
        </div>
        <ol className="mt-6 space-y-4">
          {RESEARCH_STAGES.map((label, i) => {
            const done = info.streamsProgress && isStageDone(state, i);
            return (
              <li key={label} className="flex items-start gap-3">
                <span
                  className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border text-xs ${done ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}
                  aria-hidden
                >
                  {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
                </span>
                <span className="min-w-0 flex-1 text-foreground">{label}</span>
                {done && <span className="font-mono text-xs font-medium text-primary">Done</span>}
              </li>
            );
          })}
        </ol>
        <p className="sr-only" aria-live="polite">
          {lastDone ? `${lastDone}: done. ${doneCount} of 4 steps done.` : ""}
        </p>
        <PillButton variant="outline" className="mt-8 min-h-11" onClick={onStop}>
          Stop research
        </PillButton>
      </div>
    </section>
  );
}

function Failure({
  category,
  message,
  onRetry,
  onBack,
}: {
  category: string;
  message: string;
  onRetry: () => void;
  onBack: () => void;
}) {
  return (
    <section>
      <h1 className="break-words font-serif text-4xl leading-tight text-foreground md:text-5xl">
        Researching {category}s for you
      </h1>
      <div className="mt-8">
        <Notice tone="error">{message}</Notice>
      </div>
      <div className="mt-6 flex flex-wrap gap-3">
        <PillButton onClick={onRetry} className="min-h-11">
          Try again
        </PillButton>
        <PillButton variant="outline" onClick={onBack} className="min-h-11">
          Back to your brief
        </PillButton>
      </div>
    </section>
  );
}

function Gone({ sessionId, hasBrief }: { sessionId: string; hasBrief: boolean }) {
  return (
    <section className="py-10">
      <h1 className="font-serif text-4xl text-foreground md:text-5xl">
        These results aren't here any more
      </h1>
      <p className="mt-4 text-lg text-muted-foreground">
        Results are only kept while this page is open. Your brief is still saved.
      </p>
      {hasBrief ? (
        <Link
          to="/s/$sessionId/brief"
          params={{ sessionId }}
          className={`${pillButton()} mt-8 min-h-11`}
        >
          Back to your brief
        </Link>
      ) : (
        <Link to="/research" className={`${pillButton()} mt-8 min-h-11`}>
          Start a new search
        </Link>
      )}
    </section>
  );
}

function Results({
  result,
  category,
  sessionId,
}: {
  result: ResearchResult;
  category: string;
  sessionId: string;
}) {
  const h1 = useRef<HTMLHeadingElement>(null);
  const [announce, setAnnounce] = useState("");
  const [best, ...others] = result.products;

  useEffect(() => {
    h1.current?.focus();
    setAnnounce(
      best
        ? `Results ready. ${result.products.length} options, best match ${best.name}.`
        : "Results ready. No options matched.",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pricesTime = formatCheckTime(result.pricesCheckedAt);

  return (
    <section aria-labelledby="results-title">
      <h1
        id="results-title"
        ref={h1}
        tabIndex={-1}
        className="break-words font-serif text-4xl leading-tight text-foreground outline-none md:text-5xl"
      >
        Results for your {category}
      </h1>
      {result.source === "mock" && (
        <p className="mt-3">
          <StatusBadge variant="neutral">Preview: mock data, not a live search</StatusBadge>
        </p>
      )}
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      <div className="mt-6 space-y-3">
        {!result.saved && (
          <Notice>This result isn't saved. It will be gone if you close this page.</Notice>
        )}
        {result.coverageNote ? (
          <Notice tone="warn">{result.coverageNote}</Notice>
        ) : result.partial ? (
          <Notice tone="warn">These results are partial.</Notice>
        ) : null}
        {result.relaxation && (
          <Notice
            tone="warn"
            action={
              <Link
                to="/s/$sessionId/brief"
                params={{ sessionId }}
                className={`${pillButton({ variant: "outline", size: "sm" })} min-h-11`}
              >
                Change must-haves
              </Link>
            }
          >
            {result.relaxation.hint}{" "}
            {/* Shown only when the backend names the rule; never a guess. */}
            {result.relaxation.mustHave && (
              <span className="font-semibold">Must-have: {result.relaxation.mustHave}</span>
            )}
          </Notice>
        )}
      </div>

      {best ? (
        <>
          <h2 className="mt-10 font-serif text-2xl text-foreground">Best match</h2>
          <ol className="mt-4" aria-label="Best match">
            <li className="animate-rise-in">
              <ResultCard
                product={best}
                tiedWith={tiesFor(result.products, best)}
                featured
                index={0}
              />
            </li>
          </ol>
          {others.length > 0 && (
            <>
              <h2 className="mt-12 font-serif text-2xl text-foreground">Other options</h2>
              <ol className="mt-4 space-y-5 animate-fade-soft" start={2} aria-label="Other options">
                {others.map((p, i) => (
                  <li key={p.id}>
                    <ResultCard product={p} tiedWith={tiesFor(result.products, p)} index={i + 1} />
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      ) : (
        <p className="mt-10 text-lg text-muted-foreground">
          No option matched your brief this time.
        </p>
      )}

      <div className="mt-10 space-y-3">
        {result.leftOut.length > 0 && (
          <Disclosure summary={`Left out (${result.leftOut.length})`}>
            <ul className="divide-y divide-border">
              {result.leftOut.map((p) => (
                <li key={p.name} className="py-2.5 text-sm">
                  <span className="font-medium text-foreground">{p.name}</span>
                  <span className="block text-muted-foreground">{p.reason}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
        {result.couldntCheck.length > 0 && (
          <Disclosure summary={`Couldn't check (${result.couldntCheck.length})`}>
            <ul className="divide-y divide-border">
              {result.couldntCheck.map((p) => (
                <li key={p.name} className="py-2.5 text-sm">
                  <span className="font-medium text-foreground">{p.name}</span>
                  <span className="block text-muted-foreground">{p.reason}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
      </div>

      {pricesTime && (
        <p className="mt-10 border-t border-border pt-6 text-sm text-muted-foreground">
          Prices checked {pricesTime}. Prices can change. Check the store before you buy.
        </p>
      )}
    </section>
  );
}
