import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Bookmark } from "lucide-react";
import { PillButton } from "@/components/shopsense/ui";
import { getMemoryService } from "@/lib/account/memoryService";
import {
  MEMORY_MESSAGES,
  MemoryError,
  TEXT_MAX,
  UNDO_MS,
  WEIGHT_MAX,
  WEIGHT_MIN,
  type HeldForget,
  type MemoryEdit,
  type MemoryFile,
  type RememberedItem,
} from "@/lib/account/memory";
import { categoryLabel, changedOn, KIND_LABELS, weightLabel } from "@/lib/account/memoryFormat";
import { getAuthService } from "@/lib/auth/service";
import { cn } from "@/lib/utils";

/** Loaded only when "Forget everything" is pressed, so the dialog library stays out of the page's first load. */
const ForgetAllDialog = lazy(() => import("./ForgetAllDialog"));

export const EMPTY_MEMORY =
  "Nothing remembered yet. Finish an interview while signed in, and your priorities show here.";

type Load =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; items: RememberedItem[] };

/** An item forgotten on screen: "waiting" while Undo is offered, "sending" once its delete has gone. */
type Hidden = Map<string, "waiting" | "sending">;

const messageOf = (e: unknown) => (e instanceof MemoryError ? e.message : MEMORY_MESSAGES.unknown);

/** Focus an element once React has drawn it. */
function focusSoon(id: string) {
  requestAnimationFrame(() => document.getElementById(id)?.focus());
}

