import { useEffect, useRef } from "react";

/** One-shot reveals and viewport-paused decorative motion, scoped to a container. */
export function useVisualMotion<T extends HTMLElement>() {
  const ref = useRef<T>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const elements = [
      ...(root.matches("[data-reveal], [data-motion-loop]") ? [root] : []),
      ...root.querySelectorAll<HTMLElement>("[data-reveal], [data-motion-loop]"),
    ];
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    let observer: IntersectionObserver | undefined;

    function configure() {
      observer?.disconnect();
      if (preference.matches || !("IntersectionObserver" in window)) {
        elements.forEach((element) => {
          element.classList.remove("reveal-pending", "motion-visible");
          element.classList.add("reveal-complete");
        });
        return;
      }
      observer = new IntersectionObserver((entries) => {
        entries.forEach(({ target, isIntersecting }) => {
          if (target.hasAttribute("data-motion-loop")) {
            target.classList.toggle("motion-visible", isIntersecting);
          }
          if (isIntersecting && target.hasAttribute("data-reveal")) {
            target.classList.remove("reveal-pending");
            target.classList.add("reveal-complete");
            if (!target.hasAttribute("data-motion-loop")) observer?.unobserve(target);
          }
        });
      }, { threshold: 0.12 });
      elements.forEach((element) => {
        if (element.hasAttribute("data-reveal") && !element.classList.contains("reveal-complete")) {
          element.classList.add("reveal-pending");
        }
        observer?.observe(element);
      });
    }

    configure();
    preference.addEventListener("change", configure);
    return () => {
      observer?.disconnect();
      preference.removeEventListener("change", configure);
    };
  }, []);

  return ref;
}