import { createFileRoute } from "@tanstack/react-router";
import {
  ArrowRight,
  Link2,
  MessageSquareText,
  Quote,
  Scale,
  ShieldCheck,
  Split,
  Tag,
} from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { Badge, SectionHeading } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { Typewriter } from "@/components/shopsense/Typewriter";
import { useState } from "react";
import { ResearchInput } from "@/components/shopsense/ResearchInput";
import { ResearchPipeline } from "@/components/shopsense/ResearchPipeline";
import { EvidenceGraph } from "@/components/shopsense/EvidenceGraph";
import { useVisualMotion } from "@/hooks/useVisualMotion";
import { HERO_FONTS, RUPEE_FONTS } from "@/lib/fonts";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "ShopSense — Find the right product, with evidence" },
      {
        name: "description",
        content:
          "AI-powered shopping research that compares products against your needs so you can buy with confidence.",
      },
      { property: "og:title", content: "ShopSense — Find the right product, with evidence" },
      {
        property: "og:description",
        content: "AI-powered shopping research that compares products against your needs.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
    links: [...HERO_FONTS, ...RUPEE_FONTS],
  }),
  component: Index,
});

const steps = [
  {
    icon: MessageSquareText,
    title: "Tell us what matters",
    text: "Describe what you need, your budget and the features you care about — in your own words.",
  },
  {
    icon: Scale,
    title: "Research and compare",
    text: "We read reviews, Reddit and stores, then compare the real trade-offs against your criteria.",
  },
  {
    icon: ShieldCheck,
    title: "Choose with confidence",
    text: "Get a clear best match, the reasons behind it, and a source for every fact.",
  },
];

const assurances = [
  { icon: Link2, value: "Every fact", label: "linked to its source" },
  { icon: Tag, value: "Prices", label: "checked before you buy" },
  { icon: Split, value: "Trade-offs", label: "stated, not hidden" },
];

/** What ShopSense reads: the kinds of source, and the review sites it reads directly (config/research.yaml). */
const SOURCES = [
  "Reddit threads",
  "Video reviews",
  "Store listings",
  "Makers' own pages",
  "RTINGS",
  "GSMArena",
  "Notebookcheck",
  "Gadgets 360",
  "Digit",
  "91mobiles",
  "Smartprix",
];

function SourceList() {
  return (
    <ul className="mx-auto flex max-w-4xl flex-wrap justify-center gap-2.5 px-6">
      {SOURCES.map((s) => (
        <li
          key={s}
          className="rounded-full border border-border bg-card px-4 py-2 text-sm text-foreground shadow-[var(--highlight)] transition-colors hover:border-primary/50"
        >
          {s}
        </li>
      ))}
    </ul>
  );
}

/** A result's value with its receipt: the claim, the exact quote and where it came from (an illustration). */
function Receipt() {
  return (
    <div
      role="img"
      aria-label="An illustration: a battery life value, with the quote and the source behind it"
      data-motion-loop
      className="relative mx-auto w-full max-w-md"
    >
      <div aria-hidden className="surface-raised rounded-3xl p-6">
        <p className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
          Nova Book 14 · Battery life
        </p>
        <div className="mt-3 flex items-end justify-between">
          <p className="font-serif text-4xl text-foreground">11 h 40 m</p>
          <span className="rounded-full bg-primary-soft px-3 py-1 text-xs font-medium text-primary-soft-foreground">
            3 sources agree
          </span>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-secondary">
          <div className="h-full w-[88%] rounded-full bg-primary" />
        </div>
      </div>
      <div
        aria-hidden
        className="animate-float surface-raised relative -mt-3 ml-8 mr-[-1rem] rounded-3xl border-l-4 border-l-primary p-6 sm:ml-14"
      >
        <Quote className="h-5 w-5 text-primary" />
        <p className="mt-3 font-serif text-lg leading-snug text-foreground">
          “On our standard browsing test it lasted eleven hours and forty minutes, well past a full
          workday.”
        </p>
        <div className="mt-4 flex items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">Expert review · checked today</span>
          <span className="rounded-full bg-secondary px-2.5 py-1 text-xs text-secondary-foreground">
            Source kept
          </span>
        </div>
      </div>
    </div>
  );
}

