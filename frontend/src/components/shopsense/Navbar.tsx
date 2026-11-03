import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { Menu, X } from "lucide-react";
import { BrandMark } from "./ui";
import { pillButton } from "./pill-button";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";

const links = [
  { to: "/", label: "Home" },
  { to: "/research", label: "Research" },
] as const;

const linkCls =
  "inline-flex min-h-11 items-center rounded-full px-4 text-sm text-muted-foreground transition-colors hover:text-foreground";
const activeCls = "bg-accent text-accent-foreground font-medium";

export function Navbar() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-4 z-50 px-4">
      <nav
        className="mx-auto flex max-w-5xl items-center justify-between rounded-full border border-border bg-surface/90 py-2 pl-5 pr-2 shadow-soft backdrop-blur"
        aria-label="Main"
      >
        <BrandMark />
        <ul className="hidden items-center gap-1 md:flex">
          {links.map((l) => (
            <li key={l.to}>
              <Link
                to={l.to}
                className={linkCls}
                activeProps={{ className: activeCls }}
                activeOptions={{ exact: true }}
              >
                {l.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="hidden items-center gap-1 md:flex">
          <ThemeToggle />
          <Link to="/research" className={pillButton({ size: "sm" })}>
            Get started
          </Link>
        </div>
        <div className="flex items-center md:hidden">
          <ThemeToggle />
          <button
            className="grid h-11 w-11 place-items-center rounded-full hover:bg-secondary md:hidden"
            onClick={() => setOpen(!open)}
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </nav>
      {open && (
        <div className="mx-auto mt-2 max-w-5xl rounded-2xl border border-border bg-surface p-3 shadow-soft md:hidden">
          {links.map((l) => (
            <Link
              key={l.to}
              to={l.to}
              onClick={() => setOpen(false)}
              className={cn(linkCls, "flex")}
              activeProps={{ className: activeCls }}
              activeOptions={{ exact: true }}
            >
              {l.label}
            </Link>
          ))}
          <div className="mt-2 flex gap-2 border-t border-border pt-3">
            <Link
              to="/research"
              onClick={() => setOpen(false)}
              className={cn(pillButton({ size: "sm" }), "flex-1")}
            >
              Get started
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
