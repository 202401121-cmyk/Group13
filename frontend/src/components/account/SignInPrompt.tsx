import { useState } from "react";
import { PillButton } from "@/components/shopsense/ui";
import { getAuthService } from "@/lib/auth/service";
import type { AuthState } from "@/lib/auth/types";

/** What a page about the shopper's own searches shows to someone who isn't signed in. */
export function SignInPrompt({ state, returnTo }: { state: AuthState; returnTo: string }) {
  const [busy, setBusy] = useState(false);
  if (state.status === "loading") {
    return (
      <p role="status" className="mt-6 text-muted-foreground">
        Checking whether you're signed in…
      </p>
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
    <div className="mt-6">
      <p className="text-lg text-muted-foreground">
        Sign in with Google to see the searches you've finished while signed in. Searches you make
        as a guest aren't kept here.
      </p>
      <PillButton
        type="button"
        className="mt-6"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void getAuthService()
            .signIn(returnTo)
            .finally(() => setBusy(false));
        }}
      >
        Sign in with Google
      </PillButton>
    </div>
  );
}
