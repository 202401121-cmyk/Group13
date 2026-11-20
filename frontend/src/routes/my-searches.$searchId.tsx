import { useCallback, useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { PageShell } from "@/components/shopsense/PageShell";
import { PillButton } from "@/components/shopsense/ui";
import { pillButton } from "@/components/shopsense/pill-button";
import { Notice } from "@/components/results/primitives";
import { ResultsView } from "@/components/results/ResultsView";
import { SignInPrompt } from "@/components/account/SignInPrompt";
import { savedAt } from "@/lib/account/format";
import { getAccountService } from "@/lib/account/service";
import { AccountError, ACCOUNT_MESSAGES, type SavedSearch } from "@/lib/account/types";
import { getAuthService, useAuth } from "@/lib/auth/service";
import { inSentence, keyInWords } from "@/lib/text";

export const Route = createFileRoute("/my-searches/$searchId")({
  head: () => ({
    meta: [
      { title: "Saved search — ShopSense" },
      { name: "description", content: "One of your saved searches, with its results." },
    ],
  }),
  component: SavedSearchRoute,
});

function SavedSearchRoute() {
  const { searchId } = Route.useParams();
  const state = useAuth();
  return (
    <PageShell>
      <div className="mx-auto max-w-4xl px-4 py-20 sm:px-6">
        <Link
          to="/my-searches"
          className={`${pillButton({ variant: "ghost", size: "sm" })} -ml-4 min-h-11`}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
          My searches
        </Link>
        {state.status === "signedIn" ? (
          <Saved key={`${state.user.id}:${searchId}`} searchId={searchId} />
        ) : (
          <section className="mt-6" aria-labelledby="saved-title">
            <h1 id="saved-title" className="font-serif text-5xl text-foreground">
              Saved search
            </h1>
            <SignInPrompt state={state} returnTo={`/my-searches/${encodeURIComponent(searchId)}`} />
          </section>
        )}
      </div>
    </PageShell>
  );
}

type Load =
  | { phase: "loading" }
  | { phase: "notFound" }
  | { phase: "error"; message: string }
  | { phase: "ready"; saved: SavedSearch };

function Saved({ searchId }: { searchId: string }) {
  const [load, setLoad] = useState<Load>({ phase: "loading" });

  const open = useCallback(() => {
    setLoad({ phase: "loading" });
    getAccountService()
      .search(searchId)
      .then(
        (saved) => setLoad({ phase: "ready", saved }),
        (e: unknown) => {
          if (e instanceof AccountError && e.kind === "notFound")
            return setLoad({ phase: "notFound" });
          if (e instanceof AccountError && e.kind === "signedOut") {
            void getAuthService().dropSession(); // the page then asks the shopper to sign in again
            return;
          }
          setLoad({
            phase: "error",
            message: e instanceof AccountError ? e.message : ACCOUNT_MESSAGES.unknown,
          });
        },
      );
  }, [searchId]);

  useEffect(open, [open]);

  if (load.phase === "loading") {
    return (
      <p role="status" className="mt-6 text-muted-foreground">
        Opening your saved search…
      </p>
    );
  }
  if (load.phase === "notFound") {
    // Someone else's search, or one that doesn't exist, look the same: the backend never confirms an id (S4-10).
    return (
      <section className="mt-6" aria-labelledby="saved-title">
        <h1 id="saved-title" className="font-serif text-4xl text-foreground md:text-5xl">
          {ACCOUNT_MESSAGES.notFound}
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">
          It isn't one of the searches saved to your account. Your own searches are listed in My
          searches.
        </p>
        <Link to="/my-searches" className={`${pillButton()} mt-8 min-h-11`}>
          Go to My searches
        </Link>
      </section>
    );
  }
  if (load.phase === "error") {
    return (
      <section className="mt-6" aria-labelledby="saved-title">
        <h1 id="saved-title" className="font-serif text-5xl text-foreground">
          Saved search
        </h1>
        <div className="mt-6">
          <Notice
            tone="error"
            action={
              <PillButton type="button" size="sm" onClick={open}>
                Try again
              </PillButton>
            }
          >
            {load.message}
          </Notice>
        </div>
      </section>
    );
  }

  const { saved } = load;
  return (
    <div className="mt-6">
      <ResultsView
        result={saved.result}
        heading={`Results for your ${inSentence(keyInWords(saved.category))}`}
        sessionId={null}
        before={
          <p className="mt-3 break-words text-muted-foreground">
            Saved {savedAt(saved.finishedAt)}. You asked: “{saved.request}”
          </p>
        }
      />
    </div>
  );
}
