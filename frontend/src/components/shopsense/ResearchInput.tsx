import { useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2 } from "lucide-react";
import { Card, PillButton } from "./ui";

export function ResearchInput() {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = value.trim();
    if (!q) {
      setError("Tell us a little about what you're looking for first.");
      setSubmitted(null);
      return;
    }
    setError("");
    setSubmitted(q);
  }

  return (
    <Card className="p-8 md:p-12">
      <form onSubmit={onSubmit} noValidate>
        <h2 className="font-serif text-4xl text-foreground md:text-5xl">
          What are you looking for?
        </h2>
        <p className="mt-3 text-muted-foreground">
          Start with a product, a problem or a budget. Refine the details as you go.
        </p>
        <label htmlFor="research-query" className="sr-only">
          Describe what you're looking for
        </label>
        <textarea
          id="research-query"
          rows={4}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. A lightweight laptop for coding under ₹70,000…"
          aria-invalid={!!error}
          aria-describedby="research-help research-error"
          className="mt-6 w-full resize-none rounded-2xl border border-input bg-card p-5 text-base text-foreground shadow-soft outline-none transition placeholder:text-muted-foreground/70 focus:border-primary focus:ring-4 focus:ring-primary/10"
        />
        {error && (
          <p id="research-error" className="mt-2 text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="mt-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p id="research-help" className="text-sm text-muted-foreground">
            You'll be able to refine your criteria in the next step.
          </p>
          <PillButton type="submit" size="lg">
            Start my research <ArrowRight className="h-4 w-4" />
          </PillButton>
        </div>
        {submitted && (
          <div
            role="status"
            className="mt-6 flex gap-3 rounded-2xl border border-primary/20 bg-accent p-5 text-accent-foreground"
          >
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <p className="font-medium">Request captured</p>
              <p className="mt-1 text-sm">"{submitted}"</p>
              <p className="mt-2 text-xs opacity-80">
                Live research isn't available yet — criteria refinement is coming next.
              </p>
            </div>
          </div>
        )}
      </form>
    </Card>
  );
}
