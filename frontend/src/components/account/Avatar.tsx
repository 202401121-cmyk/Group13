import { cn } from "@/lib/utils";
import { initialsOf, type AuthUser } from "@/lib/auth/types";

/**
 * The shopper's initials on a soft sage-to-clay disc. No photo: a Google picture would load from Google's servers,
 * and no page loads anything from another address (F-16).
 */
export function Avatar({
  user,
  size = "md",
  className,
}: {
  user: Pick<AuthUser, "name" | "email">;
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
}) {
  const box = {
    sm: "h-8 w-8 text-xs",
    md: "h-9 w-9 text-sm",
    lg: "h-14 w-14 text-lg",
    xl: "h-24 w-24 text-3xl",
  }[size];
  return (
    <span
      aria-hidden
      className={cn(
        "relative inline-grid shrink-0 place-items-center rounded-full font-serif font-medium text-ink-foreground",
        "avatar-disc",
        box,
        className,
      )}
    >
      {/* Drawn with CSS, not written: the initials are never part of a control's name or visible text. */}
      <span data-initials={initialsOf(user)} className="before:content-[attr(data-initials)]" />
    </span>
  );
}
