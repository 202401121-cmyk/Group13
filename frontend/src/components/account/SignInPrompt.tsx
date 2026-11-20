import { Link } from "@tanstack/react-router";
import { ArrowRight, Lock } from "lucide-react";
import { pillButton } from "@/components/shopsense/pill-button";
import { returnPath, type AuthState } from "@/lib/auth/types";
import { cn } from "@/lib/utils";

/** What a page about the shopper's own searches shows to someone who isn't signed in. */
export function SignInPrompt({ state, returnTo }: { state: AuthState; returnTo: string }) {
  if (state.status === "loading") {
    return (
      <div role="status" className="mt-8 space-y-3" aria-label="Checking whether you're signed in">
        <span className="sr-only">Checking whether you're signed in…</span>
        <div className="skeleton h-5 w-2/3" />
        <div className="skeleton h-5 w-1/2" />
      </div>
    );
  }
  if (state.status === "unavailable") {
    return (
      <p className="mt-6 text-lg text-muted-foreground">
        Sign-in isn't available on this site, so there are no saved searches to show. You can still
        search as a guest.
      </p>
    );
  }
  return (
    <div className="surface-raised animate-rise mt-8 overflow-hidden rounded-3xl">
      <div className="flex flex-col gap-6 p-7 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-primary-soft text-primary-soft-foreground">
            <Lock className="h-5 w-5" aria-hidden />
          </span>
          <div>
            <p className="font-medium text-foreground">Sign in to see your saved searches</p>
            <p className="mt-1 text-muted-foreground">
              Searches you finish while signed in are kept here. Ones you make as a guest aren't.
            </p>
          </div>
        </div>
        <Link
          to="/sign-in"
          search={{ next: returnPath(returnTo) }}
          className={cn(pillButton(), "sheen shrink-0")}
        >
          Sign in <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
      </div>
    </div>
  );
}
