import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

export const Route = createFileRoute("/features")({
  head: () => ({
    meta: [
      { title: "Features — ShopSense" },
      { name: "description", content: "What ShopSense does for your shopping research." },
      { property: "og:title", content: "Features — ShopSense" },
      { property: "og:description", content: "What ShopSense does for your shopping research." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => <ComingSoon title="Features" text="A closer look at what ShopSense does is on its way." />,
});
