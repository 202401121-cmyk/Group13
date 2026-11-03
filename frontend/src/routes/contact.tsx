import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "@/components/shopsense/PageShell";

export const Route = createFileRoute("/contact")({
  head: () => ({
    meta: [
      { title: "Contact — ShopSense" },
      { name: "description", content: "Get in touch with the ShopSense team." },
      { property: "og:title", content: "Contact — ShopSense" },
      { property: "og:description", content: "Get in touch with the ShopSense team." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => (
    <ComingSoon title="Contact" text="A way to reach the ShopSense team is on its way." />
  ),
});
