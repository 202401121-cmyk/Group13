import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Info, RefreshCw } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { BriefPanel } from "@/components/request/BriefPanel";
import { InlineError, MissingSession, PageLoading } from "@/components/interview/shared";
import { AnswersSoFar, Completion, Composer, FocusCard } from "@/components/interview/Interview";
import { EXACT_INPUT_ID } from "@/components/interview/ExactValue";
import { useInterview } from "@/lib/interview/useInterview";
import { counts } from "@/lib/interview/reducer";
import { briefCriteria, progressOf } from "@/lib/interview/view";

export const Route = createFileRoute("/s/$sessionId/questions")({
  head: () => ({
    meta: [
      { title: "A few short questions — ShopSense" },
      {
        name: "description",
        content: "Answer up to 10 short questions so ShopSense knows what matters to you.",
      },
      { property: "og:title", content: "A few short questions — ShopSense" },
      {
        property: "og:description",
        content: "Tell ShopSense what matters to you, one question at a time.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "robots", content: "noindex" },
    ],
  }),
  component: QuestionsPage,
});

function QuestionsPage() {
  const { sessionId } = Route.useParams();
  const { session, ready, state, dispatch, thinking, retryCriteria } = useInterview(sessionId);
  const [draft, setDraft] = useState("");
  const [announce, setAnnounce] = useState("");
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const cardRef = useRef<HTMLElement>(null);
  const typedRef = useRef(false);
  const prevPhase = useRef(state.phase);

  // Begin (or resume) as soon as criteria are available.
  useEffect(() => {
    if (ready && state.phase === "criteria-ready") dispatch({ type: "START" });
  }, [ready, state.phase, dispatch]);

  // Clear the composer only after a typed answer succeeded; keep focus in it.
  useEffect(() => {
    const was = prevPhase.current;
    prevPhase.current = state.phase;
    if (was === "sending" && (state.phase === "asking" || state.phase === "finished")) {
      if (typedRef.current) {
        setDraft("");
        if (state.phase === "asking") composerRef.current?.focus();
      }
      typedRef.current = false;
    }
  }, [state.phase]);

  // Live announcements.
  useEffect(() => {
    if (state.phase === "finished")
      setAnnounce([state.notice, "Your brief is ready."].filter(Boolean).join(" "));
    else if (state.phase === "error") setAnnounce(state.error ?? "Something went wrong.");
    else if (state.phase === "sending" && thinking) setAnnounce("Thinking about your answer.");
    else if (state.phase === "asking" && state.step) {
      setAnnounce(
        [
          state.notice ?? "",
          state.laterChanged ? "Later questions were updated." : "",
          state.step.note ?? "",
          state.step.question ?? "",
        ]
          .filter(Boolean)
          .join(" "),
      );
    }
  }, [state.phase, state.step, state.error, state.laterChanged, state.notice, thinking]);

  const busy = state.phase === "sending" || state.phase === "starting";
  const asking = state.phase === "asking" || state.phase === "sending" || state.phase === "error";
  const step = state.step;

  // Number keys 1–6 pick a quick reply when focus is outside the composer.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const canSend = state.phase === "asking" || state.phase === "error";
      if (!canSend || !step || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
      if (document.getElementById(EXACT_INPUT_ID)) return; // the buttons are hidden while the exact box is open
      const n = Number(e.key);
      if (n >= 1 && n <= 6 && step.replies[n - 1]) {
        e.preventDefault();
        typedRef.current = false;
        dispatch({ type: "SEND", text: step.replies[n - 1]! });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state.phase, step, dispatch]);

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

  const idx = step?.criterionId ? state.criteria.findIndex((c) => c.id === step.criterionId) : -1;
  const progress = progressOf(state);
  const selected =
    step?.criterionId && state.revising === step.criterionId
      ? (state.answers[step.criterionId]?.value ?? null)
      : null;

  function goBack(id: string) {
    dispatch({ type: "GO_BACK", criterionId: id });
    requestAnimationFrame(() => {
      const el = document.getElementById("current-question");
      el?.scrollIntoView({
        block: "start",
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
      el?.focus({ preventScroll: true });
    });
  }

  return (
    <PageShell>
      <div className="mx-auto grid max-w-6xl gap-8 px-4 pb-44 pt-10 sm:px-6 md:pt-14 lg:grid-cols-[minmax(0,1fr)_20rem] lg:pb-16">
        {/*
          At least one screen tall, so the brief panel and footer below start off screen and do not jump each
          time a new question is a different height (CLS was 0.38 on a phone).
        */}
        <div className="flex min-h-svh min-w-0 flex-col gap-5">
          <div>
            <Link
              to="/s/$sessionId/criteria"
              params={{ sessionId }}
              className="inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden /> Criteria
            </Link>
            {state.phase !== "finished" && (
              <div className="mt-2 flex items-baseline justify-between gap-3">
                <p className="font-serif text-2xl text-foreground">
                  Question {idx >= 0 ? idx + 1 : progress.settled + 1}{" "}
                  <span className="font-sans text-sm text-muted-foreground">up to 10</span>
                </p>
                <span className="font-mono text-xs text-muted-foreground">
                  {progress.settled} of {progress.total} settled
                </span>
              </div>
            )}
            <div
              className="mt-3 h-1 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label="Criteria settled"
              aria-valuemin={0}
              aria-valuemax={progress.total}
              aria-valuenow={progress.settled}
            >
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-500 motion-reduce:transition-none"
                style={{
                  width: `${progress.total ? (progress.settled / progress.total) * 100 : 0}%`,
                }}
              />
            </div>
          </div>

          {state.laterChanged && state.phase !== "sending" && (
            <p
              role="status"
              className="flex items-center gap-2 rounded-2xl bg-champagne px-4 py-3 text-sm text-champagne-foreground"
            >
              <RefreshCw className="h-4 w-4 shrink-0" aria-hidden /> Later questions were updated
            </p>
          )}

          {state.notice && (
            <p
              role="status"
              className="flex items-center gap-2 rounded-2xl bg-champagne px-4 py-3 text-sm text-champagne-foreground"
            >
              <Info className="h-4 w-4 shrink-0" aria-hidden /> {state.notice}
            </p>
          )}

          {(state.phase === "loading-criteria" || state.phase === "starting") && !step && (
            <div
              className="space-y-3 rounded-2xl border border-border bg-card p-8"
              aria-busy="true"
              aria-label="Loading question"
            >
              <div className="skeleton h-8 w-3/4" />
              <div className="grid gap-2 sm:grid-cols-3">
                <div className="skeleton h-12" />
                <div className="skeleton h-12" />
                <div className="skeleton h-12" />
              </div>
            </div>
          )}
          {state.phase === "criteria-error" && (
            <InlineError
              message={state.error ?? "We couldn't load the criteria."}
              onRetry={retryCriteria}
            />
          )}
          {state.phase === "error" && !state.pending && !step && (
            <InlineError
              message={state.error ?? "Something went wrong."}
              onRetry={() => dispatch({ type: "RETRY" })}
            />
          )}

          {state.phase === "finished" && (
            <Completion sessionId={sessionId} counts={counts(state.answers)} />
          )}

          {asking && step && step.kind !== "finished" && (
            <FocusCard
              key={`${step.criterionId}-${step.kind}-${step.question}`}
              ref={cardRef}
              step={step}
              number={idx + 1}
              selected={selected}
              busy={busy}
              thinking={thinking}
              error={state.phase === "error" ? state.error : null}
              onReply={(text) => {
                typedRef.current = false;
                dispatch({ type: "SEND", text });
              }}
              onSkip={() => {
                typedRef.current = false;
                dispatch({ type: "SEND", text: null });
              }}
              onRetry={() => dispatch({ type: "RETRY" })}
            />
          )}

          <AnswersSoFar state={state} onChange={goBack} />

          {state.phase !== "finished" && (
            <>
              <a
                href="#current-question"
                className="sr-only focus:not-sr-only focus:inline-flex focus:min-h-11 focus:items-center focus:self-start focus:rounded-full focus:bg-card focus:px-4 focus:text-sm focus:text-primary"
              >
                Jump to question
              </a>
              <Composer
                ref={composerRef}
                value={draft}
                onChange={setDraft}
                busy={busy}
                disabled={!asking || !step}
                onSend={() => {
                  if (!draft.trim()) return;
                  typedRef.current = true;
                  dispatch({ type: "SEND", text: draft });
                }}
              />
            </>
          )}
        </div>
        <BriefPanel brief={session.brief} criteria={briefCriteria(state)} progress={progress} />
      </div>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announce}
      </p>
    </PageShell>
  );
}
