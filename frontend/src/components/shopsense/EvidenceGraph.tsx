/**
 * ShopSense in one picture: sources (a Reddit thread, an expert review, a store listing, a video) tied by threads of
 * light to the product they made the best match, with its score and the criteria it was weighed on.
 *
 * Drawn on a fixed 480 x 480 stage and scaled to its container in steps (container queries and zoom, in styles.css),
 * so the composition is the same at every width. Decorative and labelled as such: the words in it are an
 * illustration, not data. Motion: the threads draw in once, a short dash of light runs along each, the chips float
 * and the criteria bars grow. All of it is stroke and transform, never a fade of anything with text (F-13); it starts
 * after the page has loaded, pauses off screen and stays still for reduced motion (data-motion-loop).
 */
import { BadgeCheck, FileText, MessagesSquare, PlayCircle, Store } from "lucide-react";
import type { ComponentType } from "react";
import { useVisualMotion } from "@/hooks/useVisualMotion";
import { cn } from "@/lib/utils";

interface Source {
  icon: ComponentType<{ className?: string }>;
  label: string;
  note: string;
  /** Where the chip hangs on the 480 x 480 stage: from the left or the right edge. */
  left?: number;
  right?: number;
  top: number;
  /** The thread from the chip to the card. */
  d: string;
  delay: number;
}

const SOURCES: Source[] = [
  {
    icon: MessagesSquare,
    label: "Reddit thread",
    note: "18 owners agree",
    left: 0,
    top: 8,
    d: "M84 60 C 92 104, 128 112, 156 138",
    delay: 0,
  },
  {
    icon: FileText,
    label: "Expert review",
    note: "battery tested",
    right: 4,
    top: 0,
    d: "M378 52 C 372 98, 346 112, 326 138",
    delay: 0.35,
  },
  {
    icon: Store,
    label: "Store listing",
    note: "₹68,990 today",
    right: 0,
    top: 420,
    d: "M386 420 C 380 384, 352 368, 328 350",
    delay: 0.7,
  },
  {
    icon: PlayCircle,
    label: "Video review",
    note: "weight checked",
    left: 4,
    top: 420,
    d: "M90 420 C 98 384, 128 368, 154 350",
    delay: 1.05,
  },
];

const CRITERIA = [
  { label: "Battery life", value: 92 },
  { label: "Performance", value: 84 },
  { label: "Weight", value: 71 },
];

/**
 * Geist Mono has no small rupee file, so in mono text the ₹ sign is set in Geist: the home page then downloads only
 * its small font files (e2e/quality/fonts.spec.ts).
 */
function MonoMoney({ text }: { text: string }) {
  const at = text.indexOf("₹");
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <span className="font-sans">₹</span>
      {text.slice(at + 1)}
    </>
  );
}

export function EvidenceGraph({ className }: { className?: string }) {
  const motion = useVisualMotion<HTMLDivElement>();
  return (
    <div
      ref={motion}
      data-motion-loop
      role="img"
      aria-label="An illustration: four sources linked to the product they made the best match"
      className={cn(
        "evidence-graph mx-auto aspect-square w-full max-w-[30rem] select-none",
        className,
      )}
    >
      <div className="evidence-stage relative mx-auto h-[480px] w-[480px]">
        <svg
          viewBox="0 0 480 480"
          className="absolute inset-0 h-full w-full overflow-visible"
          fill="none"
          aria-hidden
        >
          <defs>
            <linearGradient id="thread-fade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--glow)" stopOpacity="0.2" />
              <stop offset="100%" stopColor="var(--glow)" stopOpacity="0.75" />
            </linearGradient>
          </defs>
          {SOURCES.map((s) => (
            <g key={s.label} style={{ ["--line-delay" as string]: `${s.delay}s` }}>
              <path
                d={s.d}
                pathLength={100}
                className="thread-line"
                stroke="url(#thread-fade)"
                strokeWidth="1.25"
                strokeLinecap="round"
              />
              <path
                d={s.d}
                pathLength={100}
                className="thread-comet"
                stroke="var(--glow)"
                strokeWidth="2.5"
                strokeLinecap="round"
                style={{ ["--travel" as string]: `${3 + s.delay}s` }}
              />
            </g>
          ))}
        </svg>

        {SOURCES.map((s, i) => (
          <div
            key={s.label}
            aria-hidden
            className="animate-float absolute flex w-max max-w-[200px] items-center gap-2.5 rounded-2xl border border-ink-border bg-ink-raised/95 py-2 pl-2 pr-3 shadow-ink-chip"
            style={{ left: s.left, right: s.right, top: s.top, animationDelay: `${-i * 1.4}s` }}
          >
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-ink-well text-ink-foreground">
              <s.icon className="h-4 w-4" />
            </span>
            <span className="min-w-0 leading-tight">
              <span className="block text-[13px] font-medium text-ink-foreground">{s.label}</span>
              <span className="block font-mono text-[11px] text-ink-muted">
                <MonoMoney text={s.note} />
              </span>
            </span>
          </div>
        ))}

        <div
          aria-hidden
          className="absolute left-[104px] top-[138px] w-[272px] rounded-3xl border border-ink-border bg-ink-raised p-5 shadow-ink-card"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="flex items-center gap-1.5 font-mono text-[11px] tracking-[0.14em] text-ink-muted uppercase">
                <BadgeCheck className="h-3.5 w-3.5 text-glow" /> Best match
              </p>
              <p className="mt-1.5 font-serif text-[26px] leading-tight text-ink-foreground">
                Nova Book 14
              </p>
            </div>
            <span className="relative grid h-12 w-12 shrink-0 place-items-center">
              <svg viewBox="0 0 48 48" className="absolute inset-0 -rotate-90" fill="none">
                <circle cx="24" cy="24" r="20" stroke="var(--ink-border)" strokeWidth="4" />
                <circle
                  cx="24"
                  cy="24"
                  r="20"
                  stroke="var(--glow)"
                  strokeWidth="4"
                  strokeLinecap="round"
                  pathLength={100}
                  strokeDasharray="86 100"
                />
              </svg>
              <span className="font-mono text-sm font-medium text-ink-foreground">86</span>
            </span>
          </div>
          <ul className="mt-4 space-y-2.5">
            {CRITERIA.map((c, i) => (
              <li key={c.label}>
                <div className="flex justify-between text-[12px] text-ink-muted">
                  <span>{c.label}</span>
                  <span className="font-mono text-ink-foreground">{c.value}</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-track">
                  <div
                    className="bar-fill h-full rounded-full bg-bar-glow"
                    style={{
                      width: `${c.value}%`,
                      ["--bar-delay" as string]: `${0.6 + i * 0.15}s`,
                    }}
                  />
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-4 flex items-center gap-2 text-[12px] text-ink-muted">
            <span className="relative flex h-2 w-2">
              <span className="animate-ring absolute inset-0 rounded-full bg-glow/50" />
              <span className="relative h-2 w-2 rounded-full bg-glow" />
            </span>
            Every value linked to its source
          </p>
        </div>
      </div>
    </div>
  );
}
