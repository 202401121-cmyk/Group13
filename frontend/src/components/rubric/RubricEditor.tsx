import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { PillButton } from "@/components/shopsense/ui";
import { ApiError, MESSAGES } from "@/lib/api/client";
import { MAX_EXACT, MIN_EXACT, SLIDER_STEP, parseExactValue } from "@/lib/interview/roughLevels";
import { MAX_EDITS, MAX_EXCLUSIONS, applyEdits, sameExclusion } from "@/lib/interview/rubricEdits";
import { trackRubricSave } from "@/lib/interview/rubricSaves";
import { getInterviewService } from "@/lib/interview/service";
import type {
  EditableRubric,
  ExclusionKind,
  ExclusionView,
  InterviewService,
  RubricEditView,
  RubricSeed,
  RuleView,
  WeightView,
} from "@/lib/interview/types";
import { ORIGIN_LABEL, normalizePercents } from "@/lib/results/rubric";
import { mustHavesOf } from "@/lib/results/view";

export type RubricApi = Pick<InterviewService, "loadRubric" | "editRubric" | "parseExclusions">;

const kindLabel = (kind: ExclusionKind) => kind.charAt(0).toUpperCase() + kind.slice(1);

const NOT_SAVED = "Your last change wasn't saved.";
const ASK_FOR_WORDS = "Type a brand, a model or a feature to leave out, such as “no Xiaomi”.";
const MAX_WORDS = 200;
const SLOW_MS = 400; // a wait longer than this gets a status message (D-45)

function messageOf(e: unknown): string {
  return e instanceof ApiError ? e.message : MESSAGES.other;
}

function quoted(words: string[]): string {
  return words.map((w) => `“${w}”`).join(", ");
}

/** The leave-outs in the order the shopper already sees them, new ones last: a chip never jumps. */
function inPlace(before: ExclusionView[], after: ExclusionView[]): ExclusionView[] {
  const rank = (e: ExclusionView) => {
    const at = before.findIndex((b) => sameExclusion(b, e));
    return at >= 0 ? at : before.length + after.indexOf(e);
  };
  return [...after].sort((a, b) => rank(a) - rank(b));
}

