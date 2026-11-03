import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

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
  component: () => <ComingSoon title="FAQ" text="Answers to common questions are on their way." />,
});
