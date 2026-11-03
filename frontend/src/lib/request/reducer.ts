import { MAX_REQUEST_LENGTH, type Brief, type FieldKey, type FollowUp, type UnderstandResponse } from "./types";

export type Status = "idle" | "submitting" | "follow-up" | "examples" | "understood" | "editing" | "saving" | "error";

export type Pending = { kind: "submit"; text: string } | { kind: "clarify"; text: string; answer: string };

export interface RequestState {
  status: Status;
  text: string;
  submitted: string | null;
  followUp: FollowUp | null;
  brief: Brief | null;
  edited: boolean;
  editingField: FieldKey | null;
  inputError: string | null;
  error: string | null;
  pending: Pending | null;
  requestId: number;
}

export type Action =
  | { type: "SET_TEXT"; text: string }
  | { type: "SUBMIT" }
  | { type: "CLARIFY"; answer: string }
  | { type: "RESOLVED"; response: UnderstandResponse }
  | { type: "FAILED"; message: string }
  | { type: "RETRY" }
  | { type: "BACK_TO_REQUEST" }
  | { type: "USE_EXAMPLE"; text: string }
  | { type: "EDIT_FIELD"; field: FieldKey }
  | { type: "CANCEL_EDIT" }
  | { type: "SAVE_START" }
  | { type: "SAVE_DONE"; brief: Brief }
  | { type: "SAVE_FAILED"; message: string }
  | { type: "RESET" }
  | { type: "RESTORE"; state: Partial<RequestState> };

export const initialState: RequestState = {
  status: "idle",
  text: "",
  submitted: null,
  followUp: null,
  brief: null,
  edited: false,
  editingField: null,
  inputError: null,
  error: null,
  pending: null,
  requestId: 0,
};

export const EMPTY_MESSAGE = "Type what you're looking for first.";
export const TOO_LONG_MESSAGE = `Keep it to ${MAX_REQUEST_LENGTH.toLocaleString("en-IN")} characters or fewer.`;

export function requestReducer(state: RequestState, action: Action): RequestState {
  switch (action.type) {
    case "SET_TEXT":
      return { ...state, text: action.text, inputError: null };
    case "SUBMIT": {
      if (state.status === "submitting" || state.status === "saving") return state;
      const text = state.text.trim();
      if (!text) return { ...state, inputError: EMPTY_MESSAGE };
      if (text.length > MAX_REQUEST_LENGTH) return { ...state, inputError: TOO_LONG_MESSAGE };
      return {
        ...state,
        status: "submitting",
        text,
        submitted: text,
        inputError: null,
        error: null,
        pending: { kind: "submit", text },
        requestId: state.requestId + 1,
      };
    }
    case "CLARIFY": {
      if (state.status === "submitting") return state;
      const answer = action.answer.trim();
      if (!answer) return { ...state, inputError: "Type the kind of product first." };
      const text = state.submitted ?? state.text.trim();
      return {
        ...state,
        status: "submitting",
        inputError: null,
        error: null,
        pending: { kind: "clarify", text, answer },
        requestId: state.requestId + 1,
      };
    }
    case "RESOLVED": {
      if (state.status !== "submitting") return state;
      const r = action.response;
      if (r.kind === "follow-up") return { ...state, status: "follow-up", followUp: r.followUp };
      if (r.kind === "examples") return { ...state, status: "examples", followUp: null };
      return { ...state, status: "understood", brief: r.brief, followUp: null, edited: false, pending: null };
    }
    case "FAILED":
      if (state.status !== "submitting") return state;
      return { ...state, status: "error", error: action.message };
    case "RETRY":
      if (state.status !== "error" || !state.pending) return state;
      return { ...state, status: "submitting", error: null, requestId: state.requestId + 1 };
    case "BACK_TO_REQUEST":
      return { ...state, status: "idle", followUp: null, error: null, pending: null, inputError: null };
    case "USE_EXAMPLE":
      return { ...initialState, requestId: state.requestId, text: action.text };
    case "EDIT_FIELD":
      if (!state.brief || state.status === "saving") return state;
      return { ...state, status: "editing", editingField: action.field, error: null };
    case "CANCEL_EDIT":
      return { ...state, status: "understood", editingField: null, error: null };
    case "SAVE_START":
      return state.status === "editing" ? { ...state, status: "saving" } : state;
    case "SAVE_DONE":
      return { ...state, status: "understood", brief: action.brief, edited: true, editingField: null };
    case "SAVE_FAILED":
      return { ...state, status: "editing", error: action.message };
    case "RESET":
      return { ...initialState, requestId: state.requestId };
    case "RESTORE":
      return { ...state, ...action.state };
  }
}

const PERSISTABLE: Status[] = ["idle", "follow-up", "examples", "understood"];

/** Snapshot safe to keep in sessionStorage; in-flight states restore as their stable parent. */
export function toSnapshot(s: RequestState): Partial<RequestState> {
  let status: Status = s.status;
  if (status === "editing" || status === "saving") status = "understood";
  if (!PERSISTABLE.includes(status)) status = "idle";
  return {
    status,
    text: s.text,
    submitted: status === "idle" ? null : s.submitted,
    followUp: status === "follow-up" ? s.followUp : null,
    brief: status === "understood" ? s.brief : null,
    edited: s.edited,
  };
}
