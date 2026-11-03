import type { ReactNode } from "react";
import { Navbar } from "./Navbar";
import { Footer } from "./Footer";

export function PageShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <Navbar />
      <main>{children}</main>
      <Footer />
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
