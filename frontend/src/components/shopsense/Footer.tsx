import { Link } from "@tanstack/react-router";
import { useAuth } from "@/lib/auth/service";
import { BrandMark } from "./ui";

type FooterLink = {
  label: string;
  to:
    | "/"
    | "/research"
    | "/features"
    | "/about"
    | "/contact"
    | "/faq"
    | "/privacy"
    | "/sign-in"
    | "/my-searches"
    | "/account";
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
    label: "Account",
    links: [
      { label: "Sign in", to: "/sign-in" },
      { label: "My searches", to: "/my-searches" },
      { label: "Your profile", to: "/account" },
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
  const auth = useAuth().status;
  // As in the navigation: no account links where sign-in isn't set up, and no "Sign in" for someone signed in.
  const groups = footerGroups
    .filter((group) => group.label !== "Account" || auth !== "unavailable")
    .map((group) => ({
      ...group,
      links: group.links.filter((link) => link.to !== "/sign-in" || auth !== "signedIn"),
    }));
  return (
    <footer className="relative mt-24 overflow-hidden border-t border-border bg-background">
      <div className="bg-blueprint pointer-events-none absolute inset-0 opacity-50" aria-hidden />
      <div className="relative mx-auto grid max-w-6xl gap-12 px-6 pb-10 pt-14 md:grid-cols-[1.1fr_2fr]">
        <div className="max-w-xs">
          <BrandMark />
          <p className="mt-4 font-serif text-2xl leading-snug text-foreground">
            Research smarter. <span className="text-primary">Choose confidently.</span>
          </p>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            Every fact linked to its source, every price checked, every trade-off stated.
          </p>
        </div>
        <nav
          aria-label="Footer"
          className="grid grid-cols-2 gap-x-10 gap-y-8 text-sm sm:grid-cols-3"
        >
          {groups.map((group) => (
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
                      className="group inline-flex min-h-11 min-w-11 items-center gap-1.5 text-foreground/80 transition-colors hover:text-primary"
                    >
                      <span
                        className="h-px w-0 bg-primary transition-[width] duration-300 group-hover:w-3"
                        aria-hidden
                      />
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </div>
      <div className="relative mx-auto max-w-6xl px-6">
        <div className="flex flex-col gap-2 border-t border-border py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} ShopSense. All rights reserved.</p>
          <p className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden />A student project by
            IT314 Group 13
          </p>
        </div>
      </div>
    </footer>
  );
}
