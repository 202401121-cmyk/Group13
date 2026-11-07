import { createFileRoute } from "@tanstack/react-router";
import { ArrowRight, MessageSquareText, Scale, ShieldCheck, Link2, Tag, Split } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { Badge, SectionHeading } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { Typewriter } from "@/components/shopsense/Typewriter";
import { useState } from "react";
import { ResearchInput } from "@/components/shopsense/ResearchInput";
import { ResearchPipeline } from "@/components/shopsense/ResearchPipeline";
import { useVisualMotion } from "@/hooks/useVisualMotion";
import { HERO_FONTS, RUPEE_FONTS } from "@/lib/fonts";

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
          <div
            className="pointer-events-none absolute inset-0 bg-grain opacity-[0.04]"
            aria-hidden
          />
          <div className="relative mx-auto max-w-6xl px-6 pb-20 pt-24 md:pt-32">
            <div className="max-w-3xl">
              <Badge>AI-POWERED SHOPPING RESEARCH</Badge>
              <Typewriter
                as="h1"
                className="mt-8 font-serif text-5xl leading-[1.05] tracking-[-0.015em] text-foreground sm:text-6xl md:text-7xl"
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
                <a href="#start" className={pillButton({ size: "lg" })}>
                  Start researching <ArrowRight className="h-4 w-4" />
                </a>
                <a href="#how" className={pillButton({ variant: "link", size: "lg" })}>
                  See how it works <ArrowRight className="h-4 w-4" />
                </a>
              </div>
            </div>
          </div>
          <div className="relative mx-auto max-w-6xl px-6 pb-16">
            <dl className="grid gap-4 sm:grid-cols-3">
              {assurances.map((a) => (
                <div
                  key={a.value}
                  data-reveal
                  className="interactive-card grid grid-cols-[2.5rem_1fr] items-center gap-x-4 gap-y-1 rounded-2xl border border-border bg-card p-6 shadow-soft"
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
                  className="interactive-card group relative flex flex-col rounded-2xl border border-border bg-card p-7 shadow-soft"
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

        <section id="start" data-reveal className="mx-auto max-w-4xl px-6 py-20">
          <ResearchInput />
        </section>
      </div>
    </PageShell>
  );
}
