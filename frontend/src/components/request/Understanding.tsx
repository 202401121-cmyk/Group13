import { useEffect, useRef, useState } from "react";
import { Info, X } from "lucide-react";
import { PillButton } from "@/components/shopsense/ui";
import { describeBudget, formatAmountInput, formatINR, validateBudget } from "@/lib/request/format";
import type { Brief, FieldKey } from "@/lib/request/types";

interface Props {
  brief: Brief;
  editingField: FieldKey | null;
  saving: boolean;
  saveError: string | null;
  edited: boolean;
  onEdit: (f: FieldKey) => void;
  onCancel: () => void;
  onSave: (brief: Brief) => void;
  onConfirm: () => void;
  onStartOver: () => void;
}

const FIELDS: { key: FieldKey; label: string; empty: string; name: string }[] = [
  { key: "product", label: "Product", empty: "No product given", name: "product" },
  { key: "usedFor", label: "Used for", empty: "No use given", name: "use" },
  { key: "budget", label: "Budget", empty: "No budget given", name: "budget" },
  { key: "region", label: "Region", empty: "No region given", name: "region" },
  { key: "mustHaves", label: "Must-haves", empty: "No must-haves given", name: "must-haves" },
  { key: "exclusions", label: "Leave out", empty: "None specified", name: "exclusions" },
];

function display(b: Brief, k: FieldKey): string | null {
  switch (k) {
    case "budget":
      return describeBudget(b.budget);
    case "mustHaves":
      return b.mustHaves.join("; ") || null;
    case "exclusions":
      return b.exclusions.join(", ") || null;
    default:
      return b[k];
  }
}

const inputCls =
  "w-full rounded-xl border border-input bg-surface px-4 py-2.5 text-foreground outline-none focus:border-primary focus:ring-4 focus:ring-primary/15 aria-invalid:border-destructive";

function FieldError({ id, msg }: { id: string; msg: string | null }) {
  if (!msg) return null;
  return (
    <p id={id} className="mt-2 text-sm font-medium text-destructive">
      <span aria-hidden>⚠ </span>
      {msg}
    </p>
  );
}