function Index() {
  const motionRef = useVisualMotion<HTMLDivElement>();
  const [heroDone, setHeroDone] = useState(false);
  const [howDone, setHowDone] = useState(false);
  return (
    <PageShell>
      <div ref={motionRef}>
        <section className="relative overflow-hidden">
          <div
            className="pointer-events-none absolute inset-0 bg-ambient animate-ambient"
            data-motion-loop
            aria-hidden
          />
          <div className="bg-blueprint pointer-events-none absolute inset-0" aria-hidden />
          <div
            className="pointer-events-none absolute inset-0 bg-grain opacity-[0.04]"
            aria-hidden
          />
          <div className="relative mx-auto grid max-w-6xl items-center gap-14 px-6 pb-16 pt-20 md:pt-28 lg:grid-cols-[1.05fr_1fr] lg:gap-10">
            <div className="max-w-2xl">
              <Badge>AI-POWERED SHOPPING RESEARCH</Badge>
              <Typewriter
                as="h1"
                className="mt-8 font-serif text-5xl leading-[1.03] tracking-[-0.02em] text-foreground sm:text-6xl md:text-7xl"
                speed={55}
                onDone={() => setHeroDone(true)}
                segments={[
                  "Find the right product,",
                  { br: true },
                  { text: "with evidence.", as: "em", className: "text-primary" },
                ]}
              />
              <Typewriter
                className="mt-7 max-w-xl text-lg leading-relaxed text-muted-foreground"
                speed={14}
                start={heroDone}
                segments={[
                  "Stop jumping between reviews, product pages and comparison videos. ShopSense researches products against your needs, compares the differences that matter and shows where every fact came from.",
                ]}
              />
              <div className="mt-10 flex flex-wrap items-center gap-3">
                <a href="#start" className={cn(pillButton({ size: "lg" }), "sheen")}>
                  Start researching <ArrowRight className="h-4 w-4" />
                </a>
                <a href="#how" className={pillButton({ variant: "link", size: "lg" })}>
                  See how it works <ArrowRight className="h-4 w-4" />
                </a>
              </div>
            </div>
            <div className="relative">
              <div
                aria-hidden
                className="pointer-events-none absolute -inset-6 rounded-[2.5rem] bg-[radial-gradient(60%_60%_at_50%_45%,color-mix(in_oklab,var(--primary)_22%,transparent),transparent_70%)] blur-2xl"
              />
              <div className="bg-ink-glow relative overflow-hidden rounded-[2rem] border border-ink-border p-5 shadow-float sm:p-7">
                <div
                  className="bg-blueprint pointer-events-none absolute inset-0 opacity-40"
                  aria-hidden
                />
                <div className="relative mb-2 flex flex-wrap items-center gap-1.5" aria-hidden>
                  <span className="h-2.5 w-2.5 rounded-full bg-ink-dot" />
                  <span className="h-2.5 w-2.5 rounded-full bg-ink-dot" />
                  <span className="h-2.5 w-2.5 rounded-full bg-ink-dot" />
                  <span className="ml-3 min-w-0 font-mono text-[11px] tracking-[0.14em] text-ink-muted uppercase">
                    {/* The ₹ in Geist: Geist Mono has no small rupee file (fonts.spec.ts). */}
                    Laptop for coding · <span className="font-sans">₹</span>70,000
                  </span>
                </div>
                <EvidenceGraph className="relative" />
              </div>
            </div>
          </div>
          <div className="relative mx-auto max-w-6xl px-6 pb-16">
            <dl className="grid gap-4 sm:grid-cols-3">
              {assurances.map((a) => (
                <div
                  key={a.value}
                  data-reveal
                  className="interactive-card surface-raised grid grid-cols-[2.5rem_1fr] items-center gap-x-4 gap-y-1 rounded-2xl p-6"
                >
                  {/* A div in a <dl> may hold only <dt> then <dd>: the grid shows the value first. */}
                  <dt className="col-start-2 row-start-2 font-mono text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
                    {a.label}
                  </dt>
                  <dd className="col-span-2 row-start-1 flex items-center gap-4">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-primary-soft text-primary-soft-foreground">
                      <a.icon className="h-4 w-4" aria-hidden />
                    </span>
                    <Typewriter
                      as="span"
                      className="font-serif text-2xl text-foreground"
                      speed={60}
                      segments={[a.value]}
                    />
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section aria-labelledby="reads-title" className="py-14">
          <div className="mx-auto max-w-6xl px-6">
            <h2
              id="reads-title"
              className="text-center font-mono text-xs tracking-[0.14em] text-muted-foreground uppercase"
            >
              What ShopSense reads for you
            </h2>
          </div>
          <div className="mt-6">
            <SourceList />
          </div>
        </section>

        <section id="how" className="border-y border-border bg-secondary/60">
          <div className="mx-auto max-w-6xl px-6 py-24">
            <div data-reveal className="grid gap-6 md:grid-cols-[1.2fr_1fr] md:items-end">
              <SectionHeading
                eyebrow="HOW IT WORKS"
                title={
                  <Typewriter
                    as="span"
                    speed={45}
                    onDone={() => setHowDone(true)}
                    segments={["Better research. Clearer decisions."]}
                  />
                }
              />
              <Typewriter
                className="max-w-md border-l-2 border-primary pl-5 leading-relaxed text-muted-foreground"
                speed={20}
                start={howDone}
                segments={[
                  "Three steps from a vague need to a decision you can defend — with the evidence one click away.",
                ]}
              />
            </div>
            <ol className="relative mt-14 grid gap-5 md:grid-cols-3">
              <div
                className="pointer-events-none absolute left-[16%] right-[16%] top-[2.6rem] hidden border-t border-dashed border-primary/40 md:block"
                aria-hidden
              />
              {steps.map((s, i) => (
                <li
                  key={s.title}
                  data-reveal
                  className="interactive-card surface-raised group relative flex flex-col rounded-2xl p-7"
                >
                  <div className="flex items-center justify-between">
                    <span className="grid h-11 w-11 place-items-center rounded-full border border-border bg-primary-soft text-primary-soft-foreground transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                      <s.icon className="h-4 w-4" aria-hidden />
                    </span>
                    <span className="font-mono text-xs tracking-[0.14em] text-muted-foreground">
                      STEP 0{i + 1}
                    </span>
                  </div>
                  <Typewriter
                    as="h3"
                    className="mt-8 font-serif text-2xl text-foreground"
                    speed={40}
                    start={howDone}
                    segments={[s.title]}
                  />
                  <p className="mt-3 leading-relaxed text-muted-foreground">{s.text}</p>
                </li>
              ))}
            </ol>
            <ResearchPipeline />
          </div>
        </section>

        <section aria-labelledby="receipts-title" className="mx-auto max-w-6xl px-6 py-24">
          <div className="grid items-center gap-14 lg:grid-cols-2">
            <div data-reveal>
              <p className="font-mono text-xs tracking-[0.14em] text-muted-foreground uppercase">
                Evidence, not adjectives
              </p>
              <h2
                id="receipts-title"
                className="mt-3 font-serif text-4xl leading-tight text-foreground md:text-5xl"
              >
                Every fact comes with its receipt
              </h2>
              <p className="mt-5 max-w-lg text-lg leading-relaxed text-muted-foreground">
                Click any value on a result to see the exact words behind it and the page they came
                from. When sources disagree, you see both sides. When we can't find a value, we say
                so.
              </p>
            </div>
            <div data-reveal>
              <Receipt />
            </div>
          </div>
        </section>

        <section id="start" data-reveal className="mx-auto max-w-4xl px-6 pb-20 pt-4">
          <ResearchInput />
        </section>
      </div>
    </PageShell>
  );
}