/** Saves the export as a file, with the browser's own download. */
function save(file: MemoryFile) {
  const url = URL.createObjectURL(new Blob([file.text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

/**
 * "What we remember" (US-11): every remembered priority, must-have and leave-out, in plain words with its category.
 * A priority's words and weight can be changed, a must-have switched off or on, and any item forgotten. Forget waits
 * 5 seconds with Undo before anything is sent, so Undo restores the item without a call. The export downloads as a
 * .json file, and "Forget everything" asks first. A preference already gone (404) or changed meanwhile (409) refreshes
 * the list; an unreachable memory (503) says so and offers to try again.
 */
export function MemorySection({ userId }: { userId: string }) {
  const service = getMemoryService();
  const [load, setLoad] = useState<Load>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [hidden, setHidden] = useState<Hidden>(() => new Map());
  const [status, setStatus] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [downloading, setDownloading] = useState(false);
  const [askAll, setAskAll] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  /** Forgets waiting for their Undo: the timer, and the forget held with the sign-in read when Forget was pressed. */
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const holds = useRef(new Map<string, HeldForget>());
  /** Forgets sent and not yet answered. */
  const inflight = useRef(new Map<string, Promise<void>>());
  /** Deleted while this page was open: a list read before the delete never brings one back. */
  const gone = useRef(new Set<string>());
  /** Counts "Forget everything"s done: a forget answered after one has nothing left to say. */
  const epoch = useRef(0);

  const items = useMemo(() => (load.phase === "ready" ? load.items : []), [load]);
  const visible = items.filter((item) => !hidden.has(item.id));
  const waiting = items.filter((item) => hidden.get(item.id) === "waiting");

  /** Any failure: a sign-in that is no longer accepted signs the shopper out here too (the page then asks again). */
  const failed = useCallback((e: unknown) => {
    if (e instanceof MemoryError && e.kind === "signedOut") void getAuthService().dropSession();
  }, []);

  const unhide = useCallback((id: string) => {
    setHidden((h) => {
      if (!h.has(id)) return h;
      const next = new Map(h);
      next.delete(id);
      return next;
    });
  }, []);

  const marking = useCallback((id: string, on: boolean) => {
    setBusy((b) => {
      const next = new Set(b);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  useEffect(() => {
    let live = true;
    setLoad({ phase: "loading" });
    service.list().then(
      (list) =>
        live && setLoad({ phase: "ready", items: list.filter((i) => !gone.current.has(i.id)) }),
      (e: unknown) => {
        if (!live) return;
        failed(e);
        setLoad({ phase: "error", message: messageOf(e) });
      },
    );
    return () => {
      live = false;
    };
  }, [service, userId, attempt, failed]);

  /**
   * Reads the list again after a 404 or a 409, and says why. An open form stays open while its item is still there,
   * so a forget answered 404 never takes away what the shopper is typing elsewhere.
   */
  const refresh = useCallback(
    async (why: string) => {
      setProblem(why);
      try {
        const list = (await service.list()).filter((i) => !gone.current.has(i.id));
        setLoad({ phase: "ready", items: list });
        setEditing((e) => (e && list.some((i) => i.id === e) ? e : null));
      } catch (e) {
        failed(e);
        setEditing(null);
        setLoad({ phase: "error", message: messageOf(e) });
      }
    },
    [service, failed],
  );

  const send = useCallback(
    (item: RememberedItem): Promise<void> => {
      clearTimeout(timers.current.get(item.id));
      timers.current.delete(item.id);
      const held = holds.current.get(item.id);
      holds.current.delete(item.id);
      const undoHadFocus = document.activeElement?.id === `undo-${item.id}`;
      setHidden((h) => new Map(h).set(item.id, "sending"));
      if (undoHadFocus) heading.current?.focus(); // its Undo is going away
      const at = epoch.current;
      const removed = () => {
        gone.current.add(item.id);
        setLoad((l) =>
          l.phase === "ready" ? { ...l, items: l.items.filter((i) => i.id !== item.id) } : l,
        );
        unhide(item.id);
      };
      const run = (async () => {
        try {
          await (held ? held.send() : service.forget(item.id));
          removed();
          if (epoch.current === at) setStatus(`Forgotten: ${item.text}`);
        } catch (e) {
          if (epoch.current !== at) return removed(); // "Forget everything" has covered it since
          failed(e);
          if (e instanceof MemoryError && e.kind === "notFound") {
            removed();
            await refresh(MEMORY_MESSAGES.notFound);
            return;
          }
          unhide(item.id);
          setProblem(`We couldn't forget "${item.text}". ${messageOf(e)}`);
        } finally {
          inflight.current.delete(item.id);
        }
      })();
      inflight.current.set(item.id, run);
      return run;
    },
    [service, refresh, failed, unhide],
  );

  const arm = useCallback(
    (item: RememberedItem) => {
      setHidden((h) => new Map(h).set(item.id, "waiting"));
      holds.current.set(item.id, service.holdForget(item.id));
      timers.current.set(
        item.id,
        setTimeout(() => void send(item), UNDO_MS),
      );
    },
    [service, send],
  );

  /** Sends every forget still waiting for its Undo, now, and waits for every forget under way. */
  const flush = useCallback(async () => {
    for (const item of items) if (timers.current.has(item.id)) void send(item);
    await Promise.all([...inflight.current.values()]);
  }, [items, send]);

  // Leaving the page within the 5 seconds still forgets: Forget was the shopper's choice, and Undo only a grace
  // period. Each held forget goes at once with the sign-in read when Forget was pressed, so a sign-out or a closed
  // tab can't lose it. A page brought back from the browser's cache reads the list again.
  useEffect(() => {
    const waitingNow = timers.current;
    const held = holds.current;
    const leave = () => {
      for (const [id, timer] of waitingNow) {
        clearTimeout(timer);
        const hold = held.get(id);
        if (hold) hold.sendAsPageGoes();
        else
          service.forget(id).catch(() => {
            /* the page is going: nobody is left to tell */
          });
      }
      waitingNow.clear();
      held.clear();
    };
    const back = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      setHidden(new Map());
      setAttempt((n) => n + 1);
    };
    window.addEventListener("pagehide", leave);
    window.addEventListener("pageshow", back);
    return () => {
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("pageshow", back);
      leave();
    };
  }, [service]);

  function forget(item: RememberedItem) {
    setProblem(null);
    setStatus("");
    if (editing === item.id) setEditing(null);
    arm(item);
    focusSoon(`undo-${item.id}`);
  }

  function undo(item: RememberedItem) {
    if (!timers.current.has(item.id)) return; // already sent: its Undo is on its way out
    clearTimeout(timers.current.get(item.id));
    timers.current.delete(item.id);
    holds.current.delete(item.id);
    unhide(item.id);
    setStatus(`Kept: ${item.text}`);
    focusSoon(`forget-${item.id}`);
  }

  /** Changes one item. Resolves to a sentence when it couldn't be done and nothing else said so, else null. */
  async function edit(
    item: RememberedItem,
    change: MemoryEdit,
    focusAfter: string,
  ): Promise<string | null> {
    if (busy.has(item.id)) return null;
    marking(item.id, true);
    setProblem(null);
    try {
      const { item: next, note } = await service.edit(item.id, change);
      setLoad((l) =>
        l.phase === "ready"
          ? { ...l, items: l.items.map((i) => (i.id === item.id ? next : i)) }
          : l,
      );
      setNotes((n) => {
        const rest = { ...n };
        delete rest[item.id];
        return note ? { ...rest, [item.id]: note } : rest;
      });
      setEditing((e) => (e === item.id ? null : e));
      setStatus(`Saved: ${next.text}`);
      focusSoon(focusAfter);
      return null;
    } catch (e) {
      failed(e);
      if (e instanceof MemoryError && (e.kind === "notFound" || e.kind === "changed")) {
        setEditing((open) => (open === item.id ? null : open)); // look at it again first
        await refresh(e.message);
        heading.current?.focus();
        return null;
      }
      return messageOf(e);
    } finally {
      marking(item.id, false);
    }
  }

  async function switchRule(item: RememberedItem) {
    const verb = item.active ? "switch off" : "switch on";
    const failure = await edit(item, { active: !item.active }, `switch-${item.id}`);
    if (failure) setProblem(`We couldn't ${verb} "${item.text}". ${failure}`);
  }

  async function download() {
    if (downloading) return;
    setDownloading(true);
    setProblem(null);
    setStatus("Preparing your file…");
    try {
      await flush(); // the file holds what is still remembered, so every forget goes first
      const file = await service.download();
      save(file);
      setStatus(`Your file is downloading: ${file.name}`);
    } catch (e) {
      failed(e);
      setStatus("");
      setProblem(`We couldn't make your file. ${messageOf(e)}`);
    } finally {
      setDownloading(false);
    }
  }

  async function forgetAll() {
    setAskAll(false);
    setProblem(null);
    // Forget everything covers the forgets still waiting for Undo; if it fails, they wait again.
    const paused = items.filter((item) => timers.current.has(item.id));
    for (const item of paused) {
      clearTimeout(timers.current.get(item.id));
      timers.current.delete(item.id);
      holds.current.delete(item.id);
    }
    setHidden((h) => {
      const next = new Map(h);
      for (const item of paused) next.set(item.id, "sending"); // their Undo goes while the call runs
      return next;
    });
    setStatus("Forgetting everything…");
    try {
      await service.forgetAll();
      epoch.current += 1;
      for (const item of items) gone.current.add(item.id);
      setLoad({ phase: "ready", items: [] });
      setHidden(new Map());
      setNotes({});
      setEditing(null);
      setStatus("Everything ShopSense remembered about you is forgotten.");
      heading.current?.focus();
    } catch (e) {
      failed(e);
      setStatus("");
      for (const item of paused) arm(item);
      setProblem(`We couldn't forget everything. Nothing was changed. ${messageOf(e)}`);
    }
  }

  return (
    <section aria-labelledby="memory-title" className="surface-raised rounded-3xl p-6 md:p-8">
      <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div className="max-w-2xl">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-primary-soft text-primary-soft-foreground">
              <Bookmark className="h-5 w-5" aria-hidden />
            </span>
            <h2
              id="memory-title"
              ref={heading}
              tabIndex={-1}
              className="font-serif text-2xl text-foreground focus:outline-none focus-visible:outline-2 focus-visible:outline-ring"
            >
              What we remember
            </h2>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            From interviews you finished while signed in. ShopSense fills these in for your next
            search of the same kind. You can change or forget any of them.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <PillButton
            type="button"
            variant="outline"
            aria-disabled={downloading || load.phase !== "ready"}
            onClick={() => {
              if (load.phase === "ready") void download();
            }}
          >
            Download my data
          </PillButton>
          {visible.length > 0 && (
            <PillButton
              type="button"
              variant="outline"
              id="forget-everything"
              onClick={() => setAskAll(true)}
            >
              Forget everything
            </PillButton>
          )}
        </div>
      </div>

      <p role="status" aria-live="polite" className="mt-4 min-h-5 text-sm text-muted-foreground">
        {status}
      </p>
      {problem && (
        <p
          role="alert"
          className="mt-2 rounded-2xl border border-destructive/40 bg-card px-4 py-3 text-sm text-foreground"
        >
          {problem}
        </p>
      )}

      {waiting.length > 0 && (
        <ul aria-label="Forgotten just now" className="mt-4 space-y-2">
          {waiting.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-secondary px-4 py-2 text-sm text-foreground"
            >
              <span>
                Forgotten: <span className="font-medium">{item.text}</span>
              </span>
              <button
                type="button"
                id={`undo-${item.id}`}
                onClick={() => undo(item)}
                aria-label={`Undo: keep "${item.text}"`}
                className="inline-flex min-h-11 items-center rounded-full border border-border bg-card px-4 font-medium text-foreground hover:border-primary/50 focus-visible:outline-2 focus-visible:outline-ring"
              >
                Undo
              </button>
            </li>
          ))}
        </ul>
      )}

      {load.phase === "loading" && (
        <p className="mt-6 text-sm text-muted-foreground">Loading what we remember…</p>
      )}
      {load.phase === "error" && (
        <div
          role="alert"
          className="mt-6 flex flex-wrap items-center gap-3 text-sm text-foreground"
        >
          {load.message}
          <PillButton
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setProblem(null);
              heading.current?.focus(); // this button goes while the list loads
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </PillButton>
        </div>
      )}
      {load.phase === "ready" && visible.length === 0 && waiting.length === 0 && (
        <p className="mt-6 rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {EMPTY_MEMORY}
        </p>
      )}
      {visible.length > 0 && (
        <ul aria-labelledby="memory-title" className="mt-6 grid gap-3">
          {visible.map((item) => (
            <MemoryRow
              key={item.id}
              item={item}
              note={notes[item.id] ?? null}
              busy={busy.has(item.id)}
              editing={editing === item.id}
              onEdit={() => {
                setProblem(null);
                setEditing(item.id);
              }}
              onCancel={() => {
                setEditing(null);
                focusSoon(`change-${item.id}`);
              }}
              onSave={(change) => edit(item, change, `change-${item.id}`)}
              onSwitch={() => void switchRule(item)}
              onForget={() => forget(item)}
            />
          ))}
        </ul>
      )}

      {askAll && (
        <Suspense fallback={null}>
          <ForgetAllDialog
            count={visible.length}
            onKeep={() => setAskAll(false)}
            onForget={() => void forgetAll()}
          />
        </Suspense>
      )}
    </section>
  );
}

const chip = "rounded-full px-2.5 py-0.5 font-mono text-[11px] tracking-[0.08em] uppercase";
const action =
  "inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-ring";

function MemoryRow({
  item,
  note,
  busy,
  editing,
  onEdit,
  onCancel,
  onSave,
  onSwitch,
  onForget,
}: {
  item: RememberedItem;
  note: string | null;
  busy: boolean;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  /** Resolves to a sentence for the form when the change couldn't be made, or null when it is done. */
  onSave: (change: MemoryEdit) => Promise<string | null>;
  onSwitch: () => void;
  onForget: () => void;
}) {
  return (
    <li className="rounded-2xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn(chip, "bg-primary-soft text-primary-soft-foreground")}>
          {KIND_LABELS[item.kind]}
        </span>
        <span className={cn(chip, "border border-border text-muted-foreground")}>
          {categoryLabel(item.category)}
        </span>
        {item.weight !== null && (
          <span className="text-sm text-foreground">Weight {weightLabel(item.weight)}</span>
        )}
        {item.active !== null && (
          <span className="text-sm text-muted-foreground">
            {item.active ? "Filters your results" : "Switched off: a preference only"}
          </span>
        )}
      </div>
      <p className="mt-2 break-words text-foreground">{item.text}</p>
      {note && <p className="mt-1 text-sm text-muted-foreground">{note}</p>}
      <p className="mt-1 text-xs text-muted-foreground">Last changed {changedOn(item.updatedAt)}</p>
      {editing ? (
        <WeightForm item={item} busy={busy} onCancel={onCancel} onSave={onSave} />
      ) : (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.kind === "weight" && (
            <button
              type="button"
              id={`change-${item.id}`}
              className={action}
              aria-label={`Change "${item.text}"`}
              onClick={onEdit}
            >
              Change
            </button>
          )}
          {item.kind === "hard_rule" && (
            <button
              type="button"
              id={`switch-${item.id}`}
              className={action}
              aria-label={`${busy ? "Saving…" : item.active ? "Switch off" : "Switch on"} "${item.text}"`}
              aria-disabled={busy}
              onClick={() => {
                if (!busy) onSwitch();
              }}
            >
              {busy ? "Saving…" : item.active ? "Switch off" : "Switch on"}
            </button>
          )}
          <button
            type="button"
            id={`forget-${item.id}`}
            className={action}
            aria-label={`Forget "${item.text}"`}
            aria-disabled={busy}
            onClick={() => {
              if (!busy) onForget();
            }}
          >
            Forget
          </button>
        </div>
      )}
    </li>
  );
}

/** A priority's weight, in a number box, and its words. Enter saves; Escape and Cancel keep it as it was. */
function WeightForm({
  item,
  busy,
  onCancel,
  onSave,
}: {
  item: RememberedItem;
  busy: boolean;
  onCancel: () => void;
  onSave: (change: MemoryEdit) => Promise<string | null>;
}) {
  const [weight, setWeight] = useState(item.weight === null ? "" : String(item.weight));
  const [text, setText] = useState(item.text);
  const [errors, setErrors] = useState<{ weight?: string; text?: string; form?: string }>({});
  const ids = { weight: `weight-${item.id}`, text: `words-${item.id}` };

  useEffect(() => {
    document.getElementById(ids.weight)?.focus();
  }, [ids.weight]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const number = Number(weight.trim());
    const words = text.trim();
    const next: typeof errors = {};
    if (
      weight.trim() === "" ||
      !Number.isFinite(number) ||
      number < WEIGHT_MIN ||
      number > WEIGHT_MAX
    )
      next.weight = `Use a number from ${WEIGHT_MIN} to ${WEIGHT_MAX}.`;
    if (words.length < 1 || words.length > TEXT_MAX) next.text = `Use 1 to ${TEXT_MAX} characters.`;
    setErrors(next);
    if (next.weight) return document.getElementById(ids.weight)?.focus();
    if (next.text) return document.getElementById(ids.text)?.focus();
    const change: MemoryEdit = {
      ...(number !== item.weight ? { weight: number } : {}),
      ...(words !== item.text ? { text: words } : {}),
    };
    if (Object.keys(change).length === 0) return onCancel(); // nothing changed: nothing to send
    const problem = await onSave(change);
    if (problem) setErrors({ form: problem });
  }

  const field =
    "min-h-11 rounded-2xl border border-input bg-background px-3 py-2 text-base text-foreground focus-visible:outline-2";
  return (
    <form
      noValidate
      onSubmit={(e) => void submit(e)}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
      aria-label={`Change "${item.text}"`}
      className="mt-3 grid gap-3 border-t border-border pt-3"
    >
      <div className="grid gap-3 sm:grid-cols-[auto_1fr]">
        <div className="grid gap-1">
          <label htmlFor={ids.weight} className="text-sm font-medium text-foreground">
            Weight, {WEIGHT_MIN} to {WEIGHT_MAX}
          </label>
          <input
            id={ids.weight}
            type="number"
            inputMode="decimal"
            min={WEIGHT_MIN}
            max={WEIGHT_MAX}
            step="any"
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
            aria-invalid={errors.weight ? true : undefined}
            aria-describedby={errors.weight ? `${ids.weight}-error` : undefined}
            className={cn(field, "w-24")}
          />
          {errors.weight && (
            <p id={`${ids.weight}-error`} className="text-sm text-destructive">
              {errors.weight}
            </p>
          )}
        </div>
        <div className="grid gap-1">
          <label htmlFor={ids.text} className="text-sm font-medium text-foreground">
            In your words
          </label>
          <input
            id={ids.text}
            type="text"
            maxLength={TEXT_MAX}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={errors.text ? true : undefined}
            aria-describedby={errors.text ? `${ids.text}-error` : undefined}
            className={cn(field, "w-full")}
          />
          {errors.text && (
            <p id={`${ids.text}-error`} className="text-sm text-destructive">
              {errors.text}
            </p>
          )}
        </div>
      </div>
      {errors.form && (
        <p role="alert" className="text-sm text-foreground">
          {errors.form}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <PillButton type="submit" size="sm" aria-disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </PillButton>
        <PillButton type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </PillButton>
      </div>
    </form>
  );
}
