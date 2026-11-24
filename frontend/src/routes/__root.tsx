import {
  Outlet,
  Link,
  createRootRoute,
  useRouter,
  useRouterState,
  HeadContent,
  Scripts,
  type ErrorComponentProps,
} from "@tanstack/react-router";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { SHELL_FONTS } from "@/lib/fonts";
import { LostPage } from "@/components/shopsense/LostPage";

const primary =
  "shopsense-button primary-button sheen inline-flex min-h-11 items-center justify-center gap-2 rounded-full border border-transparent bg-primary px-6 text-sm font-medium text-primary-foreground shadow-soft";
const secondary =
  "shopsense-button inline-flex min-h-11 items-center justify-center rounded-full border border-border bg-card px-6 text-sm font-medium text-foreground hover:border-primary/50";

function NotFoundComponent() {
  return (
    <LostPage
      title="We can't find that page"
      text="It may have moved, or the address may be wrong."
      actions={
        <>
          <Link to="/" className={primary}>
            Go home
          </Link>
          <Link to="/research" className={secondary}>
            Start a search
          </Link>
        </>
      }
    />
  );
}

function ErrorComponent({ error, reset }: ErrorComponentProps) {
  console.error(error);
  const router = useRouter();

  return (
    <LostPage
      title="This page didn't load"
      text="Something went wrong on our end. You can try again, or head back home."
      actions={
        <>
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className={primary}
          >
            Try again
          </button>
          <a href="/" className={secondary}>
            Go home
          </a>
        </>
      }
    />
  );
}

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "ShopSense" },
      { name: "description", content: "AI-powered personalized shopping research." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
      ...SHELL_FONTS,
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

const THEME_SCRIPT = `try{var t=localStorage.getItem("shopsense.theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <HeadContent />
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

const Toaster = lazy(() =>
  import("@/components/ui/sonner").then(({ Toaster }) => ({ default: Toaster })),
);

/**
 * Only a search's results page raises toasts (stopping a search), so the library is fetched there, never with the
 * first load of another page (S5-12: the brief page's budget, D-45). Once fetched it stays mounted, because a toast has
 * to outlive the page that raised it.
 */
function Toasts() {
  const onResults = useRouterState({
    select: (s) => /^\/s\/[^/]+\/results\/?$/.test(s.location.pathname),
  });
  const [needed, setNeeded] = useState(false);
  useEffect(() => {
    if (onResults) setNeeded(true);
  }, [onResults]);

  if (!needed) return null;
  return (
    <Suspense fallback={null}>
      <Toaster position="bottom-center" />
    </Suspense>
  );
}

function RootComponent() {
  return (
    <>
      {/* Required: nested routes render here. Removing <Outlet /> breaks all child routes. */}
      <Outlet />
      <Toasts />
    </>
  );
}
