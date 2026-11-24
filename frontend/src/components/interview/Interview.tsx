import { forwardRef, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronDown, CheckCircle2, Loader2, Send } from "lucide-react";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { ExactValue } from "./ExactValue";
import { InlineError, StatusChip } from "./shared";
import { TermHelp } from "./TermHelp";
import { rememberedFromText, stepDisplay } from "@/lib/interview/view";
import { statusOf, type InterviewState } from "@/lib/interview/reducer";
import { exactFromAnswer, levelOf } from "@/lib/interview/roughLevels";
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
  // An earlier number reopens in the box, and anything else on the buttons.
  const earlierNumber = exactFromAnswer(selected);
  const earlierLevel = selected === null ? null : levelOf(selected);
  const [exactOpen, setExactOpen] = useState(earlierNumber !== null);
  const exactSwitch = useRef<HTMLButtonElement>(null);
  const backFromExact = useRef(false);
  useEffect(() => {
    if (!exactOpen && backFromExact.current) exactSwitch.current?.focus();
    backFromExact.current = false;
  }, [exactOpen]);
  const shown = stepDisplay(step);
  return (
    <section
      ref={ref}
      id="current-question"
      tabIndex={-1}
      aria-labelledby="question-text"
      className="rounded-2xl border border-border bg-card p-6 shadow-soft outline-hidden focus-visible:ring-2 focus-visible:ring-ring sm:p-8"
    >
      {shown.note && (
        <p
          data-step-note
          className="mb-5 rounded-2xl bg-primary-soft px-4 py-3 text-sm leading-relaxed text-primary-soft-foreground"
        >
          {shown.note}
        </p>
      )}
      {shown.label && (
        <p className="mb-2 font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
          {shown.label}
        </p>
      )}
      <h2
        id="question-text"
        className="font-serif text-2xl leading-snug text-foreground sm:text-3xl"
      >
        {shown.heading}
        {shown.question && <span className="sr-only"> ({shown.question})</span>}
      </h2>
      {step.why && (
        <p data-why className="mt-2 max-w-prose text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Why this question: </span>
          {step.why}
        </p>
      )}

      <TermHelp key={step.questionId ?? step.criterionId} terms={step.terms} />

      {exactOpen ? (
        <ExactValue
          initial={earlierNumber}
          busy={busy}
          onSend={(value) => onReply(String(value))}
          onUseButtons={() => {
            backFromExact.current = true;
            setExactOpen(false);
          }}
        />
      ) : (
        <div
          role="group"
          aria-label={`Quick replies for question ${number}`}
          className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3"
        >
          {step.replies.slice(0, 6).map((r, i) => {
            const isSel = selected === r || (earlierLevel !== null && earlierLevel === levelOf(r));
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
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {!exactOpen && (
          <button
            ref={exactSwitch}
            type="button"
            disabled={busy}
            onClick={() => setExactOpen(true)}
            className="min-h-11 rounded-full px-3 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 disabled:opacity-60"
          >
            Set an exact value
          </button>
        )}
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
  onForget,
}: {
  state: InterviewState;
  onChange: (id: string) => void;
  /** Forgets a remembered preference (US-06). Without it, no Forget button. */
  onForget?: ((preferenceId: string) => void) | undefined;
}) {
  const [open, setOpen] = useState(false);
  const rows = state.criteria.filter(
    (c) => state.answers[c.id] || state.pending?.criterionId === c.id,
  );
  const locked = state.phase === "sending" || state.phase === "starting";
  // Each row's words keep at least 9rem: on a phone the buttons wrap below them, never squeeze a word in two.
  // A remembered weight shows on its criterion's row; a must-have or leave-out (or a weight with no row) on its own.
  const shownOnRows = new Set(
    rows.filter((c) => state.answers[c.id]?.status === "remembered").map((c) => c.id),
  );
  const memoryOf = (id: string) =>
    state.remembered.find((r) => r.kind === "weight" && r.criterionId === id);
  // A remembered weight shows only on its own row while it stands; once changed, the shopper's answer replaces it.
  const alsoRemembered = state.remembered.filter(
    (r) =>
      r.kind !== "weight" ||
      (!shownOnRows.has(r.criterionId ?? "") &&
        !state.criteria.some((c) => c.id === r.criterionId)),
  );
  // A reply waiting to be tried again goes first.
  const forgetLocked = locked || state.pending !== null;
  const forgetButton = (preferenceId: string, name: string) =>
    onForget && (
      <button
        type="button"
        disabled={forgetLocked}
        onClick={() => onForget(preferenceId)}
        aria-label={`Forget ${name}`}
        className="min-h-11 rounded-full px-3 text-sm font-medium text-primary hover:bg-primary-soft hover:text-primary-soft-foreground disabled:opacity-40"
      >
        Forget
      </button>
    );
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
        {rows.length === 0 && alsoRemembered.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Your answers will appear here.</p>
        ) : rows.length === 0 ? null : (
          <ul className="mt-2 divide-y divide-border">
            {rows.map((c) => {
              const status = statusOf(state, c.id);
              const a = state.answers[c.id];
              const pendingText =
                state.pending?.criterionId === c.id ? (state.pending.text ?? "Skipping") : null;
              const value =
                status === "pending" ? pendingText : a?.status === "skipped" ? "Skipped" : a?.value;
              const current = state.step?.criterionId === c.id && state.phase !== "finished";
              const memory = a?.status === "remembered" ? memoryOf(c.id) : undefined;
              return (
                <li
                  key={c.id}
                  data-remembered={memory ? "" : undefined}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3"
                >
                  <span className="min-w-36 flex-1">
                    <span className="block text-sm font-medium text-foreground">{c.title}</span>
                    <span className="block break-words text-sm text-muted-foreground">{value}</span>
                    {memory && (
                      <span data-remembered-from className="block text-xs text-muted-foreground">
                        {rememberedFromText(memory.since)}
                      </span>
                    )}
                  </span>
                  <StatusChip status={status} />
                  {status !== "pending" && !current && (
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => onChange(c.id)}
                      aria-label={`${status === "skipped" ? "Answer now:" : "Change"} ${c.title}`}
                      className="min-h-11 rounded-full px-3 text-sm font-medium text-primary hover:bg-primary-soft hover:text-primary-soft-foreground disabled:opacity-40"
                    >
                      {status === "skipped" ? "Answer now" : "Change"}
                    </button>
                  )}
                  {memory && forgetButton(memory.preferenceId, c.title)}
                </li>
              );
            })}
          </ul>
        )}
        {alsoRemembered.length > 0 && (
          <section aria-labelledby="also-remembered" className="mt-3">
            <h3 id="also-remembered" className="text-sm font-semibold text-foreground">
              Also remembered from your earlier searches
            </h3>
            <ul className="mt-1 divide-y divide-border">
              {alsoRemembered.map((r) => (
                <li
                  key={r.preferenceId}
                  data-remembered=""
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3"
                >
                  <span className="min-w-36 flex-1">
                    <span className="block break-words text-sm text-foreground">{r.text}</span>
                    <span data-remembered-from className="block text-xs text-muted-foreground">
                      {rememberedFromText(r.since)}
                    </span>
                  </span>
                  <StatusChip status="remembered" />
                  {forgetButton(r.preferenceId, r.text)}
                </li>
              ))}
            </ul>
          </section>
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
  message = null,
  skipped = [],
  onAnswer,
}: {
  sessionId: string;
  counts: { answered: number; skipped: number; remembered: number };
  /** The backend's last words, as written ("Thanks, that's everything I need.", with any skip note). */
  message?: string | null;
  /** Skipped questions, each with "Answer now" (US-39: marked so they can be answered later). */
  skipped?: { id: string; title: string }[];
  onAnswer?: (id: string) => void;
}) {
  return (
    <section
      id="current-question"
      tabIndex={-1}
      aria-labelledby="done-title"
      className="rounded-2xl border border-border bg-card p-8 shadow-soft outline-none"
    >
      <CheckCircle2 className="h-10 w-10 text-primary" aria-hidden />
      <h2 id="done-title" className="mt-4 font-serif text-3xl text-foreground sm:text-4xl">
        Your brief is ready
      </h2>
      {message && <p className="mt-3 text-foreground">{message}</p>}
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
      {skipped.length > 0 && onAnswer && (
        <section aria-labelledby="skipped-title" className="mt-6">
          <h3 id="skipped-title" className="text-sm font-semibold text-foreground">
            Skipped questions
          </h3>
          <ul className="mt-2 divide-y divide-border">
            {skipped.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="min-w-0 text-sm text-foreground">{c.title}</span>
                <button
                  type="button"
                  onClick={() => onAnswer(c.id)}
                  aria-label={`Answer now: ${c.title}`}
                  className="min-h-11 rounded-full px-3 text-sm font-medium text-primary hover:bg-primary-soft hover:text-primary-soft-foreground"
                >
                  Answer now
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
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
