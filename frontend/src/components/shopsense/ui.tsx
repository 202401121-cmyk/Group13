import { Link } from "@tanstack/react-router";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

export const pillButton = cva(
  "shopsense-button inline-flex items-center justify-center gap-2 rounded-full font-medium transition-all duration-200 disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary:
          "primary-button bg-primary text-primary-foreground shadow-soft hover:brightness-105 active:brightness-95",
        ghost: "text-foreground hover:bg-secondary",
        outline: "border border-border bg-card text-foreground hover:border-primary/50",
        link: "text-foreground hover:text-primary",
      },
      size: { sm: "h-9 px-4 text-sm", md: "h-11 px-6 text-sm", lg: "h-13 px-7 text-base" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type PillProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof pillButton>;
export function PillButton({ className, variant, size, ...props }: PillProps) {
  return <button className={cn(pillButton({ variant, size }), className)} {...props} />;
}

export function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3.5 py-1.5 font-mono text-[11px] font-medium tracking-[0.14em] text-foreground">
      <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden />
      {children}
    </span>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: string;
}) {
  return (
    <div className="max-w-2xl">
      {eyebrow && (
        <p className="mb-3 font-mono text-xs font-medium tracking-[0.14em] text-muted-foreground">{eyebrow}</p>
      )}
      <h2 className="font-serif text-4xl leading-tight text-foreground md:text-5xl">{title}</h2>
      {description && (
        <p className="mt-4 text-base leading-relaxed text-muted-foreground">{description}</p>
      )}
    </div>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("interactive-card rounded-2xl border border-border bg-card p-7 shadow-soft", className)}>
      {children}
    </div>
  );
}

export function BrandMark({ to = "/" as const }: { to?: "/" }) {
  return (
    <Link to={to} className="flex items-center gap-2.5" aria-label="ShopSense home">
      <span className="grid h-8 w-8 place-items-center rounded-full bg-primary text-primary-foreground">
        <svg
          viewBox="0 0 24 24"
          className="h-4 w-4"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden
        >
          <path d="M5 8h14l-1.2 11H6.2L5 8Z" />
          <path d="M9 8V6a3 3 0 0 1 6 0v2" />
          <circle cx="12" cy="14" r="2" />
        </svg>
      </span>
      <span className="font-serif text-xl text-foreground">ShopSense</span>
    </Link>
  );
}
