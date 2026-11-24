import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRight, Calculator, CircleHelp, Lock, Quote } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PageHero } from "@/components/shopsense/PageHero";
import { pillButton } from "@/components/shopsense/pill-button";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/about")({
  head: () => ({
    meta: [
      { title: "About ShopSense — ShopSense" },
      {
        name: "description",
        content: "ShopSense helps you research products against your needs and buy with evidence.",
      },
      { property: "og:title", content: "About ShopSense — ShopSense" },
      {
        property: "og:description",
        content: "ShopSense helps you research products against your needs and buy with evidence.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: About,
});

const PRINCIPLES = [
  {
    icon: Quote,
    title: "Every claim keeps its source",
    text: "A value without a source is never shown as a fact. Each one links to the page it came from, with the exact words.",
  },
  {
    icon: Calculator,
    title: "Code decides, not a model",
    text: "Language models read the sources. The ranking is plain arithmetic on your weights, so it can be checked.",
  },
  {
    icon: CircleHelp,
    title: "Unknown is not bad",
    text: "When we can't find a value we say “Not verified”. We never count it as zero against a product.",
  },
  {
    icon: Lock,
    title: "Your words stay yours",
    text: "What you type is never sent to a service that may train on it, and a guest's search is never linked to an account.",
  },
];

function About() {
  return (
    <PageShell>
      <PageHero
        eyebrow="About ShopSense"
        title={
          <>
            Shopping research, <em className="text-primary">without the guesswork.</em>
          </>
        }
        lead="ShopSense helps you research products against your needs and buy with evidence. Tell it what you want in plain English; it reads what owners and reviewers say, compares the trade-offs that matter to you, and shows where every fact came from."
      />

      <section
        aria-labelledby="why-title"
        className="mx-auto grid max-w-6xl gap-10 px-6 py-16 md:grid-cols-[1fr_1.4fr]"
      >
        <h2
          id="why-title"
          className="font-serif text-4xl leading-tight text-foreground md:text-5xl"
        >
          Why we built it
        </h2>
        <div className="space-y-5 text-lg leading-relaxed text-muted-foreground">
          <p>
            Buying a laptop or earbuds can mean a dozen open tabs: spec sheets, reviews that
            disagree, Reddit threads, videos and store pages with different prices. The answer is in
            there somewhere. But finding it takes hours, and it is hard to tell a fact from a sales
            pitch.
          </p>
          <p>
            ShopSense does that reading for you, and keeps the receipts. You can see why a product
            came first, what it was weighed on, and the source behind every value, so the decision
            stays yours.
          </p>
        </div>
      </section>

      <section aria-labelledby="principles-title" className="mx-auto max-w-6xl px-6 py-16">
        <h2 id="principles-title" className="font-serif text-4xl text-foreground md:text-5xl">
          What we hold to
        </h2>
        <div className="mt-10 grid gap-5 sm:grid-cols-2">
          {PRINCIPLES.map(({ icon: Icon, title, text }) => (
            <article key={title} className="interactive-card surface-raised rounded-3xl p-7">
              <span className="grid h-11 w-11 place-items-center rounded-2xl bg-primary-soft text-primary-soft-foreground">
                <Icon className="h-5 w-5" aria-hidden />
              </span>
              <h3 className="mt-6 font-serif text-2xl text-foreground">{title}</h3>
              <p className="mt-3 leading-relaxed text-muted-foreground">{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="team-title" className="mx-auto max-w-6xl px-6 py-16">
        <div className="bg-ink-glow relative overflow-hidden rounded-[2rem] p-10 md:p-14">
          <div
            className="bg-blueprint pointer-events-none absolute inset-0 opacity-40"
            aria-hidden
          />
          <div className="relative max-w-2xl">
            <h2 id="team-title" className="font-serif text-4xl text-ink-foreground">
              Who makes it
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-muted">
              ShopSense is a student software-engineering project, built by IT314 Group 13. It is
              free to use while we build it, and it is honest about what it can't do yet.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                to="/research"
                className={cn(
                  pillButton({ size: "lg" }),
                  "sheen bg-ink-foreground text-ink hover:brightness-95",
                )}
              >
                Start a search <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
              <Link
                to="/contact"
                className="inline-flex min-h-13 items-center rounded-full border border-ink-border px-7 font-medium text-ink-foreground transition-colors hover:border-ink-muted"
              >
                Contact us
              </Link>
            </div>
          </div>
        </div>
      </section>
    </PageShell>
  );
}
