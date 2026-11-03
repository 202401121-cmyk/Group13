import { forwardRef, type KeyboardEvent } from "react";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { Card, PillButton } from "@/components/shopsense/ui";
import { MAX_REQUEST_LENGTH } from "@/lib/request/types";
import { ExampleChips } from "./ExampleChips";

interface Props {
  text: string;
  error: string | null;
  onChange: (text: string) => void;
  onSubmit: () => void;
  onPickExample: (text: string) => void;
}

export const RequestForm = forwardRef<HTMLTextAreaElement, Props>(function RequestForm(
  { text, error, onChange, onSubmit, onPickExample },
  ref,
) {
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Never submit mid-composition (Hindi, Gujarati and other IMEs).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  }

  return (
    <div className="space-y-8">
      <Card className="p-6 sm:p-8">
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
        >
          <label htmlFor="request-text" className="text-sm font-semibold text-foreground">
            Your request
          </label>
          <textarea
            ref={ref}
            id="request-text"
            rows={5}
            value={text}
            maxLength={MAX_REQUEST_LENGTH}
            onChange={(e) => onChange(e.target.value.slice(0, MAX_REQUEST_LENGTH))}
            onKeyDown={onKeyDown}
            placeholder="e.g. laptop for coding under 70k, at least 16 GB RAM, not too heavy"
            aria-invalid={!!error}
            aria-describedby={`request-count${error ? " request-error" : ""}`}
            className="mt-3 w-full resize-y rounded-2xl border border-input bg-surface p-4 text-base leading-relaxed text-foreground outline-hidden transition placeholder:text-muted-foreground/70 focus:border-primary focus:ring-4 focus:ring-primary/15 aria-invalid:border-destructive"
          />
          <div className="mt-2 flex flex-wrap items-start justify-between gap-2 text-sm">
            {error ? (
              <p
                id="request-error"
                role="alert"
                className="flex items-center gap-1.5 font-medium text-destructive"
              >
                <span aria-hidden>⚠</span> {error}
              </p>
            ) : (
              <span />
            )}
            <p id="request-count" className="text-muted-foreground" aria-live="polite">
              {text.length.toLocaleString("en-IN")} of 1,000 characters
            </p>
          </div>
          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-muted-foreground">
              Enter to continue · Shift + Enter for a new line
            </p>
            <PillButton type="submit" size="lg" className="w-full sm:w-auto">
              Continue <ArrowRight className="h-4 w-4" />
            </PillButton>
          </div>
        </form>
      </Card>
      <ExampleChips onPick={onPickExample} />
      <p className="flex items-start gap-2 text-sm text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
        ShopSense reads reviews, Reddit and stores, and shows where every fact came from.
      </p>
    </div>
  );
});
