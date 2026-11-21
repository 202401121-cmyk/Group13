import { useRef, useState, type FormEvent } from "react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button-variants";
import { DELETED_KEY, isConfirmWord, MEMORY_MESSAGES, MemoryError } from "@/lib/account/memory";
import { getMemoryService } from "@/lib/account/memoryService";
import { getAuthService } from "@/lib/auth/service";
import { giveFocusBack } from "@/lib/results/view";

/** The plain page shown once the account is deleted and the shopper signed out (src/routes/account_.deleted.tsx). */
export const DELETED_PATH = "/account/deleted";
export const TYPE_DELETE = 'Type the word "delete" to confirm.';

/**
 * "Delete your account?" (US-11). It lists exactly what goes, and asks for the word "delete": anything else is refused
 * here, and never sent. Focus starts in the box; Escape and "Keep my account" close it and give focus back to the
 * button that opened it. While the delete runs it can't be closed. Once the backend has confirmed nothing is left, the
 * shopper is signed out and the confirmation page loads afresh. Loaded only when needed, so its dialog library stays
 * out of the account page's first load.
 */
export default function DeleteAccountDialog({ onClose }: { onClose: () => void }) {
  const [returnTo] = useState(() => document.activeElement as HTMLElement | null);
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLInputElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!isConfirmWord(typed)) {
      setProblem(TYPE_DELETE);
      box.current?.focus();
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await getMemoryService().deleteAccount(typed);
    } catch (err) {
      setBusy(false);
      if (err instanceof MemoryError && err.kind === "signedOut") {
        onClose();
        void getAuthService().dropSession(); // the page then asks the shopper to sign in again
        return;
      }
      setProblem(err instanceof MemoryError ? err.message : MEMORY_MESSAGES.unknown);
      return;
    }
    // The account is gone: sign out here too (its token stays valid until it expires), and show the plain page,
    // which says "deleted" only after this.
    try {
      sessionStorage.setItem(DELETED_KEY, "1");
    } catch {
      /* storage blocked: the page then says only that nobody is signed in */
    }
    await getAuthService().signOut(DELETED_PATH);
  }

  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <AlertDialogContent
        aria-busy={busy}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          box.current?.focus();
        }}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          giveFocusBack(returnTo);
        }}
        onEscapeKeyDown={(e) => {
          if (busy) e.preventDefault();
        }}
        className="max-h-[100dvh] overflow-y-auto"
      >
        <form noValidate onSubmit={(e) => void submit(e)} className="grid gap-4">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-serif text-2xl font-normal">
              Delete your account?
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm leading-relaxed text-foreground">
                <p>This deletes, for good:</p>
                <ul className="list-disc space-y-1 pl-5 text-left">
                  <li>every request and search saved with your account</li>
                  <li>everything ShopSense remembers about you</li>
                  <li>your profile</li>
                  <li>your sign-in to ShopSense (your Google account itself isn't touched)</li>
                </ul>
                <p>This can't be undone.</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-1.5">
            <label htmlFor="confirm-delete" className="text-sm font-medium text-foreground">
              Type <span className="font-mono">delete</span> to confirm
            </label>
            <input
              id="confirm-delete"
              ref={box}
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              maxLength={40}
              readOnly={busy}
              aria-invalid={problem === TYPE_DELETE ? true : undefined}
              aria-describedby={problem ? "confirm-delete-problem" : undefined}
              className="min-h-11 rounded-2xl border border-input bg-background px-3 py-2 text-base text-foreground focus-visible:outline-2"
            />
            {problem && (
              <p id="confirm-delete-problem" role="alert" className="text-sm text-foreground">
                {problem}
              </p>
            )}
            {busy && (
              <p role="status" className="text-sm text-muted-foreground">
                Deleting your account…
              </p>
            )}
          </div>
          <AlertDialogFooter className="gap-2">
            <AlertDialogCancel disabled={busy}>Keep my account</AlertDialogCancel>
            <button
              type="submit"
              aria-disabled={busy}
              className={buttonVariants({ variant: "destructive" })}
            >
              {busy ? "Deleting…" : "Delete my account"}
            </button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}
