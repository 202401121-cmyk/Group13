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

/**
 * "Stop research?" when the shopper leaves the results page while research runs. It is loaded only when
 * needed (the results page imports it lazily), so its dialog library stays out of the page's first load.
 * Escape and "Keep researching" both keep the research going, and focus goes back to where it was.
 */
export default function StopResearchDialog({
  onKeep,
  onStop,
}: {
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
          if (returnTo?.isConnected) returnTo.focus();
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>Stop research?</AlertDialogTitle>
          <AlertDialogDescription>
            Leaving this page stops the research. Your brief and answers are kept.
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
