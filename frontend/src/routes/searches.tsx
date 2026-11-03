import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

export const Route = createFileRoute("/searches")({
  head: () => ({
    meta: [
      { title: "My Searches — ShopSense" },
      { name: "description", content: "Your past research sessions will appear here." },
      { property: "og:title", content: "My Searches — ShopSense" },
      { property: "og:description", content: "Your past research sessions will appear here." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => <ComingSoon title="My Searches" text="Your past research sessions will appear here." />,
});
