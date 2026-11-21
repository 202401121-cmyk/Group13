import { useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button-variants";
import { giveFocusBack } from "@/lib/results/view";

/**
 * "Forget everything?", before every remembered item is deleted (US-11). Loaded only when needed, so its dialog
 * library stays out of the account page's first load. "Keep them" has focus first; Escape keeps them too. Focus goes
 * back to "Forget everything", or to the page's heading if that is gone.
 */
export default function ForgetAllDialog({
  count,
  onKeep,
  onForget,
}: {
  count: number;
  onKeep: () => void;
  onForget: () => void;
}) {
  const [returnTo] = useState(() => document.activeElement as HTMLElement | null);
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open) onKeep();
      }}
    >
      <AlertDialogContent
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          document.getElementById("keep-memory")?.focus();
        }}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          giveFocusBack(returnTo);
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>Forget everything?</AlertDialogTitle>
          <AlertDialogDescription>
            This deletes {count === 1 ? "the 1 thing" : `all ${count} things`} ShopSense remembers
            about you. Your saved searches and your account stay. This can't be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel id="keep-memory">Keep them</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "destructive" })}
            onClick={onForget}
          >
            Forget everything
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
