import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

export const Route = createFileRoute("/saved")({
  head: () => ({
    meta: [
      { title: "Saved Products — ShopSense" },
      { name: "description", content: "Products you save will live here." },
      { property: "og:title", content: "Saved Products — ShopSense" },
      { property: "og:description", content: "Products you save will live here." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => <ComingSoon title="Saved Products" text="Products you save will live here." />,
});
