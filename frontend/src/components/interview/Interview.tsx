import { forwardRef, useState, type KeyboardEvent } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronDown, CheckCircle2, Loader2, Send } from "lucide-react";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { InlineError, StatusChip } from "./shared";
import { noteContainsQuestion } from "@/lib/interview/view";
import { statusOf, type InterviewState } from "@/lib/interview/reducer";
import { ANSWER_COUNTER_FROM, MAX_ANSWER_LENGTH, type Step } from "@/lib/interview/types";
import { cn } from "@/lib/utils";

/* ---------- Focus card ---------- */

export const FocusCard = forwardRef<
  HTMLElement,
  {
    step: Step;
    number: number;
    selected: string | null;
    busy: boolean;
    thinking: boolean;
    error: string | null;
    onReply: (text: string) => void;
    onSkip: () => void;
    onRetry: () => void;
  }
>(function FocusCard(
  { step, number, selected, busy, thinking, error, onReply, onSkip, onRetry },
  ref,
) {
  const [whyOpen, setWhyOpen] = useState(false);
  const hideQuestion = noteContainsQuestion(step.note, step.question);
  return (
    <section
      ref={ref}
      id="current-question"
      tabIndex={-1}
      aria-labelledby="question-text"
      className="rounded-2xl border border-border bg-card p-6 shadow-soft outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-8"
    >
      {step.note && (
        <p className="mb-5 rounded-2xl bg-primary-soft px-4 py-3 text-sm leading-relaxed text-primary-soft-foreground">
          {step.note}
        </p>
      )}
      {step.kind === "rephrase" && (
        <p className="mb-2 font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
          Asked another way
        </p>
      )}
      <h2
        id="question-text"
        className={cn(
          "font-serif text-2xl leading-snug text-foreground sm:text-3xl",
          hideQuestion && "sr-only",
        )}
      >
        {step.question}
      </h2>

      {step.why && (
        <div className="mt-3">
          <button
            type="button"
            aria-expanded={whyOpen}
            onClick={() => setWhyOpen(!whyOpen)}
            className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-primary"
          >
            Why this question?
            <ChevronDown
              className={cn("h-4 w-4 transition-transform", whyOpen && "rotate-180")}
              aria-hidden
            />
          </button>
          {whyOpen && <p className="max-w-prose text-sm text-muted-foreground">{step.why}</p>}
        </div>
      )}

      <div
        role="group"
        aria-label={`Quick replies for question ${number}`}
        className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3"
      >
        {step.replies.slice(0, 6).map((r, i) => {
          const isSel = selected === r;
          return (
            <button
              key={r}
              type="button"
              disabled={busy}
              aria-pressed={isSel}
              onClick={() => onReply(r)}
              className={cn(
                "flex min-h-12 items-center gap-3 rounded-2xl border px-4 py-3 text-left font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                isSel
                  ? "border-primary bg-primary-soft text-primary-soft-foreground"
                  : "border-border bg-surface text-foreground hover:border-primary/50 hover:bg-primary-soft/50",
              )}
            >
              <kbd
                className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-border bg-background text-xs text-muted-foreground"
                aria-hidden
              >
                {i + 1}
              </kbd>
              <span className="min-w-0 break-words">{r}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={onSkip}
          className="min-h-11 rounded-full px-3 text-sm font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline disabled:opacity-50"
        >
          Skip this question
        </button>
        {busy && thinking && (
          <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />{" "}
            Thinking about your answer
          </span>
        )}
      </div>
      {error && (
        <div className="mt-4">
          <InlineError message={error} onRetry={onRetry} />
        </div>
      )}
    </section>
  );
});

/* ---------- Answers so far ---------- */

export function AnswersSoFar({
  state,
  onChange,
}: {
  state: InterviewState;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rows = state.criteria.filter(
    (c) => state.answers[c.id] || state.pending?.criterionId === c.id,
  );
  const locked = state.phase === "sending" || state.phase === "starting";
  return (
    <section
      aria-labelledby="answers-title"
      className="rounded-2xl border border-border bg-surface p-5 sm:p-6"
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls="answers-list"
        className="flex min-h-11 w-full items-center justify-between gap-2 text-left lg:pointer-events-none"
      >
        <h2 id="answers-title" className="font-serif text-xl text-foreground">
          Answers so far{" "}
          <span className="font-sans text-sm text-muted-foreground">({rows.length})</span>
        </h2>
        <ChevronDown
          className={cn(
            "h-5 w-5 text-muted-foreground transition-transform lg:hidden",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>
      <div id="answers-list" className={cn(open ? "block" : "hidden", "lg:block")}>
        {rows.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Your answers will appear here.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border">
            {rows.map((c) => {
              const status = statusOf(state, c.id);
              const a = state.answers[c.id];
              const pendingText =
                state.pending?.criterionId === c.id ? (state.pending.text ?? "Skipping") : null;
              const value =
                status === "pending"
                  ? pendingText
                  : a?.status === "remembered"
                    ? `Remembered: ${a.value}`
                    : a?.status === "skipped"
                      ? "Skipped"
                      : a?.value;
              const current = state.step?.criterionId === c.id && state.phase !== "finished";
              return (
                <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3">
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-foreground">{c.title}</span>
                    <span className="block break-words text-sm text-muted-foreground">{value}</span>
                  </span>
                  <StatusChip status={status} />
                  {status !== "pending" && !current && (
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => onChange(c.id)}
                      aria-label={`${status === "skipped" ? "Answer" : "Change"} ${c.title}`}
                      className="min-h-11 rounded-full px-3 text-sm font-medium text-primary hover:bg-primary-soft disabled:opacity-40"
                    >
                      {status === "skipped" ? "Answer" : "Change"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

/* ---------- Composer ---------- */

export const Composer = forwardRef<
  HTMLTextAreaElement,
  {
    value: string;
    onChange: (v: string) => void;
    onSend: () => void;
    busy: boolean;
    disabled: boolean;
  }
>(function Composer({ value, onChange, onSend, busy, disabled }, ref) {
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Never submit mid-IME composition (e.g. Hindi, Gujarati keyboards).
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    if (!busy) onSend();
  }
  const canSend = !busy && !disabled && value.trim().length > 0;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface/95 px-4 pt-3 pb-safe shadow-soft backdrop-blur lg:static lg:rounded-2xl lg:border lg:p-4"
    >
      <label htmlFor="answer-input" className="sr-only">
        Or type your answer
      </label>
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          id="answer-input"
          ref={ref}
          rows={1}
          value={value}
          maxLength={MAX_ANSWER_LENGTH}
          readOnly={busy}
          aria-disabled={busy || disabled}
          disabled={disabled}
          placeholder="Or type your answer"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={(e) => setTimeout(() => e.target.scrollIntoView({ block: "nearest" }), 250)}
          className="max-h-40 min-h-11 flex-1 resize-none rounded-2xl border border-input bg-background px-4 py-2.5 text-base text-foreground placeholder:text-muted-foreground focus-visible:outline-2 disabled:opacity-60"
        />
        <PillButton type="submit" disabled={!canSend} className="h-11 shrink-0" aria-label="Send">
          <Send className="h-4 w-4" aria-hidden /> <span className="hidden sm:inline">Send</span>
        </PillButton>
      </div>
      {value.length >= ANSWER_COUNTER_FROM && (
        <p
          className={cn(
            "mx-auto mt-1 max-w-3xl text-right text-xs",
            value.length >= MAX_ANSWER_LENGTH ? "text-destructive" : "text-muted-foreground",
          )}
          aria-live="polite"
        >
          {value.length} / {MAX_ANSWER_LENGTH}
        </p>
      )}
    </form>
  );
});

/* ---------- Completion ---------- */

export function Completion({
  sessionId,
  counts,
}: {
  sessionId: string;
  counts: { answered: number; skipped: number; remembered: number };
}) {
  return (
    <section
      id="current-question"
      tabIndex={-1}
      aria-labelledby="done-title"
      className="rounded-2xl border border-border bg-card p-8 shadow-soft outline-none animate-in fade-in slide-in-from-bottom-2 motion-reduce:animate-none"
    >
      <CheckCircle2 className="h-10 w-10 text-primary" aria-hidden />
      <h2 id="done-title" className="mt-4 font-serif text-3xl text-foreground sm:text-4xl">
        Your brief is ready
      </h2>
      <dl className="mt-6 grid grid-cols-3 gap-3">
        {(
          [
            ["Answered", counts.answered],
            ["Skipped", counts.skipped],
            ["Remembered", counts.remembered],
          ] as const
        ).map(([k, v]) => (
          <div key={k} className="flex flex-col-reverse rounded-2xl bg-primary-soft/60 p-4">
            <dt className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
              {k}
            </dt>
            <dd className="font-serif text-3xl text-foreground">{v}</dd>
          </div>
        ))}
      </dl>
      <Link
        to="/s/$sessionId/brief"
        params={{ sessionId }}
        className={`${pillButton({ size: "lg" })} mt-8 min-h-11`}
      >
        Review your brief
      </Link>
    </section>
  );
}
