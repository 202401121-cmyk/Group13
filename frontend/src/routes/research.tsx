import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useReducer, useRef, useState } from "react";
import { PageShell } from "@/components/shopsense/PageShell";
import { RequestForm } from "@/components/request/RequestForm";
import { BriefPanel } from "@/components/request/BriefPanel";
import {
  Clarification,
  ErrorState,
  ExamplesState,
  Submitting,
} from "@/components/request/StatusScreens";
import { Understanding } from "@/components/request/Understanding";
import { initialState, requestReducer, toSnapshot, type RequestState } from "@/lib/request/reducer";
import { getRequestService } from "@/lib/request/service";
import { createSession } from "@/lib/session/store";

const STORAGE_KEY = "shopsense.request.v1";
const CONFIRMED_KEY = "shopsense.brief.confirmed.v1";

export const Route = createFileRoute("/research")({
  head: () => ({
    meta: [
      { title: "What are you shopping for? — ShopSense" },
      {
        name: "description",
        content:
          "Describe what you need in your own words and ShopSense turns it into a clear shopping brief.",
      },
      { property: "og:title", content: "What are you shopping for? — ShopSense" },
      {
        property: "og:description",
        content: "Describe what you need and get a clear, editable shopping brief.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ResearchPage,
});

const ANNOUNCE: Partial<Record<RequestState["status"], string>> = {
  submitting: "Reading your request.",
  "follow-up": "We need one more detail.",
  examples: "We couldn't tell what kind of product you mean.",
  understood: "Here's what we understood.",
  error: "Something went wrong. You can retry.",
};

function ResearchPage() {
  const [state, dispatch] = useReducer(requestReducer, initialState);
  const [hydrated, setHydrated] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();
  const service = getRequestService();

  // Restore from session storage after hydration.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) dispatch({ type: "RESTORE", state: JSON.parse(raw) });
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(toSnapshot(state)));
    } catch {
      /* storage unavailable: ignore */
    }
  }, [state, hydrated]);

  // Run the pending request whenever a new submission starts.
  useEffect(() => {
    if (state.status !== "submitting" || !state.pending) return;
    let cancelled = false;
    const p = state.pending;
    service
      .understand(
        p.kind === "clarify" ? { text: p.text, clarification: p.answer } : { text: p.text },
      )
      .then((response) => !cancelled && dispatch({ type: "RESOLVED", response }))
      .catch(
        (e: unknown) =>
          !cancelled &&
          dispatch({
            type: "FAILED",
            message: e instanceof Error ? e.message : "Please try again.",
          }),
      );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.requestId]);

  function pickExample(text: string) {
    dispatch({ type: "USE_EXAMPLE", text });
    requestAnimationFrame(() => {
      const el = textRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(text.length, text.length);
      }
    });
  }

  async function saveBrief(brief: NonNullable<RequestState["brief"]>) {
    dispatch({ type: "SAVE_START" });
    try {
      dispatch({ type: "SAVE_DONE", brief: await service.saveBrief(brief) });
    } catch {
      dispatch({ type: "SAVE_FAILED", message: "We couldn't save that. Try again." });
    }
  }

  function confirm() {
    if (!state.brief) return;
    sessionStorage.setItem(CONFIRMED_KEY, JSON.stringify(state.brief));
    const sessionId = createSession(state.brief);
    navigate({ to: "/s/$sessionId/criteria", params: { sessionId } });
  }

  function startOver() {
    dispatch({ type: "RESET" });
    sessionStorage.removeItem(STORAGE_KEY);
    requestAnimationFrame(() => textRef.current?.focus());
  }

  const s = state.status;
  const understoodView = s === "understood" || s === "editing" || s === "saving";

  return (
    <PageShell>
      <div className="relative">
        <div className="relative mx-auto grid max-w-6xl gap-10 px-5 pb-10 pt-14 sm:px-6 md:pt-20 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0">
            {understoodView && state.brief ? (
              <Understanding
                brief={state.brief}
                editingField={state.editingField}
                saving={s === "saving"}
                saveError={state.error}
                edited={state.edited}
                onEdit={(field) => dispatch({ type: "EDIT_FIELD", field })}
                onCancel={() => dispatch({ type: "CANCEL_EDIT" })}
                onSave={saveBrief}
                onConfirm={confirm}
                onStartOver={startOver}
              />
            ) : (
              <>
                <h1 className="font-serif text-4xl leading-tight text-foreground md:text-6xl">
                  What are you shopping for?
                </h1>
                <p className="mt-3 max-w-xl text-muted-foreground md:text-lg">
                  Describe it in your own words: what it's for, your budget, anything you don't
                  want.
                </p>
                <div className="mt-8">
                  {s === "idle" && (
                    <RequestForm
                      ref={textRef}
                      text={state.text}
                      error={state.inputError}
                      onChange={(text) => dispatch({ type: "SET_TEXT", text })}
                      onSubmit={() => dispatch({ type: "SUBMIT" })}
                      onPickExample={pickExample}
                    />
                  )}
                  {s === "submitting" && <Submitting text={state.submitted ?? state.text} />}
                  {s === "error" && (
                    <ErrorState
                      text={state.submitted ?? state.text}
                      message={state.error ?? "Please try again."}
                      onRetry={() => dispatch({ type: "RETRY" })}
                      onEdit={() => dispatch({ type: "BACK_TO_REQUEST" })}
                    />
                  )}
                  {s === "follow-up" && state.followUp && (
                    <Clarification
                      text={state.submitted ?? state.text}
                      followUp={state.followUp}
                      error={state.inputError}
                      onAnswer={(answer) => dispatch({ type: "CLARIFY", answer })}
                      onEdit={() => dispatch({ type: "BACK_TO_REQUEST" })}
                    />
                  )}
                  {s === "examples" && (
                    <>
                      <ExamplesState onPick={pickExample} />
                      <button
                        type="button"
                        onClick={() => {
                          dispatch({ type: "BACK_TO_REQUEST" });
                          requestAnimationFrame(() => textRef.current?.focus());
                        }}
                        className="mt-4 text-sm font-medium text-primary underline-offset-4 hover:underline"
                      >
                        Describe it in my own words
                      </button>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
          <BriefPanel brief={state.brief} />
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {ANNOUNCE[s] ?? ""}
      </p>
    </PageShell>
  );
}