/** "Always" the shopper asked for that the saved rubric doesn't have (a leave-out from the request, for now). */
function alwaysNotKept(edits: RubricEditView[], rubric: EditableRubric): string[] {
  return edits.flatMap((edit) => {
    if (edit.kind !== "add_exclusion" || !edit.exclusion.always) return [];
    const kept = rubric.exclusions.find((e) => sameExclusion(e, edit.exclusion));
    return kept && !kept.always ? [kept.value] : [];
  });
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-border pt-6">
      <h2 className="font-serif text-2xl text-foreground">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/**
 * The rubric editor (US-09, US-10): switch a must-have to a preference and back, set each weight from 0 to 10, and
 * rule things out in words. Every change shows at once and is then saved; a failed save puts back what the backend
 * has and says so. Saves go one at a time, so they reach the backend in the order they were made.
 */
export function RubricEditor({
  sessionId,
  seed,
  initial,
  service = getInterviewService(),
  onChangeAnswer,
  onRubric,
}: {
  sessionId: string;
  seed: RubricSeed;
  /** The rubric as saved, loaded by the page before it draws, so nothing moves once it shows. */
  initial: EditableRubric;
  service?: RubricApi;
  /** Goes back to a criterion's question, as the brief's "Change" did. */
  onChangeAnswer?: ((criterionId: string) => void) | undefined;
  /** Hears the rubric as it is shown, so a search can start from it. */
  onRubric?: ((rubric: EditableRubric) => void) | undefined;
}) {
  const id = useId();
  const seedRef = useRef(seed);
  const [shown, setShown] = useState<EditableRubric>(initial);
  const saved = useRef<EditableRubric>(initial);
  const current = useRef<EditableRubric>(initial);
  const queue = useRef<Promise<boolean>>(Promise.resolve(true));
  const generation = useRef(0);
  const inFlight = useRef(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [words, setWords] = useState("");
  const [reading, setReading] = useState(false);
  const [readBack, setReadBack] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const onRubricRef = useRef(onRubric);
  onRubricRef.current = onRubric;

  function show(rubric: EditableRubric) {
    const next = { ...rubric, exclusions: inPlace(current.current.exclusions, rubric.exclusions) };
    current.current = next;
    setShown(next);
    onRubricRef.current?.(next);
  }

  useEffect(() => {
    onRubricRef.current?.(current.current);
  }, []);

  useEffect(() => {
    if (!focusId) return;
    document.getElementById(focusId)?.focus();
    setFocusId(null);
  }, [focusId]);

  /** Shows the edits at once, then saves them after any save before them. Resolves to whether they were saved. */
  function save(edits: RubricEditView[]): Promise<boolean> {
    const from = current.current;
    let next;
    try {
      next = applyEdits(from, edits);
    } catch (e) {
      setError(`${messageOf(e)} ${NOT_SAVED}`);
      return Promise.resolve(false);
    }
    show(next.rubric);
    setNote(next.note);
    setError(null);
    const gen = generation.current;
    inFlight.current += 1;
    const done = queue.current.then(async () => {
      try {
        if (gen !== generation.current) return false; // an earlier save failed: this one went with the rollback
        const answer = await service.editRubric(sessionId, edits);
        if (gen !== generation.current) return false;
        saved.current = answer.rubric;
        if (inFlight.current === 1) {
          show(answer.rubric); // the backend's word, once nothing newer is waiting to be saved
          setNote(answer.note);
        }
        const lost = alwaysNotKept(edits, answer.rubric);
        if (lost.length > 0) {
          setError(
            `“Always” can't be kept yet for ${quoted(lost)}, as it came from your request. It is still left out of this search.`,
          );
        }
        return true;
      } catch (e) {
        generation.current += 1;
        const after = generation.current;
        show(saved.current);
        setNote(null);
        setError(`${messageOf(e)} ${NOT_SAVED}`);
        // A save that timed out may have been stored all the same: show what the backend really has.
        service
          .loadRubric(sessionId, seedRef.current)
          .then((loaded) => {
            if (!loaded || after !== generation.current || inFlight.current > 0) return;
            saved.current = loaded;
            show(loaded);
          })
          .catch(() => {
            /* the message already says the change wasn't saved */
          });
        return false;
      } finally {
        inFlight.current -= 1;
      }
    });
    queue.current = done;
    trackRubricSave(sessionId, done);
    return done;
  }

  // Weights as they stand, with any number still being typed or slid.
  const typed = (w: WeightView): number => {
    const raw = drafts[w.key];
    if (raw === undefined) return w.weight;
    const parsed = parseExactValue(raw);
    return parsed.ok ? parsed.value : w.weight;
  };
  const percents = normalizePercents(shown.weights.map(typed));

  function draft(key: string, raw: string) {
    setDrafts((d) => ({ ...d, [key]: raw }));
    setFieldErrors((f) => {
      const { [key]: _gone, ...rest } = f;
      return rest;
    });
  }

  function commitWeight(w: WeightView) {
    const raw = drafts[w.key];
    if (raw === undefined) return;
    const parsed = parseExactValue(raw);
    if (!parsed.ok) {
      setFieldErrors((f) => ({ ...f, [w.key]: parsed.message }));
      return;
    }
    setDrafts((d) => {
      const { [w.key]: _done, ...rest } = d;
      return rest;
    });
    // With every weight at 0 the usual ones are shown, but 0 is what is stored: the shopper's number always goes.
    if (parsed.value !== w.weight || note) {
      void save([{ kind: "set_weight", key: w.key, weight: parsed.value }]);
    }
  }

  function switchRule(rule: RuleView, active: boolean) {
    if (rule.active !== active) void save([{ kind: "switch_rule", ruleId: rule.id, active }]);
  }

  function setAlways(e: ExclusionView, always: boolean) {
    void save([
      { kind: "remove_exclusion", exclusion: e },
      { kind: "add_exclusion", exclusion: { ...e, always } },
    ]);
  }

  function remove(e: ExclusionView, at: number) {
    const count = current.current.exclusions.length;
    const next = at < count - 1 ? at : at - 1; // the chip after it takes its place, else the one before
    setFocusId(next >= 0 ? `${id}-remove-${next}` : `${id}-rule-out`);
    void save([{ kind: "remove_exclusion", exclusion: e }]);
  }

  function ruleOut(event: FormEvent) {
    event.preventDefault();
    const text = words.trim();
    if (reading) return;
    if (!text) {
      setReadBack(ASK_FOR_WORDS);
      return;
    }
    setReading(true);
    setReadBack(null);
    const slow = setTimeout(() => setReadBack("Reading what you typed…"), SLOW_MS);
    const work = (async (): Promise<boolean> => {
      try {
        const read = await service.parseExclusions(text.slice(0, MAX_WORDS));
        clearTimeout(slow);
        const { planLeaveOuts } = await import("./leaveOuts");
        const plan = planLeaveOuts(read, current.current.exclusions);
        setReadBack(plan.said);
        if (plan.clear) setWords("");
        const saves = plan.saves.map((part) =>
          save(part.map((exclusion) => ({ kind: "add_exclusion" as const, exclusion }))),
        );
        const ok = (await Promise.all(saves)).every(Boolean);
        if (!ok) setWords(text); // not saved: the shopper's words come back, to try again
        return ok;
      } catch (e) {
        clearTimeout(slow);
        setReadBack(messageOf(e));
        return true; // nothing was read, so nothing is waiting to be saved
      } finally {
        setReading(false);
      }
    })();
    trackRubricSave(sessionId, work);
  }

  const budget = mustHavesOf({ ...seedRef.current.brief, mustHaves: [] });
  const alwaysNote = `${id}-always`;
  const ruleOutHelp = `${id}-rule-out-help`;
  const ruleOutSaid = `${id}-rule-out-said`;
  // The input fills its 44 px pill, so the whole pill is the target (D-45); the dot or tick is drawn beside it.
  const fillPill =
    "absolute -inset-px cursor-pointer appearance-none rounded-full focus-visible:outline-none";

  return (
    <div className="space-y-8">
      {error && (
        <p role="alert" className="rounded-2xl bg-destructive/10 px-4 py-3 text-foreground">
          {error}
        </p>
      )}

      <Section title="Must have">
        {budget.length > 0 && <p className="mb-4 text-foreground">{budget[0]}</p>}
        {shown.rules.length > 0 ? (
          <>
            <p className="mb-4 text-sm text-muted-foreground">
              A must-have rules out every product without it. A preference never rules anything out.
            </p>
            <div className="space-y-4">
              {shown.rules.map((rule) => (
                <fieldset key={rule.id} className="min-w-0">
                  <legend className="break-words font-medium text-foreground">{rule.words}</legend>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {(
                      [
                        ["Must-have", true],
                        ["Preference", false],
                      ] as const
                    ).map(([label, active]) => (
                      <label
                        key={label}
                        className="group relative inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border border-border px-4 text-sm text-foreground has-[:checked]:border-primary has-[:checked]:bg-primary/10 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring"
                      >
                        <input
                          type="radio"
                          name={`${id}-rule-${rule.id}`}
                          checked={rule.active === active}
                          onChange={() => switchRule(rule, active)}
                          className={fillPill}
                        />
                        <span
                          aria-hidden
                          className="grid h-4 w-4 place-items-center rounded-full border-2 border-muted-foreground group-has-[:checked]:border-primary"
                        >
                          <span className="h-2 w-2 rounded-full bg-primary opacity-0 group-has-[:checked]:opacity-100" />
                        </span>
                        {label}
                      </label>
                    ))}
                  </div>
                </fieldset>
              ))}
            </div>
          </>
        ) : (
          budget.length === 0 && (
            <p className="text-muted-foreground">No must-haves. Every option will be compared.</p>
          )
        )}
      </Section>

      {shown.weights.length > 0 && (
        <Section title="What counts most">
          <p className="text-sm text-muted-foreground">
            Set each one from {MIN_EXACT} to {MAX_EXACT}. Its share is its part of the total.
          </p>
          <p
            data-rubric-note
            aria-live="polite"
            aria-atomic="true"
            className={note ? "mt-2 text-sm font-medium text-foreground" : undefined}
          >
            {note ?? ""}
          </p>
          <ul className="mt-4 space-y-6">
            {shown.weights.map((w, i) => {
              const box = `${id}-weight-${w.key}`;
              const slider = `${box}-slider`;
              const err = `${box}-error`;
              const raw = drafts[w.key] ?? String(w.weight);
              return (
                <li key={w.key}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <span className="font-medium text-foreground">{w.title}</span>
                    <span className="flex items-center gap-3">
                      {w.origin && (
                        <span className="text-xs text-muted-foreground">
                          {ORIGIN_LABEL[w.origin]}
                        </span>
                      )}
                      {onChangeAnswer && (
                        <button
                          type="button"
                          onClick={() => onChangeAnswer(w.key)}
                          className="min-h-11 rounded-full px-2 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                          aria-label={`Change ${w.title}`}
                        >
                          Change
                        </button>
                      )}
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-[auto_1fr_auto] items-center gap-3">
                    <label htmlFor={box} className="sr-only">
                      {w.title}, from {MIN_EXACT} to {MAX_EXACT}
                    </label>
                    <input
                      id={box}
                      type="number"
                      inputMode="decimal"
                      min={MIN_EXACT}
                      max={MAX_EXACT}
                      step="any"
                      value={raw}
                      aria-invalid={fieldErrors[w.key] ? true : undefined}
                      aria-describedby={fieldErrors[w.key] ? err : undefined}
                      onChange={(e) => draft(w.key, e.target.value)}
                      onBlur={() => commitWeight(w)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitWeight(w);
                      }}
                      className="min-h-11 w-20 rounded-2xl border border-input bg-background px-3 py-2 text-base text-foreground focus-visible:outline-2"
                    />
                    <label htmlFor={slider} className="sr-only">
                      {w.title}, slide from {MIN_EXACT} to {MAX_EXACT}
                    </label>
                    <input
                      id={slider}
                      type="range"
                      min={MIN_EXACT}
                      max={MAX_EXACT}
                      step={SLIDER_STEP}
                      value={typed(w)}
                      onChange={(e) => draft(w.key, e.target.value)}
                      onPointerUp={() => commitWeight(w)}
                      onKeyUp={() => commitWeight(w)}
                      onBlur={() => commitWeight(w)}
                      className="h-11 w-full min-w-0 cursor-pointer rounded-full accent-primary focus-visible:outline-2 focus-visible:outline-offset-2"
                    />
                    <span className="w-24 text-right text-sm tabular-nums text-foreground">
                      <span data-share={w.key} className="font-semibold">
                        {percents[i]}%
                      </span>{" "}
                      <span className="text-muted-foreground">share</span>
                    </span>
                  </div>
                  {fieldErrors[w.key] && (
                    <p id={err} role="alert" className="mt-1 text-sm font-medium text-destructive">
                      {fieldErrors[w.key]}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      <Section title="Leave out">
        {shown.exclusions.length > 0 ? (
          <ul aria-label="Things to leave out" className="space-y-2">
            {shown.exclusions.map((e, i) => {
              const check = `${id}-always-${i}`;
              return (
                <li
                  key={`${e.kind}:${e.value.toLowerCase()}`}
                  data-exclusion={e.value}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl border border-border bg-card py-1 pl-4 pr-1"
                >
                  <span className="min-w-0 break-words font-medium text-foreground">{e.value}</span>
                  <span className="text-xs text-muted-foreground">{kindLabel(e.kind)}</span>
                  <span className="ml-auto flex items-center">
                    <label
                      htmlFor={check}
                      className="group relative inline-flex min-h-11 min-w-11 cursor-pointer items-center gap-2 rounded-full px-3 text-sm text-foreground has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring"
                    >
                      <input
                        id={check}
                        type="checkbox"
                        checked={e.always}
                        aria-describedby={alwaysNote}
                        onChange={(ev) => setAlways(e, ev.target.checked)}
                        className={fillPill}
                      />
                      <span
                        aria-hidden
                        className="grid h-4 w-4 place-items-center rounded border-2 border-muted-foreground group-has-[:checked]:border-primary group-has-[:checked]:bg-primary"
                      >
                        <span className="mb-0.5 h-2 w-1 rotate-45 border-r-2 border-b-2 border-primary-foreground opacity-0 group-has-[:checked]:opacity-100" />
                      </span>
                      Always<span className="sr-only"> leave out {e.value}</span>
                    </label>
                    <button
                      id={`${id}-remove-${i}`}
                      type="button"
                      onClick={() => remove(e, i)}
                      aria-label={`Remove ${e.value}`}
                      className="grid h-11 w-11 place-items-center rounded-full text-foreground hover:bg-primary/10 focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      <X className="h-4 w-4" aria-hidden />
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-muted-foreground">Nothing is left out yet.</p>
        )}
        <p id={alwaysNote} className="mt-2 text-xs text-muted-foreground">
          “Always” applies to this search for now. ShopSense will remember it for later searches
          once you can sign in.
        </p>
        <form onSubmit={ruleOut} noValidate className="mt-5">
          <label htmlFor={`${id}-rule-out`} className="text-sm font-medium text-foreground">
            Rule something out
          </label>
          <div className="mt-2 flex gap-2">
            <input
              id={`${id}-rule-out`}
              value={words}
              maxLength={MAX_WORDS}
              placeholder="e.g. no Xiaomi, not touchscreen"
              onChange={(e) => setWords(e.target.value)}
              aria-describedby={`${ruleOutHelp} ${ruleOutSaid}`}
              className="min-h-11 w-full min-w-0 rounded-2xl border border-input bg-background px-4 py-2 text-base text-foreground focus-visible:outline-2"
            />
            <PillButton type="submit" variant="outline" disabled={reading} aria-busy={reading}>
              Add
            </PillButton>
          </div>
          <p id={ruleOutHelp} className="mt-1 text-xs text-muted-foreground">
            A brand, a model or a feature, in your own words.
          </p>
          <p
            id={ruleOutSaid}
            role="status"
            className={readBack ? "mt-1 text-sm text-foreground" : undefined}
          >
            {readBack ?? ""}
          </p>
        </form>
      </Section>
    </div>
  );
}
