import { useState, type ReactNode } from "react";
import { Navbar } from "./Navbar";
import { Footer } from "./Footer";

export function PageShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="relative min-h-screen">
      {/* A faint drafting grid behind the top of every page, so they share one surface (US-30 redesign). */}
      <div
        className="bg-blueprint pointer-events-none absolute inset-x-0 top-0 h-[34rem] opacity-60"
        aria-hidden
      />
      <Navbar open={menuOpen} onOpenChange={setMenuOpen} />
      {/* While the small-screen menu is open, nothing behind it can take focus or be pressed. */}
      <main inert={menuOpen} className="relative">
        {children}
      </main>
      <div inert={menuOpen}>
        <Footer />
      </div>
    </div>
  );
}
