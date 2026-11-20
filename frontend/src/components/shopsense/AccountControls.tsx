/**
 * Sign-in in the navigation (US-30): "Sign in", which opens the sign-in page, or the shopper's initials, which open
 * their account menu (My searches, their profile, privacy, Sign out). In the small-screen menu the same, laid out flat.
 *
 * Guests see everything they saw before; sign-in only adds. While the session is read at page load the controls keep
 * their place but show nothing, so the bar doesn't jump. With no Supabase settings (live mode only) there is no sign-in
 * at all.
 */
import { useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { AlertTriangle, LogOut, Search, UserRound, X } from "lucide-react";
import { pillButton } from "./pill-button";
import { AccountMenu } from "@/components/account/AccountMenu";
import { Avatar } from "@/components/account/Avatar";
import { getAuthService, useAuth } from "@/lib/auth/service";
import { returnPath } from "@/lib/auth/types";
import { useAccountName } from "@/hooks/useAccountName";
import { cn } from "@/lib/utils";

/** Where the shopper is now, to come back to after signing in or out. */
function useHere(): string {
  return useRouterState({ select: (s) => s.location.href });
}

export function AccountControls({
  layout,
  onNavigate,
}: {
  layout: "bar" | "panel";
  onNavigate?: () => void;
}) {
  const state = useAuth();
  const name = useAccountName(state);
  const here = useHere();
  const [busy, setBusy] = useState(false);
  const auth = getAuthService();

  if (state.status === "unavailable") return null;
  if (state.status === "loading") {
    // Keeps the control's place, so the bar doesn't move when the session has been read.
    return (
      <span aria-hidden className={cn("inline-block h-11", layout === "bar" ? "w-20" : "w-full")} />
    );
  }

  const signOut = () => {
    setBusy(true);
    void auth.signOut(here).finally(() => setBusy(false));
  };

  if (state.status === "guest") {
    return (
      <Link
        to="/sign-in"
        search={{ next: returnPath(here) }}
        onClick={onNavigate}
        className={cn(
          pillButton({ variant: layout === "bar" ? "ghost" : "outline", size: "sm" }),
          layout === "panel" && "flex-1",
        )}
      >
        Sign in
      </Link>
    );
  }

  if (layout === "bar") {
    return <AccountMenu user={state.user} name={name} busy={busy} onSignOut={signOut} />;
  }

  // The small-screen menu: who is signed in, then their places, then Sign out, as full-width rows.
  const row =
    "flex min-h-12 w-full items-center gap-3 rounded-2xl px-3 text-left text-[15px] text-foreground transition-colors hover:bg-secondary";
  return (
    <div className="flex w-full flex-col">
      <div className="flex items-center gap-3 rounded-2xl bg-secondary/70 p-3">
        <Avatar user={state.user} size="md" />
        <div className="min-w-0">
          <p className="truncate font-medium text-foreground">{name}</p>
          {state.user.email && (
            <p className="truncate text-sm text-muted-foreground">{state.user.email}</p>
          )}
        </div>
      </div>
      <div className="mt-1 flex flex-col">
        <Link to="/my-searches" onClick={onNavigate} className={row}>
          <Search className="h-4 w-4 text-muted-foreground" aria-hidden />
          My searches
        </Link>
        <Link to="/account" onClick={onNavigate} className={row}>
          <UserRound className="h-4 w-4 text-muted-foreground" aria-hidden />
          Your profile
        </Link>
        <button type="button" disabled={busy} onClick={signOut} className={row}>
          <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden />
          Sign out
        </button>
      </div>
    </div>
  );
}

/** The sentence for a sign-in that didn't work, under the navigation, until it is closed. */
export function SignInError() {
  const state = useAuth();
  const [closed, setClosed] = useState(false);
  if (state.status !== "guest" || !state.error || closed) return null;
  return (
    <div
      role="alert"
      className="mx-auto mt-2 flex max-w-5xl items-center justify-between gap-3 rounded-2xl border border-destructive/40 bg-card py-1 pl-4 pr-1 text-sm text-foreground shadow-soft"
    >
      <p className="flex min-w-0 items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />
        <span>{state.error}</span>
      </p>
      <button
        type="button"
        className="grid h-11 w-11 shrink-0 place-items-center rounded-full hover:bg-secondary"
        aria-label="Close this message"
        onClick={() => setClosed(true)}
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
