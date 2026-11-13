import { useCallback, useEffect, useRef, useState } from "react";
import { createFileRoute, Link, useBlocker, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { MissingSession, PageLoading } from "@/components/interview/shared";
import { BriefSections } from "@/components/results/BriefSections";
import { ResearchProgress } from "@/components/results/ResearchProgress";
import { Disclosure, NotSavedNotice, Notice, StatusBadge } from "@/components/results/primitives";
import {
  CouldntCheckItem,
  ResultCard,
  TooCloseToCall,
  type ShowSources,
} from "@/components/results/ResultCard";
import { SourceDrawer, type SourceTarget } from "@/components/results/SourceDrawer";
import {
  formatCheckTime,
  giveFocusBack,
  latestOnly,
  researchInput,
  tieGroups,
  tiesFor,
} from "@/lib/results/view";
import { useInterview } from "@/lib/interview/useInterview";
import { loadSession, saveSession, type Session } from "@/lib/session/store";
import { rubricForBrief, weightRows, type WeightRow } from "@/lib/results/rubric";
import {
  attachResearch,
  checkForResults,
  getResearchState,
  leaveResearch,
  researchServiceInfo,
  resetResearch,
  retryResearch,
  sourcesFor,
  stopResearch,
  useResearch,
} from "@/lib/results/controller";
import {
  formatElapsed,
  isOngoing,
  searchIdOf,
  type Connection,
  type ResearchState,
  type StopNote,
} from "@/lib/results/machine";
import { announcementFor } from "@/lib/results/progress";
import type {
  EvidenceView,
  ProductResult,
  ProgressEvent,
  ResearchResult,
} from "@/lib/results/types";
import { inSentence, listInWords } from "@/lib/text";

// Loaded on demand: the dialog library is only needed if the shopper tries to stop or leave during research.
const loadStopDialog = () => import("@/components/results/StopResearchDialog");
type StopDialog = Awaited<ReturnType<typeof loadStopDialog>>["default"];

/** Only the search ID goes in the address, never the shopper's words. Checked before any use of it. */
function searchFromAddress(search: Record<string, unknown>): { search?: string } {
  return search["search"] === undefined || search["search"] === null
    ? {}
    : { search: String(search["search"]) };
}

export const Route = createFileRoute("/s/$sessionId/results")({
  validateSearch: searchFromAddress,
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
  component: ResultsRoute,
});

/** Another session is another page: nothing from this one carries over. */
function ResultsRoute() {
  const { sessionId } = Route.useParams();
  return <ResultsPage key={sessionId} sessionId={sessionId} />;
}

const TAB_TITLES: Partial<Record<ResearchState["status"], string>> = {
  researching: "Researching...",
  done: "Results ready",
  stopped: "Search stopped",
  timedOut: "Search took too long",
  error: "Search problem",
};

function ResultsPage({ sessionId }: { sessionId: string }) {
  const { search: addressSearch } = Route.useSearch();
  const navigate = useNavigate();
  const { session, ready, state: interview } = useInterview(sessionId);
  const research = useResearch(sessionId);
  const stateSearch = searchIdOf(research);

  const blocker = useBlocker({
    // Writing the search ID into the address is a navigation too, so only a change of page counts.
    shouldBlockFn: ({ current, next }) => {
      const st = getResearchState(sessionId).status;
      return (st === "researching" || st === "starting") && current.pathname !== next.pathname;
    },
    enableBeforeUnload: false,
    withResolver: true,
  });

  // Keeps the address and the run together. The address gains the search ID as soon as there is one, so a
  // reload can re-attach. An address the run doesn't match (a reload, or coming back to an earlier search) is
  // read from the backend. An ID on its way into the address, with the run already ended, is left to arrive.
  // On the way out of the page the run has been let go, and the address still names it until the page changes.
  // While the leave question is open, the browser may already be on the entry it is going Back to, so writing to
  // the address would overwrite that entry: it waits for the answer, and for the browser to come back after it.
  const writing = useRef<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [cameBack, setCameBack] = useState(0);
  const leaveQuestionOpen = blocker.status === "blocked";
  useEffect(() => {
    const again = () => setCameBack((n) => n + 1);
    window.addEventListener("popstate", again);
    return () => window.removeEventListener("popstate", again);
  }, []);
  useEffect(() => {
    if (!session || !ready || leaving || leaveQuestionOpen) return;
    if (stateSearch === (addressSearch ?? null)) {
      writing.current = null;
      return;
    }
    if (stateSearch && (!addressSearch || isOngoing(research))) {
      if (writing.current === stateSearch) return;
      if (window.location.pathname !== `/s/${sessionId}/results`) return;
      writing.current = stateSearch;
      void navigate({
        to: "/s/$sessionId/results",
        params: { sessionId },
        search: { search: stateSearch },
        replace: true,
      });
      return;
    }
    if (!addressSearch || (stateSearch && writing.current === stateSearch)) return;
    if (research.status === "briefReady" || stateSearch) {
      const rubric = rubricForBrief(interview, !researchServiceInfo().isMock) ?? [];
      attachResearch(researchInput(sessionId, session.brief, rubric), addressSearch);
    }
  }, [
    session,
    ready,
    leaving,
    leaveQuestionOpen,
    cameBack,
    interview,
    research,
    stateSearch,
    addressSearch,
    sessionId,
    navigate,
  ]);

  // Tab title: this page can be kept in the background, so the tab says how the search is doing.
  useEffect(() => {
    const title = TAB_TITLES[research.status];
    if (title) document.title = `${title} | ShopSense`;
  }, [research.status]);

  // Background stage.
  useEffect(() => {
    if (research.status !== "done") return;
    const s = loadSession(sessionId);
    if (s && s.stage !== "results") saveSession({ ...s, stage: "results" });
  }, [research.status, sessionId]);

  // Fetch the "Stop research?" dialog as soon as research runs, so it opens at once if the shopper tries to
  // stop or leave. It is kept in state rather than React.lazy, so there is no Suspense delay when it opens.
  const [StopResearchDialog, setStopResearchDialog] = useState<StopDialog | null>(null);
  const [dialogFailed, setDialogFailed] = useState(false);
  // Which run the question is about: a retry is a new run, so an open question can't come back by itself.
  const [askingAbout, setAskingAbout] = useState<number | null>(null);
  const researching = research.status === "researching" || research.status === "starting";
  useEffect(() => {
    if (!researching || StopResearchDialog || dialogFailed) return;
    let live = true;
    loadStopDialog().then(
      (m) => live && setStopResearchDialog(() => m.default),
      () => live && setDialogFailed(true),
    );
    return () => {
      live = false;
    };
  }, [researching, StopResearchDialog, dialogFailed]);

  /** Stopped by leaving the page: there is no screen left to say how it went, so a message does. */
  const stopAndLeave = useCallback(() => {
    setLeaving(true);
    void leaveResearch(sessionId).then((outcome) => {
      if (outcome === "stopped") toast("Search stopped");
      else if (outcome === "tooLate")
        toast("That search had nearly finished, so it wasn't stopped.");
      else toast("We couldn't stop that search. It will end by itself soon.");
    });
    blocker.proceed?.();
  }, [blocker, sessionId]);

  // A question that no longer applies. The search has ended behind it, so its results are worth staying for.
  // Or the dialog couldn't be loaded: the shopper has chosen to leave, so they aren't held here.
  useEffect(() => {
    if (blocker.status !== "blocked" || leaving) return;
    if (!researching) blocker.reset?.();
    else if (dialogFailed) stopAndLeave();
  }, [blocker, leaving, researching, dialogFailed, stopAndLeave]);

  // "Stop research" was pressed while the dialog was still loading, and it couldn't be loaded after all.
  useEffect(() => {
    if (!dialogFailed || askingAbout === null) return;
    setAskingAbout(null);
    if (askingAbout === research.runId) void stopResearch(sessionId);
  }, [dialogFailed, askingAbout, research.runId, sessionId]);

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
  const rows = weightRows(rubricForBrief(interview, !researchServiceInfo().isMock) ?? []);

  function backToBrief() {
    setLeaving(true);
    resetResearch(sessionId);
    void navigate({ to: "/s/$sessionId/brief", params: { sessionId } });
  }

  const summary = <BriefSummary session={session} rows={rows} />;

  return (
    <PageShell>
      <div className="mx-auto grid max-w-6xl gap-8 px-4 pb-20 pt-10 sm:px-6 md:pt-14 lg:grid-cols-[minmax(0,760px)_20rem] lg:justify-between">
        {/*
          At least one screen tall, so the brief summary and footer start below the fold while research runs
          and do not jump when the results replace the progress list (CLS was 0.31 on a phone).
        */}
        <div className="min-h-svh min-w-0">
          {research.status === "starting" && (
            <Starting category={category} message="Starting your search" />
          )}
          {(research.status === "attaching" ||
            (research.status === "briefReady" && addressSearch && !leaving)) && (
            <Starting category={category} message="Picking your search up again" />
          )}
          {/* One place for both states, so the screen reader's live region stays and hears the change. */}
          {(research.status === "researching" || research.status === "stopping") && (
            <Waiting
              category={category}
              events={research.events}
              startedAt={research.startedAt}
              connection={research.connection}
              stopNote={research.status === "researching" ? research.stopNote : null}
              stopping={research.status === "stopping"}
              onCheck={() => checkForResults(sessionId)}
              onStop={() => {
                // With no dialog to ask in, the button's own name is the question.
                if (dialogFailed) void stopResearch(sessionId);
                else setAskingAbout(research.runId);
              }}
            />
          )}
          {research.status === "stopped" && <Stopped sessionId={sessionId} />}
          {research.status === "timedOut" && (
            <Failure
              category={category}
              message="This search took too long, so we stopped waiting. Your brief is safe. Try again, or try again later."
              onRetry={() => retryResearch(sessionId)}
              onBack={backToBrief}
            />
          )}
          {research.status === "error" && research.kind === "notFound" && (
            <CantFind sessionId={sessionId} />
          )}
          {research.status === "error" && research.kind !== "notFound" && (
            <Failure
              category={category}
              message={failureMessage(research)}
              onRetry={() => retryResearch(sessionId)}
              onBack={backToBrief}
            />
          )}
          {research.status === "done" && (
            <Results
              result={research.result}
              category={category}
              sessionId={sessionId}
              stopCameTooLate={research.stopCameTooLate}
            />
          )}
          {research.status === "briefReady" && !addressSearch && !leaving && (
            <Gone sessionId={sessionId} hasBrief={Boolean(session.brief)} />
          )}
        </div>
        {research.status !== "briefReady" && <div className="min-w-0 lg:pt-2">{summary}</div>}
      </div>

      {blocker.status === "blocked" && researching && StopResearchDialog && (
        <StopResearchDialog leaving onKeep={() => blocker.reset?.()} onStop={stopAndLeave} />
      )}
      {askingAbout === research.runId &&
        research.status === "researching" &&
        StopResearchDialog && (
          <StopResearchDialog
            leaving={false}
            onKeep={() => setAskingAbout(null)}
            onStop={() => {
              setAskingAbout(null);
              void stopResearch(sessionId);
            }}
          />
        )}
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

function failureMessage(state: Extract<ResearchState, { status: "error" }>): string {
  if (state.kind === "busy" && state.message) return state.message;
  return state.kind === "network"
    ? "We lost the connection while researching. Your brief is safe."
    : "Something went wrong while researching. Your brief is safe.";
}

function ResearchHeading({ category }: { category: string }) {
  return (
    <h1
      id="research-title"
      tabIndex={-1}
      className="break-words font-serif text-4xl leading-tight text-foreground md:text-5xl"
    >
      Researching {category}s for you
    </h1>
  );
}

/** A short wait with nothing to show yet: the run is being started, or read after a reload. */
function Starting({ category, message }: { category: string; message: string }) {
  return (
    <section aria-labelledby="research-title">
      <ResearchHeading category={category} />
      <p role="status" className="mt-6 flex items-center gap-2 text-foreground md:text-lg">
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
        {message}
      </p>
    </section>
  );
}

const QUIET_WORDS: Record<Exclude<Connection, "live">, string> = {
  quiet: "We haven't heard from ShopSense for a minute. Your search may still be running.",
  checking: "Checking for results.",
  unreachable: "We still can't reach ShopSense. Check your connection and try again.",
};

function Waiting({
  category,
  events,
  startedAt,
  connection,
  stopNote,
  stopping,
  onCheck,
  onStop,
}: {
  category: string;
  events: ProgressEvent[];
  startedAt: number;
  connection: Connection;
  stopNote: StopNote;
  stopping: boolean;
  onCheck: () => void;
  onStop: () => void;
}) {
  const info = researchServiceInfo();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const quiet = connection === "live" ? null : QUIET_WORDS[connection];
  const tooLate = "This search has nearly finished, so it's too late to stop it.";
  const stoppingWords = "Stopping your search";
  let said = quiet ?? announcementFor(events);
  if (!quiet && stopping) said = stoppingWords;
  else if (!quiet && stopNote === "tooLate") said = tooLate;

  return (
    <section aria-labelledby="research-title">
      <ResearchHeading category={category} />
      <p className="mt-3 text-muted-foreground md:text-lg">
        This can take up to 2½ minutes. You can keep this page in the background.
      </p>
      {info.isMock && (
        <p className="mt-4">
          <StatusBadge variant="neutral">Preview: simulated progress from mock data</StatusBadge>
        </p>
      )}
      {quiet && (
        <div className="mt-6">
          <Notice
            tone="warn"
            action={
              <PillButton
                variant="outline"
                size="sm"
                className="min-h-11"
                onClick={onCheck}
                disabled={connection === "checking"}
                aria-busy={connection === "checking"}
              >
                Check for results
              </PillButton>
            }
          >
            {quiet}
          </Notice>
        </div>
      )}
      {stopNote === "failed" && (
        <div className="mt-6">
          <Notice tone="error">
            We couldn't stop this search, so it's still running. Please try again in a moment.
          </Notice>
        </div>
      )}
      {stopNote === "tooLate" && (
        <div className="mt-6">
          <Notice>{tooLate}</Notice>
        </div>
      )}

      <div className="mt-8 rounded-2xl border border-border bg-card p-6 shadow-soft sm:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="font-serif text-2xl text-foreground">What ShopSense is doing</h2>
          <p className="font-mono text-xs tabular-nums text-muted-foreground">
            <span className="sr-only">Elapsed time: </span>
            {formatElapsed(now - startedAt)}
          </p>
        </div>
        <ResearchProgress events={events} />
        <p className="sr-only" aria-live="polite">
          {said}
        </p>
        {stopping ? (
          <p className="mt-8 flex min-h-11 items-center gap-2 text-foreground">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
            {stoppingWords}
          </p>
        ) : stopNote === "tooLate" ? null : (
          <PillButton variant="outline" className="mt-8 min-h-11" onClick={onStop}>
            Stop research
          </PillButton>
        )}
      </div>
    </section>
  );
}

/** Takes focus when it appears: whatever had it, such as "Stop research", is gone. */
function useFocusOnArrival() {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  return heading;
}

function Stopped({ sessionId }: { sessionId: string }) {
  const heading = useFocusOnArrival();
  return (
    <section className="py-10">
      <h1
        ref={heading}
        tabIndex={-1}
        className="font-serif text-4xl text-foreground outline-none md:text-5xl"
      >
        Search stopped
      </h1>
      <p className="mt-4 text-lg text-muted-foreground">
        Your brief and answers are kept, so you can start again whenever you like.
      </p>
      <Link
        to="/s/$sessionId/brief"
        params={{ sessionId }}
        className={`${pillButton()} mt-8 min-h-11`}
      >
        Back to your brief
      </Link>
    </section>
  );
}

function CantFind({ sessionId }: { sessionId: string }) {
  const heading = useFocusOnArrival();
  return (
    <section className="py-10">
      <h1
        ref={heading}
        tabIndex={-1}
        className="font-serif text-4xl text-foreground outline-none md:text-5xl"
      >
        We can't find that search
      </h1>
      <p className="mt-4 text-lg text-muted-foreground">
        Finished searches are only kept for a while. Your brief is still here, so you can run the
        research again.
      </p>
      <Link
        to="/s/$sessionId/brief"
        params={{ sessionId }}
        className={`${pillButton()} mt-8 min-h-11`}
      >
        Back to your brief
      </Link>
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
      <ResearchHeading category={category} />
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
        There's no search to show on this page. Your brief is still here, so you can run the
        research again.
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
  stopCameTooLate,
}: {
  result: ResearchResult;
  category: string;
  sessionId: string;
  stopCameTooLate: boolean;
}) {
  const h1 = useRef<HTMLHeadingElement>(null);
  const [announce, setAnnounce] = useState("");
  const best = result.products[0];
  const ties = tieGroups(result.products);
  // A tie for first has no single best match, so every tied product stands together at the top.
  const firstTie = ties.find((t) => t.products[0] === best);
  const leaders = firstTie?.products ?? (best ? [best] : []);
  const others = result.products.filter((p) => !leaders.includes(p));

  // One "Where this comes from" drawer for the whole page. Only the latest opening's answer is shown: one that
  // arrives after the drawer was closed, or opened again for something else, is dropped.
  const [drawer, setDrawer] = useState<{
    target: SourceTarget;
    opener: HTMLElement;
    evidence: EvidenceView | null;
  } | null>(null);
  const [answers] = useState(latestOnly);
  const showSources: ShowSources = (product, criterion, opener) => {
    const token = answers.next();
    setDrawer({
      target: { productId: product.id, productName: product.name, criterion },
      opener,
      evidence: null,
    });
    void sourcesFor(
      result.searchId ?? null,
      product.id,
      criterion.criterionId,
      criterion.claims,
    ).then((evidence) => {
      if (answers.isCurrent(token)) setDrawer((d) => (d ? { ...d, evidence } : d));
    });
  };
  const closeSources = () => {
    const opener = drawer?.opener ?? null;
    answers.cancel();
    setDrawer(null);
    giveFocusBack(opener);
  };

  useEffect(() => {
    h1.current?.focus();
    const count = `Results ready. ${result.products.length} options`;
    setAnnounce(
      firstTie
        ? `${count}. ${listInWords(leaders.map((p) => p.name))} are too close to call.`
        : best
          ? `${count}, best match ${best.name}.`
          : "Results ready. No options matched.",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A tie's one card sits just above the first product in it.
  const ranked = (p: ProductResult, index: number, featured: boolean) => {
    const tie = ties.find((t) => t.products[0] === p);
    return (
      <li key={p.id}>
        {tie && <TooCloseToCall tie={tie} />}
        <ResultCard
          product={p}
          tiedWith={tiesFor(result.products, p)}
          featured={featured}
          index={index}
          onShowSources={showSources}
        />
      </li>
    );
  };

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
        {stopCameTooLate && (
          <Notice>
            Your search had nearly finished when you stopped it, so here are the results.
          </Notice>
        )}
        <NotSavedNotice saved={result.saved} />
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
          <ol className="mt-4 space-y-5" aria-label="Best match">
            {leaders.map((p, i) => ranked(p, i, true))}
          </ol>
          {others.length > 0 && (
            <>
              <h2 className="mt-12 font-serif text-2xl text-foreground">Other options</h2>
              <ol className="mt-4 space-y-5" start={leaders.length + 1} aria-label="Other options">
                {others.map((p, i) => ranked(p, leaders.length + i, false))}
              </ol>
            </>
          )}
        </>
      ) : (
        <p className="mt-10 text-lg text-muted-foreground">
          No option matched your brief this time.
        </p>
      )}

      {result.couldntCheck.length > 0 && (
        <section
          aria-labelledby="couldnt-check-title"
          className="mt-10 rounded-2xl border border-border bg-card px-4 py-4 sm:px-5"
        >
          <h2 id="couldnt-check-title" className="font-serif text-2xl text-foreground">
            Couldn't check ({result.couldntCheck.length})
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            We couldn't tell if these meet your must-haves, so they aren't ranked. You can look them
            up yourself.
          </p>
          <ul className="mt-2 divide-y divide-border">
            {result.couldntCheck.map((p) => (
              <CouldntCheckItem key={p.name} product={p} />
            ))}
          </ul>
        </section>
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
      </div>

      {pricesTime && (
        <p className="mt-10 border-t border-border pt-6 text-sm text-muted-foreground">
          Prices checked {pricesTime}. Prices can change. Check the store before you buy.
        </p>
      )}

      {drawer && (
        <SourceDrawer target={drawer.target} evidence={drawer.evidence} onClose={closeSources} />
      )}
    </section>
  );
}
