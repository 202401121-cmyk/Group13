/**
 * Sign-in in the navigation (US-30): "Sign in", or the shopper's name with "Sign out", and "My searches".
 *
 * Guests see everything they saw before; sign-in only adds. While the session is read at page load the controls keep
 * their place but show nothing, so the bar doesn't jump. With no Supabase settings (live mode only) there is no sign-in
 * at all.
 */
import { useEffect, useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { AlertTriangle, X } from "lucide-react";
import { pillButton } from "./pill-button";
import { getAccountService } from "@/lib/account/service";
import { AccountError } from "@/lib/account/types";
import { getAuthService, useAuth } from "@/lib/auth/service";
import type { AuthState } from "@/lib/auth/types";
import { cn } from "@/lib/utils";

let profileFor: { userId: string; answer: Promise<string | null> } | null = null;

/** The profile's display name, asked for once per signed-in user however many controls show it. */
function askProfileName(userId: string): Promise<string | null> {
  if (profileFor?.userId !== userId) {
    // The first call after signing in also makes the shopper's profile (S4-10). A token the backend no longer
    // accepts ends the session here, so the shopper goes on as a guest.
    const answer = getAccountService()
      .me()
      .then(
        (me) => me.displayName,
        (e: unknown) => {
          if (e instanceof AccountError && e.kind === "signedOut")
            void getAuthService().dropSession();
          if (profileFor?.userId === userId) profileFor = null; // ask again next time
          return null;
        },
      );
    profileFor = { userId, answer };
  }
  return profileFor.answer;
}

/** The name to show: the profile's display name, else the one Google gave, else "Your account". */
function useAccountName(state: AuthState): string {
  const [profileName, setProfileName] = useState<string | null>(null);
  const userId = state.status === "signedIn" ? state.user.id : null;
  useEffect(() => {
    if (!userId) return;
    let live = true;
    void askProfileName(userId).then((name) => live && setProfileName(name));
    return () => {
      live = false;
    };
  }, [userId]);
  if (state.status !== "signedIn") return "";
  return profileName ?? state.user.name ?? "Your account";
}

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
    // Keeps the button's place, so the bar doesn't move when the session has been read.
    return (
      <span aria-hidden className={cn("inline-block h-11", layout === "bar" ? "w-20" : "w-full")} />
    );
  }

  const button = pillButton({ variant: layout === "bar" ? "ghost" : "outline", size: "sm" });
  if (state.status === "guest") {
    return (
      <button
        type="button"
        className={cn(button, layout === "panel" && "flex-1")}
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void auth.signIn(here).finally(() => setBusy(false));
        }}
      >
        Sign in
      </button>
    );
  }
  return (
    <div className={cn("flex items-center gap-1", layout === "panel" && "w-full flex-wrap gap-2")}>
      {layout === "panel" && (
        <Link
          to="/my-searches"
          onClick={onNavigate}
          className={cn(pillButton({ variant: "outline", size: "sm" }), "flex-1")}
        >
          My searches
        </Link>
      )}
      <span
        className={cn(
          "max-w-40 truncate px-2 text-sm font-medium text-foreground",
          layout === "bar" && "hidden lg:inline",
        )}
        title={name}
      >
        {name}
      </span>
      <button
        type="button"
        className={cn(button, layout === "panel" && "flex-1")}
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void auth.signOut(here).finally(() => setBusy(false));
        }}
      >
        Sign out
        {layout === "bar" && <span className="sr-only lg:hidden"> ({name})</span>}
      </button>
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
