import { useState, type ReactNode } from "react";
import { Navbar } from "./Navbar";
import { Footer } from "./Footer";

export function PageShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="min-h-screen">
      <Navbar open={menuOpen} onOpenChange={setMenuOpen} />
      {/* While the small-screen menu is open, nothing behind it can take focus or be pressed. */}
      <main inert={menuOpen}>{children}</main>
      <div inert={menuOpen}>
        <Footer />
      </div>
    </div>
  );
}

export function ComingSoon({ title, text }: { title: string; text: string }) {
  return (
    <PageShell>
      <section className="mx-auto max-w-3xl px-6 py-32">
        <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">
          Coming soon
        </p>
        <h1 className="mt-3 font-serif text-5xl text-foreground">{title}</h1>
        <p className="mt-4 text-lg text-muted-foreground">{text}</p>
      </section>
    </PageShell>
  );
}
