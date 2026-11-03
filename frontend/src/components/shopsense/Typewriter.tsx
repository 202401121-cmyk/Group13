import { Fragment, useEffect, useRef, useState, type ElementType } from "react";
import { cn } from "@/lib/utils";

export type TypeSegment = string | { text: string; as?: ElementType; className?: string } | { br: true };

type Props = {
  as?: ElementType;
  segments: TypeSegment[];
  className?: string;
  /** ms per character */
  speed?: number;
  /** When provided, typing waits until this is true (in addition to being in view). */
  start?: boolean;
  onDone?: () => void;
};

const norm = (s: TypeSegment) => (typeof s === "string" ? { text: s } : s);

/**
 * Types text in letter by letter once it scrolls into view. Full text is laid out
 * from the start (untyped characters are visibility:hidden) so there is no layout shift.
 * Screen readers get the full text once; the animated copy is aria-hidden.
 */
export function Typewriter({ as: Tag = "p", segments, className, speed = 28, start = true, onDone }: Props) {
  const ref = useRef<HTMLElement>(null);
  const total = segments.reduce((n, s) => n + ("br" in norm(s) ? 0 : (norm(s) as { text: string }).text.length), 0);
  const [count, setCount] = useState(total); // SSR/no-JS: full text
  const [inView, setInView] = useState(false);
  const [phase, setPhase] = useState<"idle" | "typing" | "done">("idle");
  const posRef = useRef(0);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setPhase("done");
      doneRef.current?.();
      return;
    }
    setCount(0);
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e?.isIntersecting) {
          setInView(true);
          io.disconnect();
        }
      },
      { threshold: 0.4 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (phase === "done" || !inView || !start) return;
    setPhase("typing");
    let i = posRef.current;
    let raf = 0;
    let timer = 0;
    let last = performance.now();
    let acc = 0;
    const tick = (t: number) => {
      acc += t - last;
      last = t;
      while (acc >= speed && i < total) {
        acc -= speed;
        i++;
      }
      posRef.current = i;
      setCount(i);
      if (i < total) raf = requestAnimationFrame(tick);
      else
        timer = window.setTimeout(() => {
          setPhase("done");
          doneRef.current?.();
        }, 350);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
    };
    // phase intentionally excluded so setting "typing" doesn't restart the loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, start, speed, total]);

  const fullText = segments.map((s) => ("br" in norm(s) ? " " : (norm(s) as { text: string }).text)).join("");
  let remaining = count;
  let cursorPlaced = false;
  const showCursor = phase === "typing";

  return (
    <Tag ref={ref} className={className}>
      <span className="sr-only">{fullText}</span>
      <span aria-hidden>
        {segments.map((raw, idx) => {
          const s = norm(raw);
          if ("br" in s) return <br key={idx} />;
          const shown = Math.max(0, Math.min(s.text.length, remaining));
          remaining -= shown;
          const cursorHere = showCursor && !cursorPlaced && shown < s.text.length;
          if (cursorHere) cursorPlaced = true;
          const Inner = s.as ?? Fragment;
          const innerProps = s.as ? { className: s.className } : {};
          return (
            <Inner key={idx} {...innerProps}>
              {s.text.slice(0, shown)}
              {cursorHere && <span className="type-cursor" />}
              <span className="invisible">{s.text.slice(shown)}</span>
            </Inner>
          );
        })}
        {showCursor && !cursorPlaced && <span className="type-cursor" />}
      </span>
    </Tag>
  );
}

/** Classes kept for consumers that need to merge. */
export const typewriterCn = cn;
