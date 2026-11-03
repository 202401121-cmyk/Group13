import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Loader2 } from "lucide-react";
import { Card, PillButton } from "@/components/shopsense/ui";
import type { FollowUp } from "@/lib/request/types";
import { ExampleChips } from "./ExampleChips";

function Echo({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <p className="font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">You asked</p>
      <p className="mt-1 break-words text-foreground">"{text}"</p>
    </div>
  );
}

export function Submitting({ text }: { text: string }) {
  return (
    <Card className="space-y-5">
      <Echo text={text} />
      <p role="status" className="flex items-center gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin text-primary motion-reduce:animate-none" aria-hidden />
        Reading your request…
      </p>
    </Card>
  );
}

export function ErrorState({ text, message, onRetry, onEdit }: { text: string; message: string; onRetry: () => void; onEdit: () => void }) {
  return (
    <Card className="space-y-5">
      <Echo text={text} />
      <div role="alert" className="flex gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-4">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden />
        <div>
          <p className="font-medium text-foreground">Something went wrong</p>
          <p className="mt-1 text-sm text-muted-foreground">{message}</p>
        </div>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <PillButton onClick={onRetry} className="w-full sm:w-auto">Retry</PillButton>
        <PillButton variant="outline" onClick={onEdit} className="w-full sm:w-auto">Edit request</PillButton>
      </div>
    </Card>
  );
}

export function Clarification({
  text,
  followUp,
  error,
  onAnswer,
  onEdit,
}: {
  text: string;
  followUp: FollowUp;
  error: string | null;
  onAnswer: (answer: string) => void;
  onEdit: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [other, setOther] = useState(false);
  const [custom, setCustom] = useState("");
  useEffect(() => headingRef.current?.focus(), []);

  return (
    <Card className="space-y-6">
      <Echo text={text} />
      <div>
        <h2 ref={headingRef} tabIndex={-1} className="font-serif text-3xl text-foreground outline-none">{followUp.question}</h2>
        <p className="mt-2 text-sm text-muted-foreground">Pick one so we don't have to guess.</p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Product type">
        {followUp.options.map((o) => (
          <button key={o} type="button" onClick={() => onAnswer(o)} className="rounded-2xl border border-border bg-surface px-4 py-3 text-left font-medium text-foreground transition-colors hover:border-primary/40 hover:bg-accent">
            {o}
          </button>
        ))}
        <button type="button" aria-expanded={other} onClick={() => setOther(true)} className="rounded-2xl border border-dashed border-border px-4 py-3 text-left font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground">
          Something else
        </button>
      </div>
      {other && (
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            onAnswer(custom);
          }}
          className="space-y-3"
        >
          <label htmlFor="custom-kind" className="text-sm font-semibold text-foreground">Type the kind of product</label>
          <input
            id="custom-kind"
            autoFocus
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            aria-invalid={!!error}
            aria-describedby={error ? "custom-error" : undefined}
            className="w-full rounded-full border border-input bg-surface px-5 py-3 text-foreground outline-none focus:border-primary focus:ring-4 focus:ring-primary/15"
          />
          {error && <p id="custom-error" className="text-sm font-medium text-destructive"><span aria-hidden>⚠ </span>{error}</p>}
          <PillButton type="submit" className="w-full sm:w-auto">Continue <ArrowRight className="h-4 w-4" /></PillButton>
        </form>
      )}
      <button type="button" onClick={onEdit} className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
        Change my request instead
      </button>
    </Card>
  );
}

export function ExamplesState({ onPick }: { onPick: (text: string) => void }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headingRef.current?.focus(), []);
  return (
    <Card className="space-y-6">
      <div>
        <h2 ref={headingRef} tabIndex={-1} className="font-serif text-3xl text-foreground outline-none">We couldn't tell what kind of product you mean</h2>
        <p className="mt-2 text-muted-foreground">Pick an example to start from, or describe the product in a few words.</p>
      </div>
      <ExampleChips onPick={onPick} label="Examples" />
    </Card>
  );
}
