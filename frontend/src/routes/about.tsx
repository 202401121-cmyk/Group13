import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

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
  component: () => (
    <ComingSoon
      title="About ShopSense"
      text="ShopSense helps you research products against your needs and buy with evidence."
    />
  ),
});
