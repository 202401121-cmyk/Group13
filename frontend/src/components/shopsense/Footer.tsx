import { Link } from "@tanstack/react-router";
import { BrandMark } from "./ui";

type FooterLink = {
  label: string;
  to: "/" | "/research" | "/features" | "/about" | "/contact" | "/faq" | "/privacy";
  hash?: string;
};

const footerGroups: { label: string; links: FooterLink[] }[] = [
  {
    label: "Product",
    links: [
      { label: "Home", to: "/" },
      { label: "How It Works", to: "/", hash: "how" },
      { label: "Research", to: "/research" },
      { label: "Features", to: "/features" },
    ],
  },
  {
    label: "Company",
    links: [
      { label: "About", to: "/about" },
      { label: "Contact", to: "/contact" },
      { label: "FAQ", to: "/faq" },
      { label: "Privacy Policy", to: "/privacy" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="mt-24 border-t border-border bg-background">
      <div className="mx-auto flex max-w-6xl flex-col gap-10 px-6 py-12 md:flex-row md:justify-between md:gap-8">
        <div className="max-w-xs">
          <BrandMark />
          <p className="mt-3 text-sm text-muted-foreground">
            Research smarter. Choose confidently.
          </p>
        </div>
        <nav
          aria-label="Footer"
          className="grid grid-cols-2 gap-x-10 gap-y-8 text-sm sm:gap-x-16 md:gap-x-20"
        >
          {footerGroups.map((group) => (
            <div key={group.label}>
              <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                {group.label}
              </h2>
              <ul className="mt-2">
                {group.links.map((link) => (
                  <li key={link.label}>
                    <Link
                      to={link.to}
                      {...(link.hash ? { hash: link.hash } : {})}
                      className="inline-flex min-h-11 min-w-11 items-center text-muted-foreground transition-colors hover:text-primary"
                    >
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </div>
      <div className="mx-auto max-w-6xl px-6">
        <div className="border-t border-border py-6">
          <p className="text-xs text-muted-foreground">
            © {new Date().getFullYear()} ShopSense. All rights reserved.
          </p>
        </div>
      </div>
    </footer>
  );
}
