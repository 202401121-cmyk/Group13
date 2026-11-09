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
import { giveFocusBack } from "@/lib/results/view";

/**
 * "Stop research?" when the shopper presses "Stop research", or leaves the results page while research runs.
 * It is loaded only when needed (the results page imports it lazily), so its dialog library stays out of the
 * page's first load. Escape and "Keep researching" both keep the research going, and focus goes back to the
 * control that opened the dialog, or to the page's main heading if that control is gone.
 */
export default function StopResearchDialog({
  leaving,
  onKeep,
  onStop,
}: {
  /** The shopper is leaving the page, not pressing the page's own "Stop research" button. */
  leaving: boolean;
  onKeep: () => void;
  onStop: () => void;
}) {
  // The control that was focused when the dialog opened (usually the link the shopper used to leave).
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
          document.getElementById("keep-researching")?.focus();
        }}
        // There is no trigger button, so by default focus would drop to the page body on close.
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          giveFocusBack(returnTo);
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>Stop research?</AlertDialogTitle>
          <AlertDialogDescription>
            {leaving ? "Leaving this page stops the research." : "This ends the search."} Your brief
            and answers are kept.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          {/* Closing the dialog (this button or Escape) calls onKeep through onOpenChange. */}
          <AlertDialogCancel id="keep-researching">Keep researching</AlertDialogCancel>
          <AlertDialogAction onClick={onStop}>Stop research</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
