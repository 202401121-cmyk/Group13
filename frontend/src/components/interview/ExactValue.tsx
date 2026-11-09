import { useEffect, useRef, useState, type FormEvent } from "react";
import { PillButton } from "@/components/shopsense/ui";
import {
  MAX_EXACT,
  MIN_EXACT,
  SLIDER_STEP,
  parseExactValue,
  roughWeightsInWords,
} from "@/lib/interview/roughLevels";

/** The route's number keys look for this, so they can't pick a hidden button while the box is open. */
export const EXACT_INPUT_ID = "exact-value";
const SLIDER_ID = "exact-value-slider";
const HELP_ID = "exact-value-help";
const ERROR_ID = "exact-value-error";

/** A number box and a matching slider for an exact answer (US-08). Nothing is sent until it is a number from 0 to 10. */
export function ExactValue({
  initial,
  busy,
  onSend,
  onUseButtons,
}: {
  initial: number | null;
  busy: boolean;
  onSend: (value: number) => void;
  onUseButtons: () => void;
}) {
  const [raw, setRaw] = useState(initial === null ? "" : String(initial));
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);

  const parsed = parseExactValue(raw);

  function change(value: string) {
    setRaw(value);
    setError(null);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (parsed.ok) {
      onSend(parsed.value);
    } else {
      setError(parsed.message);
      inputRef.current?.focus();
    }
  }

  return (
    <form noValidate onSubmit={submit} className="mt-5 grid gap-4">
      <div className="grid gap-2">
        <label htmlFor={EXACT_INPUT_ID} className="text-sm font-medium text-foreground">
          Your number, from {MIN_EXACT} to {MAX_EXACT}
        </label>
        <input
          ref={inputRef}
          id={EXACT_INPUT_ID}
          type="number"
          inputMode="decimal"
          min={MIN_EXACT}
          max={MAX_EXACT}
          step="any"
          value={raw}
          readOnly={busy}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${HELP_ID} ${ERROR_ID}` : HELP_ID}
          onChange={(e) => change(e.target.value)}
          className="min-h-11 w-full max-w-40 rounded-2xl border border-input bg-background px-4 py-2.5 text-base text-foreground focus-visible:outline-2"
        />
        {error && (
          <p id={ERROR_ID} role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="grid gap-1">
        <label htmlFor={SLIDER_ID} className="text-sm font-medium text-foreground">
          Or slide to your number
        </label>
        <input
          id={SLIDER_ID}
          type="range"
          min={MIN_EXACT}
          max={MAX_EXACT}
          step={SLIDER_STEP}
          value={parsed.ok ? parsed.value : (MIN_EXACT + MAX_EXACT) / 2}
          disabled={busy}
          onChange={(e) => change(e.target.value)}
          // With the box empty the slider sits in the middle, and clicking it there fires no change.
          onPointerUp={(e) => {
            if (!raw.trim()) change(e.currentTarget.value);
          }}
          className="h-11 w-full cursor-pointer rounded-full accent-primary focus-visible:outline-2 focus-visible:outline-offset-2"
        />
      </div>
      <p id={HELP_ID} className="text-sm text-muted-foreground">
        For comparison: {roughWeightsInWords()}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <PillButton type="submit" disabled={busy}>
          Send this number
        </PillButton>
        <button
          type="button"
          disabled={busy}
          onClick={onUseButtons}
          className="min-h-11 rounded-full px-3 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 disabled:opacity-60"
        >
          Use the buttons instead
        </button>
      </div>
    </form>
  );
}
