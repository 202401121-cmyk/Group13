/**
 * The signed-in shopper's menu (US-30): their initials in the navigation open a small panel with who they are, My
 * searches, their profile, privacy, and Sign out.
 *
 * A menu button as WAI-ARIA describes it: Enter, Space or Down opens it on the first item, Up on the last; the arrow
 * keys, Home and End move between items; Escape closes it and gives focus back to the button; Tab, or a click
 * anywhere else, closes it too, and so does going to another page.
 */
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { ChevronDown, LogOut, Search, ShieldCheck, UserRound } from "lucide-react";
import { Avatar } from "./Avatar";
import type { AuthUser } from "@/lib/auth/types";
import { cn } from "@/lib/utils";

const itemCls =
  "flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-sm text-foreground outline-none transition-colors hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring";

export function AccountMenu({
  user,
  name,
  busy,
  onSignOut,
}: {
  user: AuthUser;
  name: string;
  busy: boolean;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  // A new page closes the menu, also one reached with the browser's Back button.
  const href = useRouterState({ select: (s) => s.location.href });
  const [shownAt, setShownAt] = useState(href);
  if (href !== shownAt) {
    setShownAt(href);
    if (open) setOpen(false);
  }
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  const items = () => [...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];

  const close = useCallback((focusButton: boolean) => {
    setOpen(false);
    if (focusButton) button.current?.focus();
  }, []);

  // Opening focuses the first or the last item, as the key asked.
  const [focusAt, setFocusAt] = useState<"first" | "last">("first");
  useEffect(() => {
    if (!open) return;
    const all = items();
    (focusAt === "first" ? all[0] : all[all.length - 1])?.focus();
    function onPointer(e: PointerEvent) {
      const target = e.target as Node;
      if (!menu.current?.contains(target) && !button.current?.contains(target)) close(false);
    }
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open, focusAt, close]);

  function onButtonKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setFocusAt(e.key === "ArrowUp" ? "last" : "first");
      setOpen(true);
    }
  }

  function onMenuKey(e: KeyboardEvent<HTMLDivElement>) {
    const all = items();
    const at = all.indexOf(document.activeElement as HTMLElement);
    const go = (i: number) => all[(i + all.length) % all.length]?.focus();
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(all.length - 1);
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") {
      // Back to the button first, so Tab and Shift+Tab move on from there whichever browser it is.
      button.current?.focus();
      setOpen(false);
      return;
    } else return;
    e.preventDefault();
  }

  return (
    <div className="relative">
      <button
        ref={button}
        type="button"
        id={`${id}-button`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={`${id}-menu`}
        aria-label={`${name}, account menu`}
        onClick={() => {
          setFocusAt("first");
          setOpen((o) => !o);
        }}
        onKeyDown={onButtonKey}
        className={cn(
          "group flex min-h-11 items-center gap-2 rounded-full py-1 pl-1 pr-3 transition-colors hover:bg-secondary",
          open && "bg-secondary",
        )}
      >
        <Avatar user={user} size="sm" />
        <span className="hidden max-w-32 truncate text-sm font-medium text-foreground lg:inline">
          {name}
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            "h-4 w-4 text-muted-foreground transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div
          ref={menu}
          id={`${id}-menu`}
          role="menu"
          aria-labelledby={`${id}-button`}
          onKeyDown={onMenuKey}
          // A click on the name, a line or the padding keeps focus in the menu, so the keys still work.
          onMouseDown={(e) => {
            if (!(e.target as Element).closest('[role="menuitem"]')) e.preventDefault();
          }}
          className="animate-menu absolute right-0 top-full z-50 mt-3 w-72 rounded-2xl border border-border bg-popover p-2 text-popover-foreground shadow-[var(--highlight),var(--shadow-float)]"
        >
          <div className="flex items-center gap-3 rounded-xl px-3 pb-3 pt-2">
            <Avatar user={user} size="md" />
            <div className="min-w-0">
              <p className="truncate font-medium text-foreground">{name}</p>
              {user.email && <p className="truncate text-sm text-muted-foreground">{user.email}</p>}
            </div>
          </div>
          <div className="my-1 border-t border-border" role="none" />
          <Link
            to="/my-searches"
            role="menuitem"
            tabIndex={-1}
            className={itemCls}
            onClick={() => close(true)}
          >
            <Search className="h-4 w-4 text-muted-foreground" aria-hidden />
            My searches
          </Link>
          <Link
            to="/account"
            role="menuitem"
            tabIndex={-1}
            className={itemCls}
            onClick={() => close(true)}
          >
            <UserRound className="h-4 w-4 text-muted-foreground" aria-hidden />
            Your profile
          </Link>
          <Link
            to="/privacy"
            role="menuitem"
            tabIndex={-1}
            className={itemCls}
            onClick={() => close(true)}
          >
            <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden />
            Privacy
          </Link>
          <div className="my-1 border-t border-border" role="none" />
          <button
            type="button"
            role="menuitem"
            tabIndex={-1}
            disabled={busy}
            className={itemCls}
            onClick={() => {
              close(false);
              onSignOut();
            }}
          >
            <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
