import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useRef } from "react";
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

export function Navbar({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const menuButton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  // Closing gives focus back to the menu button, so it never drops to the page body.
  const close = useCallback(() => {
    onOpenChange(false);
    menuButton.current?.focus();
  }, [onOpenChange]);

  // While the small-screen menu is open, focus stays in it: the close button, then the panel's links.
  useEffect(() => {
    if (!open) return;
    const stops = (): HTMLElement[] =>
      [menuButton.current, ...(panel.current?.querySelectorAll("a") ?? [])].filter(
        (el) => el !== null,
      );
    stops()[1]?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
      if (e.key !== "Tab") return;
      e.preventDefault();
      const all = stops();
      const at = all.indexOf(document.activeElement as HTMLElement);
      all[(at + (e.shiftKey ? -1 : 1) + all.length) % all.length]?.focus();
    }
    // The menu exists only below the md breakpoint: if the screen grows, close it, or the page stays inert.
    const wide = window.matchMedia("(min-width: 48rem)");
    function onWidth() {
      if (wide.matches) onOpenChange(false);
    }
    document.addEventListener("keydown", onKey);
    wide.addEventListener("change", onWidth);
    return () => {
      document.removeEventListener("keydown", onKey);
      wide.removeEventListener("change", onWidth);
    };
  }, [open, close, onOpenChange]);

  return (
    <header className="sticky top-4 z-50 px-4">
      <nav
        className="mx-auto flex max-w-5xl items-center justify-between rounded-full border border-border bg-surface/90 py-2 pl-5 pr-2 shadow-soft backdrop-blur"
        aria-label="Main"
      >
        <div inert={open}>
          <BrandMark />
        </div>
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
          <div inert={open}>
            <ThemeToggle />
          </div>
          <button
            ref={menuButton}
            className="grid h-11 w-11 place-items-center rounded-full hover:bg-secondary md:hidden"
            onClick={() => (open ? close() : onOpenChange(true))}
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </nav>
      {open && (
        <div
          ref={panel}
          className="mx-auto mt-2 max-h-[calc(100dvh-6rem)] max-w-5xl overflow-y-auto rounded-2xl border border-border bg-surface p-3 shadow-soft md:hidden"
        >
          {links.map((l) => (
            <Link
              key={l.to}
              to={l.to}
              onClick={close}
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
              onClick={close}
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
