import { useId, useState } from "react";
import { CircleHelp } from "lucide-react";
import type { QuestionTerm } from "@/lib/interview/types";

/**
 * "What does this mean?" under a question (US-07). Each term is a plain button that opens its explanation
 * in place. Opening one is never an answer: it changes only this component's own state, sends nothing and
 * leaves focus on the button. Key it by the question, so it starts closed on every new question.
 */
export function TermHelp({ terms }: { terms: QuestionTerm[] }) {
  // A step saved by an older version of the page may have no list at all.
  if (!terms?.length) return null;
  const named = terms.length > 1;
  return (
    <div className="mt-3 flex flex-col gap-1">
      {terms.map((t) => (
        <TermToggle key={t.term} term={t} named={named} />
      ))}
    </div>
  );
}

function TermToggle({ term, named }: { term: QuestionTerm; named: boolean }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const regionId = `${id}-meaning`;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-sm text-sm font-medium text-primary underline underline-offset-4 hover:decoration-2"
      >
        <CircleHelp className="h-4 w-4 shrink-0" aria-hidden />
        {named ? `What does ${term.term} mean?` : "What does this mean?"}
      </button>
      <div
        id={regionId}
        role="region"
        aria-label={`What ${term.term} means`}
        hidden={!open}
        className="mt-1 max-w-prose rounded-2xl bg-muted px-4 py-3 text-sm leading-relaxed text-foreground"
      >
        <p>
          <strong className="font-semibold">{term.term}</strong>: {term.explanation}
        </p>
      </div>
    </div>
  );
}
