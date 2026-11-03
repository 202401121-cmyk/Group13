import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

export const Route = createFileRoute("/privacy")({
  head: () => ({
    meta: [
      { title: "Privacy Policy — ShopSense" },
      { name: "description", content: "How ShopSense handles your data." },
      { property: "og:title", content: "Privacy Policy — ShopSense" },
      { property: "og:description", content: "How ShopSense handles your data." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => (
    <ComingSoon
      title="Privacy Policy"
      text="How ShopSense handles your data will be documented here."
    />
  ),
});
