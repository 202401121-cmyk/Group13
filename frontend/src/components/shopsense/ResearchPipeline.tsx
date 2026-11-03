import { ClipboardList, Search, ShieldCheck, Scale, Check } from "lucide-react";

const stages = [
  { label: "Intake", icon: ClipboardList },
  { label: "Research", icon: Search },
  { label: "Verify", icon: ShieldCheck },
  { label: "Match", icon: Scale },
  { label: "Decide", icon: Check },
];

export function ResearchPipeline() {
  return (
    <div
      className="research-pipeline mt-12 border-t border-border pt-8"
      data-reveal
      data-motion-loop
    >
      <p className="text-center font-mono text-[10px] uppercase text-muted-foreground sm:text-xs">
        Workflow illustration · Not live research
      </p>
      <ol
        aria-label="ShopSense workflow illustration"
        className="mx-auto mt-6 grid max-w-3xl grid-cols-5"
      >
        {stages.map(({ label, icon: Icon }) => (
          <li
            key={label}
            className="pipeline-stage relative flex min-w-0 flex-col items-center gap-3"
          >
            <span className="pipeline-connector" aria-hidden />
            <span className="pipeline-icon relative grid h-10 w-10 place-items-center rounded-full border border-border bg-card text-primary sm:h-12 sm:w-12">
              <Icon className="h-4 w-4" aria-hidden />
              <span
                className="pipeline-indicator absolute -right-0.5 top-0 h-1.5 w-1.5 rounded-full bg-primary"
                aria-hidden
              />
            </span>
            <span className="text-[10px] font-medium text-foreground sm:text-sm">{label}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
