/**
 * The page for an address that leads nowhere, and for a page that failed to load: a lens searching an empty shelf,
 * plain words and a way on. Exactly one heading, and no error number anywhere (not-found.spec.ts).
 */
import type { ReactNode } from "react";
import { useVisualMotion } from "@/hooks/useVisualMotion";
import { BrandMark } from "./ui";

function SearchingLens() {
  // The lens moves once the page has loaded, only while on screen, and not at all for reduced motion.
  const motion = useVisualMotion<HTMLDivElement>();
  return (
    <div
      ref={motion}
      data-motion-loop
      aria-hidden
      className="relative mx-auto h-44 w-72 select-none"
    >
      <svg viewBox="0 0 288 176" className="absolute inset-0 h-full w-full" fill="none">
        {/* the shelf, and the outlines of products that aren't there */}
        <path d="M24 132h240" stroke="var(--border)" strokeWidth="2" strokeLinecap="round" />
        {[44, 104, 164, 224].map((x, i) => (
          <rect
            key={x}
            x={x}
            y={132 - [48, 64, 40, 56][i]!}
            width="40"
            height={[48, 64, 40, 56][i]!}
            rx="8"
            stroke="var(--border)"
            strokeWidth="2"
            strokeDasharray="5 6"
          />
        ))}
        {/* a thread that leads nowhere */}
        <path
          d="M30 160 C 90 150, 120 172, 170 158 S 250 150, 262 164"
          stroke="var(--primary)"
          strokeOpacity="0.45"
          strokeWidth="1.5"
          strokeDasharray="2 7"
          strokeLinecap="round"
        />
      </svg>
      <div className="lost-lens absolute left-0 top-6">
        <svg viewBox="0 0 64 64" className="h-16 w-16" fill="none">
          <circle
            cx="26"
            cy="26"
            r="17"
            fill="var(--card)"
            stroke="var(--primary)"
            strokeWidth="4"
          />
          <path d="M39 39 L56 56" stroke="var(--primary)" strokeWidth="6" strokeLinecap="round" />
          <path
            d="M18 22 a9 9 0 0 1 8 -6"
            stroke="var(--primary)"
            strokeOpacity="0.5"
            strokeWidth="3"
            strokeLinecap="round"
          />
        </svg>
      </div>
    </div>
  );
}

export function LostPage({
  title,
  text,
  actions,
}: {
  title: string;
  text: string;
  actions: ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-background">
      <div className="bg-ambient pointer-events-none absolute inset-0 opacity-80" aria-hidden />
      <div className="bg-blueprint pointer-events-none absolute inset-0" aria-hidden />
      <div className="relative px-6 pt-6">
        <BrandMark />
      </div>
      <main className="relative flex flex-1 items-center justify-center px-6 pb-24">
        <div className="animate-rise max-w-lg text-center">
          <SearchingLens />
          <h1 className="mt-10 font-serif text-4xl leading-tight text-foreground md:text-5xl">
            {title}
          </h1>
          <p className="mx-auto mt-4 max-w-md text-lg text-muted-foreground">{text}</p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">{actions}</div>
        </div>
      </main>
    </div>
  );
}
