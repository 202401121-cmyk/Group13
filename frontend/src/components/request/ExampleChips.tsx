import { EXAMPLE_REQUESTS } from "@/lib/request/types";

export function ExampleChips({ onPick, label = "Or start from an example" }: { onPick: (text: string) => void; label?: string }) {
  return (
    <div>
      <p id="examples-label" className="text-sm font-medium text-foreground">{label}</p>
      <ul className="mt-3 flex flex-wrap gap-2" aria-labelledby="examples-label">
        {EXAMPLE_REQUESTS.map((ex) => (
          <li key={ex} className="max-w-full">
            <button
              type="button"
              onClick={() => onPick(ex)}
              className="max-w-full rounded-full border border-border bg-card px-4 py-2 text-left text-sm text-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-accent-foreground"
            >
              {ex}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
