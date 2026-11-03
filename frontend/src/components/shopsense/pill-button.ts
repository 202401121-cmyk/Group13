import { cva } from "class-variance-authority";

/** Classes for ShopSense's rounded buttons, also used on links that look like buttons. */
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
      // Every size is at least 44 px tall: the smallest touch target ShopSense allows.
      size: { sm: "h-11 px-4 text-sm", md: "h-11 px-6 text-sm", lg: "h-13 px-7 text-base" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);
