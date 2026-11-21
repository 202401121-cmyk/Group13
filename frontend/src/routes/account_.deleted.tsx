import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { CircleCheck } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { pillButton } from "@/components/shopsense/pill-button";
import { DELETED_KEY } from "@/lib/account/memory";
import { useAuth } from "@/lib/auth/service";
import { cn } from "@/lib/utils";

/**
 * The plain page shown after an account is deleted (US-11): the delete dialog notes the delete in this tab, signs the
 * shopper out and loads this page afresh. It says "deleted", and what went, only then. Someone still signed in is
 * told their account is still here, and anyone else that there is nothing to confirm, so the page never says
 * something untrue. Until the browser has read both, it shows nothing.
 */
export const Route = createFileRoute("/account_/deleted")({
  head: () => ({
    meta: [
      { title: "Your account is deleted — ShopSense" },
      { name: "robots", content: "noindex" },
    ],
  }),
  component: AccountDeleted,
});

function deletedHere(): boolean {
  try {
    return sessionStorage.getItem(DELETED_KEY) !== null;
  } catch {
    return false;
  }
}

const title = "font-serif text-5xl text-foreground";
const lead = "mt-4 text-lg leading-relaxed text-muted-foreground";

function AccountDeleted() {
  const state = useAuth();
  const [deleted, setDeleted] = useState<boolean | null>(null);
  useEffect(() => setDeleted(deletedHere()), []);
  const known = deleted !== null && state.status !== "loading";
  return (
    <PageShell>
      <section
        aria-labelledby="deleted-title"
        aria-busy={!known}
        className="mx-auto max-w-2xl px-6 pb-16 pt-20 md:pt-24"
      >
        {!known ? (
          <h1 id="deleted-title" className="sr-only">
            Your account
          </h1>
        ) : state.status === "signedIn" ? (
          <>
            <h1 id="deleted-title" className={title}>
              Your account is still here
            </h1>
            <p className={lead}>
              This page shows after an account is deleted. You're signed in, so yours hasn't been.
            </p>
            <Link to="/account" className={cn(pillButton({ size: "md" }), "mt-8")}>
              Go to your profile
            </Link>
          </>
        ) : deleted ? (
          <>
            <CircleCheck className="h-10 w-10 text-primary" aria-hidden />
            <h1 id="deleted-title" className={cn(title, "mt-4")}>
              Your account is deleted
            </h1>
            <p className={lead}>
              We deleted every request and search saved with your account, everything ShopSense
              remembered about you, your profile and your sign-in. You're signed out.
            </p>
            <p className="mt-3 leading-relaxed text-muted-foreground">
              You can still search as a guest. A guest's search is never linked to anyone.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/research" className={pillButton({ size: "md" })}>
                Start a search
              </Link>
              <Link to="/" className={pillButton({ size: "md", variant: "outline" })}>
                Go home
              </Link>
            </div>
          </>
        ) : (
          <>
            <h1 id="deleted-title" className={title}>
              Nothing to confirm here
            </h1>
            <p className={lead}>
              This page confirms that an account was deleted. No account was deleted in this browser
              tab, and nobody is signed in.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/research" className={pillButton({ size: "md" })}>
                Start a search
              </Link>
              <Link to="/" className={pillButton({ size: "md", variant: "outline" })}>
                Go home
              </Link>
            </div>
          </>
        )}
      </section>
    </PageShell>
  );
}
