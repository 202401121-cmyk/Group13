import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowRight,
  Bookmark,
  Calculator,
  Gauge,
  ListChecks,
  MessageSquareText,
  Quote,
  Radio,
  SlidersHorizontal,
  Tag,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PageHero } from "@/components/shopsense/PageHero";
import { EvidenceGraph } from "@/components/shopsense/EvidenceGraph";
import { pillButton } from "@/components/shopsense/pill-button";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/features")({
  head: () => ({
    meta: [
      { title: "Features — ShopSense" },
      {
        name: "description",
        content:
          "What ShopSense does: plain-English requests, a short interview, live research and a source for every fact.",
      },
      { property: "og:title", content: "Features — ShopSense" },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Features,
});

function Feature({
  icon: Icon,
  title,
  children,
  className,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <article className={cn("interactive-card surface-raised rounded-3xl p-7", className)}>
      <span className="grid h-11 w-11 place-items-center rounded-2xl bg-primary-soft text-primary-soft-foreground">
        <Icon className="h-5 w-5" aria-hidden />
      </span>
      <h3 className="mt-6 font-serif text-2xl text-foreground">{title}</h3>
      <div className="mt-3 leading-relaxed text-muted-foreground">{children}</div>
    </article>
  );
}

function Features() {
  return (
    <PageShell>
      <PageHero
        eyebrow="Features"
        title={
          <>
            From a vague need to a decision <em className="text-primary">you can defend.</em>
          </>
        }
        lead="ShopSense reads what people and reviewers say, weighs it against what matters to you, and shows where every fact came from."
      />

      <section aria-labelledby="ask-title" className="mx-auto max-w-6xl px-6 py-16">
        <h2 id="ask-title" className="font-serif text-4xl text-foreground md:text-5xl">
          You say what you need
        </h2>
        <div className="mt-10 grid gap-5 md:grid-cols-3">
          <Feature icon={MessageSquareText} title="Plain-English requests">
            Type it as you'd say it: “a laptop for coding under ₹70,000, at least 16 GB RAM”. We
            work out the product, the budget, your region and your must-haves.
          </Feature>
          <Feature icon={ListChecks} title="A short interview">
            A few quick questions find what matters most to you. Answer roughly (“high”, “low”) or
            give an exact number, skip any, and go back to change one.
          </Feature>
          <Feature icon={SlidersHorizontal} title="A brief you control">
            Switch a must-have to a preference, rule out brands or features in your own words, and
            set how much each thing counts before the research starts.
          </Feature>
        </div>
      </section>

      <section aria-labelledby="research-title" className="bg-ink-glow relative overflow-hidden">
        <div className="bg-blueprint pointer-events-none absolute inset-0 opacity-40" aria-hidden />
        <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-6 py-24 lg:grid-cols-2">
          <div>
            <p className="font-mono text-xs tracking-[0.14em] text-ink-muted uppercase">
              Live research
            </p>
            <h2
              id="research-title"
              className="mt-3 font-serif text-4xl text-ink-foreground md:text-5xl"
            >
              We read the sources, so you don't have to
            </h2>
            <p className="mt-5 max-w-lg leading-relaxed text-ink-muted">
              Reddit threads, expert reviews, video reviews and store listings, read in parallel
              while you watch the progress. Every value we keep comes with its exact quote and where
              it was found.
            </p>
            <ul className="mt-8 space-y-4">
              {[
                { icon: Radio, text: "Progress as it happens, source by source" },
                { icon: Quote, text: "An exact quote behind every value" },
                { icon: Gauge, text: "Disputed values show both sides, never a guess" },
              ].map(({ icon: Icon, text }) => (
                <li key={text} className="flex items-center gap-3 text-ink-foreground">
                  <span className="grid h-9 w-9 place-items-center rounded-xl bg-ink-well text-glow">
                    <Icon className="h-4 w-4" aria-hidden />
                  </span>
                  {text}
                </li>
              ))}
            </ul>
          </div>
          <EvidenceGraph />
        </div>
      </section>

      <section aria-labelledby="decide-title" className="mx-auto max-w-6xl px-6 py-24">
        <h2 id="decide-title" className="font-serif text-4xl text-foreground md:text-5xl">
          You get an answer, and the reasons
        </h2>
        <div className="mt-10 grid gap-5 md:grid-cols-6">
          <Feature
            icon={Calculator}
            title="Ranked by rules, not by a guess"
            className="md:col-span-4"
          >
            Language models only read the sources and pull out each claim with its quote. The
            ranking is done by our own code, with your weights, never by a model. When two products
            are too close to call, we say so, and name what separates them.
          </Feature>
          <Feature icon={Tag} title="Prices, checked" className="md:col-span-2">
            Store prices with the time we checked them. An old price says it may have changed.
          </Feature>
          <Feature icon={Gauge} title="Honest gaps" className="md:col-span-2">
            A value we couldn't find is marked “Not verified”, never counted as zero.
          </Feature>
          <Feature icon={Bookmark} title="Your searches, kept" className="md:col-span-4">
            Sign in with Google and every search you finish is kept in My searches, with its
            sources, for you alone. Guests can search too: nothing is ever linked to an account
            later.
          </Feature>
        </div>
        <div className="mt-14 flex flex-wrap gap-3">
          <Link to="/research" className={cn(pillButton({ size: "lg" }), "sheen")}>
            Start a search <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
          <Link to="/faq" className={pillButton({ variant: "outline", size: "lg" })}>
            Read the FAQ
          </Link>
        </div>
      </section>
    </PageShell>
  );
}
