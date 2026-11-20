import { useEffect, useState } from "react";
import { getAccountService } from "@/lib/account/service";
import { AccountError } from "@/lib/account/types";
import { getAuthService } from "@/lib/auth/service";
import type { AuthState } from "@/lib/auth/types";

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
export function useAccountName(state: AuthState): string {
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
