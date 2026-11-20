import type { ReactNode } from "react";

/** The top of a content page (About, Features, FAQ, Contact): an eyebrow, the title and a lead, on the drafting grid. */
export function PageHero({
  eyebrow,
  title,
  lead,
  children,
}: {
  eyebrow: string;
  title: ReactNode;
  lead: string;
  children?: ReactNode;
}) {
  return (
    <section className="relative overflow-hidden">
      <div className="bg-ambient pointer-events-none absolute inset-0 opacity-90" aria-hidden />
      <div className="bg-blueprint pointer-events-none absolute inset-0" aria-hidden />
      <div className="animate-rise relative mx-auto max-w-5xl px-6 pb-16 pt-24 md:pb-20 md:pt-32">
        <p className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3.5 py-1.5 font-mono text-[11px] font-medium tracking-[0.14em] text-foreground uppercase shadow-[var(--highlight)]">
          <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden />
          {eyebrow}
        </p>
        <h1 className="mt-7 max-w-3xl font-serif text-5xl leading-[1.05] tracking-[-0.015em] text-foreground md:text-6xl">
          {title}
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground">{lead}</p>
        {children}
      </div>
    </section>
  );
}
