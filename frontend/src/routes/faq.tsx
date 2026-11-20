import { createFileRoute, Link } from "@tanstack/react-router";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PageHero } from "@/components/shopsense/PageHero";

export const Route = createFileRoute("/faq")({
  head: () => ({
    meta: [
      { title: "FAQ — ShopSense" },
      { name: "description", content: "Answers to common questions about ShopSense." },
      { property: "og:title", content: "FAQ — ShopSense" },
      { property: "og:description", content: "Answers to common questions about ShopSense." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Faq,
});

const linkCls = "font-medium text-foreground underline underline-offset-4 hover:text-primary";

const QUESTIONS: { q: string; a: ReactNode }[] = [
  {
    q: "Is ShopSense free?",
    a: "Yes. ShopSense is a student project, and there is nothing to pay.",
  },
  {
    q: "Where do the results come from?",
    a: "From Reddit threads, expert reviews, video reviews and store listings, read while you wait. Every value on a result links to the source it came from, with the exact words.",
  },
  {
    q: "Does an AI choose the product for me?",
    a: "No. Language models read the sources and pull out each claim with its quote. The ranking itself is done by our own code, with the weights from your brief, and you can see what each product was weighed on.",
  },
  {
    q: "Do I need an account?",
    a: (
      <>
        No. Anyone can search as a guest. If you{" "}
        <Link to="/sign-in" className={linkCls}>
          sign in with Google
        </Link>
        , every search you finish is kept in My searches, for you alone.
      </>
    ),
  },
  {
    q: "What do you keep about me?",
    a: (
      <>
        What you typed, what we understood, your priorities and the finished result. As a guest,
        nothing that names you: no name, email, IP address or browser. If you sign in, the sign-in
        service also keeps your email and, for each sign-in, the IP address and browser. The full
        list is in{" "}
        <Link to="/privacy" className={linkCls}>
          Privacy
        </Link>
        .
      </>
    ),
  },
  {
    q: "Why does a value say “Not verified”?",
    a: "We couldn't find a source for it. We never count a missing value as zero, so it doesn't count against the product.",
  },
  {
    q: "How long does a search take?",
    a: "Usually a minute or two. You see each source as it is read, and you can stop the search at any time.",
  },
  {
    q: "Can I change what matters after the questions?",
    a: "Yes. On your brief you can switch a must-have to a preference, rule out brands or features, and set how much each thing counts, before the research starts.",
  },
  {
    q: "Why is a price different at the store?",
    a: "Prices are checked when your search runs, and stores change them. Each price shows when it was checked: check the store before you buy.",
  },
];

function Faq() {
  return (
    <PageShell>
      <PageHero
        eyebrow="FAQ"
        title="Questions, answered"
        lead="How ShopSense works, where its facts come from, and what it keeps."
      />
      <section aria-label="Questions" className="mx-auto max-w-3xl px-6 pb-8">
        <div className="surface-raised divide-y divide-border overflow-hidden rounded-3xl">
          {QUESTIONS.map(({ q, a }) => (
            <details key={q} className="faq-item group">
              <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-4 px-6 py-4 text-left font-medium text-foreground transition-colors hover:bg-secondary/60 [&::-webkit-details-marker]:hidden">
                {q}
                <ChevronDown
                  className="h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-300 group-open:rotate-180"
                  aria-hidden
                />
              </summary>
              <p className="px-6 pb-5 leading-relaxed text-muted-foreground">{a}</p>
            </details>
          ))}
        </div>
      </section>
    </PageShell>
  );
}
