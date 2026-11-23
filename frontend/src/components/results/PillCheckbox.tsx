import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A checkbox whose whole pill is the target, at least 44 px (D-45): the input fills the label unseen, and a drawn box
 * shows its state. The input itself draws the focus outline round the pill, so it shows in forced colours too. The
 * label's words name it, or `label` when they are too short to say which one it is.
 */
export function PillCheckbox({
  checked,
  onChange,
  label,
  className,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string | undefined;
  className?: string | undefined;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "group relative inline-flex min-h-11 min-w-11 cursor-pointer items-center gap-2 rounded-full px-3 text-sm font-medium text-foreground",
        className,
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
        className="absolute -inset-px cursor-pointer appearance-none rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      />
      <span
        aria-hidden
        className="grid h-5 w-5 shrink-0 place-items-center rounded border-2 border-muted-foreground group-has-[:checked]:border-primary group-has-[:checked]:bg-primary"
      >
        <span className="mb-0.5 h-2.5 w-1.5 rotate-45 border-r-2 border-b-2 border-primary-foreground opacity-0 group-has-[:checked]:opacity-100" />
      </span>
      {children}
    </label>
  );
}