function Editor({ field, brief, saving, saveError, onCancel, onSave }: { field: (typeof FIELDS)[number]; brief: Brief; saving: boolean; saveError: string | null; onCancel: () => void; onSave: (b: Brief) => void }) {
  const k = field.key;
  const errId = `${k}-error`;
  const [error, setError] = useState<string | null>(null);
  const [textVal, setTextVal] = useState(k === "product" || k === "usedFor" || k === "region" ? (brief[k] ?? "") : "");
  const [from, setFrom] = useState(brief.budget?.min != null ? formatINR(brief.budget.min) : "");
  const [upTo, setUpTo] = useState(brief.budget?.max != null ? formatINR(brief.budget.max) : "");
  const [musts, setMusts] = useState(brief.mustHaves.join("\n"));
  const [excl, setExcl] = useState<string[]>(brief.exclusions);
  const [newExcl, setNewExcl] = useState("");

  function save() {
    const next: Brief = { ...brief };
    if (k === "product") {
      if (!textVal.trim()) return setError("Tell us what product you're shopping for.");
      next.product = textVal.trim();
    } else if (k === "usedFor" || k === "region") {
      next[k] = textVal.trim() || null;
    } else if (k === "budget") {
      const v = validateBudget(from, upTo);
      if (!v.ok) return setError(v.error);
      next.budget = v.budget;
    } else if (k === "mustHaves") {
      next.mustHaves = musts.split("\n").map((s) => s.trim()).filter(Boolean);
    } else {
      const pending = newExcl.trim();
      next.exclusions = pending && !excl.includes(pending) ? [...excl, pending] : excl;
    }
    setError(null);
    onSave(next);
  }

  function addExcl() {
    const v = newExcl.trim();
    if (!v) return setError("Type something to leave out.");
    if (!excl.includes(v)) setExcl([...excl, v]);
    setNewExcl("");
    setError(null);
  }

  const msg = error ?? saveError;
  const described = msg ? errId : undefined;

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
      onKeyDown={(e) => e.key === "Escape" && onCancel()}
      className="mt-3"
    >
      {(k === "product" || k === "usedFor" || k === "region") && (
        <>
          <label htmlFor={`edit-${k}`} className="sr-only">{field.label}</label>
          <input id={`edit-${k}`} autoFocus value={textVal} onChange={(e) => setTextVal(e.target.value)} aria-invalid={!!msg} aria-describedby={described} className={inputCls} />
        </>
      )}
      {k === "budget" && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {([["From", from, setFrom], ["Up to", upTo, setUpTo]] as const).map(([lbl, val, set], i) => (
              <div key={lbl}>
                <label htmlFor={`budget-${i}`} className="text-sm font-medium text-foreground">{lbl}</label>
                <div className="relative mt-1">
                  <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden>₹</span>
                  <input
                    id={`budget-${i}`}
                    autoFocus={i === 0}
                    inputMode="numeric"
                    value={val}
                    onChange={(e) => set(formatAmountInput(e.target.value))}
                    aria-invalid={!!msg}
                    aria-describedby={`budget-help${described ? ` ${errId}` : ""}`}
                    className={`${inputCls} pl-8`}
                  />
                </div>
              </div>
            ))}
          </div>
          <p id="budget-help" className="mt-2 text-xs text-muted-foreground">Leave From empty if you only have a limit.</p>
        </>
      )}
      {k === "mustHaves" && (
        <>
          <label htmlFor="edit-musts" className="sr-only">Must-haves, one per line</label>
          <textarea id="edit-musts" autoFocus rows={3} value={musts} onChange={(e) => setMusts(e.target.value)} aria-describedby="musts-help" className={inputCls} />
          <p id="musts-help" className="mt-1 text-xs text-muted-foreground">One per line. We keep your wording.</p>
        </>
      )}
      {k === "exclusions" && (
        <>
          {excl.length > 0 && (
            <ul className="mb-3 flex flex-wrap gap-2" aria-label="Things to leave out">
              {excl.map((x) => (
                <li key={x} className="inline-flex items-center gap-1 rounded-full bg-accent py-1 pl-3 pr-1 text-sm text-accent-foreground">
                  {x}
                  <button type="button" onClick={() => setExcl(excl.filter((e) => e !== x))} aria-label={`Remove ${x}`} className="grid h-6 w-6 place-items-center rounded-full hover:bg-primary/10">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <label htmlFor="edit-excl" className="sr-only">Add something to leave out</label>
            <input
              id="edit-excl"
              autoFocus
              value={newExcl}
              placeholder="e.g. refurbished"
              onChange={(e) => setNewExcl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  addExcl();
                }
              }}
              aria-invalid={!!msg}
              aria-describedby={described}
              className={inputCls}
            />
            <PillButton type="button" variant="outline" onClick={addExcl}>Add</PillButton>
          </div>
        </>
      )}
      <FieldError id={errId} msg={msg} />
      <div className="mt-3 flex gap-2">
        <PillButton type="submit" size="sm" disabled={saving}>{saving ? "Saving…" : "Save"}</PillButton>
        <PillButton type="button" size="sm" variant="ghost" onClick={onCancel} disabled={saving}>Cancel</PillButton>
      </div>
    </form>
  );
}

export function Understanding(p: Props) {
  const h1 = useRef<HTMLHeadingElement>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  useEffect(() => h1.current?.focus(), []);

  function startOver() {
    if (p.edited) setConfirmReset(true);
    else p.onStartOver();
  }

  return (
    <section aria-labelledby="understood-title">
      <h1 id="understood-title" ref={h1} tabIndex={-1} className="font-serif text-4xl text-foreground outline-none md:text-5xl">Here's what we understood</h1>
      <p className="mt-3 text-muted-foreground">Check the details and fix anything we got wrong.</p>
      {p.brief.source === "fallback" && (
        <p className="mt-5 flex items-start gap-2 rounded-2xl border border-border bg-secondary px-4 py-3 text-sm text-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
          Our backup reader filled this in. Please check it.
        </p>
      )}
      <dl className="mt-8 divide-y divide-border rounded-2xl border border-border bg-card px-5 shadow-soft sm:px-7">
        {FIELDS.map((f) => {
          const v = display(p.brief, f.key);
          const editing = p.editingField === f.key;
          return (
            <div key={f.key} className="grid gap-1 py-5 sm:grid-cols-[9rem_1fr_auto] sm:gap-4">
              <dt className="text-sm font-semibold text-muted-foreground">{f.label}</dt>
              <dd className="min-w-0">
                {editing ? (
                  <Editor field={f} brief={p.brief} saving={p.saving} saveError={p.saveError} onCancel={p.onCancel} onSave={p.onSave} />
                ) : (
                  <span className={v ? "break-words text-foreground" : "text-muted-foreground italic"}>{v ?? f.empty}</span>
                )}
              </dd>
              {!editing && (
                <dd className="sm:text-right">
                  <button
                    type="button"
                    onClick={() => p.onEdit(f.key)}
                    disabled={p.editingField !== null}
                    aria-label={`${v ? "Edit" : "Add"} ${f.name}`}
                    className="rounded-full px-3 py-1 text-sm font-medium text-primary hover:bg-accent disabled:opacity-40"
                  >
                    {v ? "Edit" : "Add"}
                  </button>
                </dd>
              )}
            </div>
          );
        })}
      </dl>
      {confirmReset ? (
        <div role="alertdialog" aria-labelledby="reset-q" className="mt-8 rounded-2xl border border-border bg-surface p-5">
          <p id="reset-q" className="font-medium text-foreground">Discard your edits and start over?</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <PillButton size="sm" onClick={p.onStartOver} autoFocus>Discard and start over</PillButton>
            <PillButton size="sm" variant="ghost" onClick={() => setConfirmReset(false)}>Keep my edits</PillButton>
          </div>
        </div>
      ) : (
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <PillButton size="lg" onClick={p.onConfirm} disabled={p.editingField !== null} className="w-full sm:w-auto">Looks right</PillButton>
          <PillButton size="lg" variant="outline" onClick={startOver} className="w-full sm:w-auto">Start over</PillButton>
        </div>
      )}
    </section>
  );
}

