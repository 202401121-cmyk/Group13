import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowUpRight, Bug, CircleHelp, ShieldCheck } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PageHero } from "@/components/shopsense/PageHero";

export const Route = createFileRoute("/contact")({
  head: () => ({
    meta: [
      { title: "Contact — ShopSense" },
      {
        name: "description",
        content: "How to reach the ShopSense team, report a problem or ask about your data.",
      },
      { property: "og:title", content: "Contact — ShopSense" },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Contact,
});

/** The project's public repository: where problems are reported and the code can be read. */
const REPOSITORY = "https://github.com/202401121-cmyk/Group13";

function Way({
  icon: Icon,
  title,
  text,
  action,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  text: string;
  action: ReactNode;
}) {
  return (
    <article className="interactive-card surface-raised flex flex-col rounded-3xl p-7">
      <span className="grid h-11 w-11 place-items-center rounded-2xl bg-primary-soft text-primary-soft-foreground">
        <Icon className="h-5 w-5" aria-hidden />
      </span>
      <h2 className="mt-6 font-serif text-2xl text-foreground">{title}</h2>
      <p className="mt-3 flex-1 leading-relaxed text-muted-foreground">{text}</p>
      <div className="mt-6">{action}</div>
    </article>
  );
}

const actionCls =
  "inline-flex min-h-11 items-center gap-1.5 rounded-full border border-border bg-card px-5 text-sm font-medium text-foreground transition-colors hover:border-primary/50";

function Contact() {
  return (
    <PageShell>
      <PageHero
        eyebrow="Contact"
        title="Talk to the team"
        lead="ShopSense is built in the open by IT314 Group 13. The quickest way to reach us is through the project's public repository."
      />
      <section
        aria-label="Ways to reach us"
        className="mx-auto grid max-w-6xl gap-5 px-6 pb-8 md:grid-cols-3"
      >
        <Way
          icon={Bug}
          title="Report a problem"
          text="Something broke, or a result looks wrong? Open an issue: say what you searched for and what you expected."
          action={
            <a
              href={`${REPOSITORY}/issues`}
              target="_blank"
              rel="noopener noreferrer"
              className={actionCls}
            >
              Open an issue <ArrowUpRight className="h-4 w-4" aria-hidden />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          }
        />
        <Way
          icon={CircleHelp}
          title="Ask a question"
          text="Most questions about how ShopSense works, and what it keeps, are answered in the FAQ."
          action={
            <Link to="/faq" className={actionCls}>
              Read the FAQ
            </Link>
          }
        />
        <Way
          icon={ShieldCheck}
          title="Your data"
          text="What we save for a guest and for a signed-in shopper, where it is stored and who can read it."
          action={
            <Link to="/privacy" className={actionCls}>
              Read Privacy
            </Link>
          }
        />
      </section>
    </PageShell>
  );
}
