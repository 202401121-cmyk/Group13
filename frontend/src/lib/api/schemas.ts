/**
 * Checks live responses against the contracts the backend exports to shared/contracts/schema.
 *
 * ajv and the schemas are loaded on first use, so mock mode never downloads them. A response that does not
 * match its schema is rejected with ApiError("bad_response"): the screens never build on a guessed shape.
 */
import type { CriteriaSet } from "@/types/contracts/CriteriaSet";
import type { EvidenceResponse } from "@/types/contracts/EvidenceResponse";
import type { ExclusionParseResponse } from "@/types/contracts/ExclusionParseResponse";
import type { IntakeResponse } from "@/types/contracts/IntakeResponse";
import type { Intent } from "@/types/contracts/Intent";
import type { InterviewStep } from "@/types/contracts/InterviewStep";
import type { PresearchSession } from "@/types/contracts/PresearchSession";
import type { ProgressEvent } from "@/types/contracts/ProgressEvent";
import type { ReplyRequest } from "@/types/contracts/ReplyRequest";
import type { RubricEditResponse } from "@/types/contracts/RubricEditResponse";
import type { SearchResult } from "@/types/contracts/SearchResult";
import type { SearchRun } from "@/types/contracts/SearchRun";
import type { SearchRunStarted } from "@/types/contracts/SearchRunStarted";
import { ApiError, MESSAGES } from "./client";

export interface Contracts {
  IntakeResponse: IntakeResponse;
  Intent: Intent;
  CriteriaSet: CriteriaSet;
  InterviewStep: InterviewStep;
  PresearchSession: PresearchSession;
  ReplyRequest: ReplyRequest;
  SearchResult: SearchResult;
  SearchRun: SearchRun;
  SearchRunStarted: SearchRunStarted;
  ProgressEvent: ProgressEvent;
  EvidenceResponse: EvidenceResponse;
  RubricEditResponse: RubricEditResponse;
  ExclusionParseResponse: ExclusionParseResponse;
}
export type ContractName = keyof Contracts;

type Validator = (value: unknown) => boolean;

const SCHEMAS: Record<ContractName, () => Promise<{ default: unknown }>> = {
  IntakeResponse: () => import("@contracts/schema/IntakeResponse.json"),
  Intent: () => import("@contracts/schema/Intent.json"),
  CriteriaSet: () => import("@contracts/schema/CriteriaSet.json"),
  InterviewStep: () => import("@contracts/schema/InterviewStep.json"),
  PresearchSession: () => import("@contracts/schema/PresearchSession.json"),
  ReplyRequest: () => import("@contracts/schema/ReplyRequest.json"),
  SearchResult: () => import("@contracts/schema/SearchResult.json"),
  SearchRun: () => import("@contracts/schema/SearchRun.json"),
  SearchRunStarted: () => import("@contracts/schema/SearchRunStarted.json"),
  ProgressEvent: () => import("@contracts/schema/ProgressEvent.json"),
  EvidenceResponse: () => import("@contracts/schema/EvidenceResponse.json"),
  RubricEditResponse: () => import("@contracts/schema/RubricEditResponse.json"),
  ExclusionParseResponse: () => import("@contracts/schema/ExclusionParseResponse.json"),
};

/** CommonJS packages arrive as { default } or { default: { default } } depending on the bundler. */
function interop<T>(mod: unknown): T {
  const first = (mod as { default?: unknown }).default ?? mod;
  return ((first as { default?: unknown }).default ?? first) as T;
}

interface AjvLike {
  compile(schema: object): Validator;
}

let ajv: Promise<AjvLike> | null = null;
const validators = new Map<ContractName, Promise<Validator>>();

function loadAjv(): Promise<AjvLike> {
  ajv ??= Promise.all([import("ajv/dist/2020"), import("ajv-formats")]).then(
    ([ajvMod, formatsMod]) => {
      const Ajv2020 = interop<new (opts: object) => AjvLike>(ajvMod);
      const addFormats = interop<(a: AjvLike) => void>(formatsMod);
      const instance = new Ajv2020({ strict: false, allErrors: false });
      addFormats(instance);
      return instance;
    },
  );
  ajv.catch(() => {
    ajv = null; // a failed download can be tried again
  });
  return ajv;
}

function validatorFor(name: ContractName): Promise<Validator> {
  let v = validators.get(name);
  if (!v) {
    v = Promise.all([loadAjv(), SCHEMAS[name]()]).then(([a, schema]) =>
      a.compile(schema.default as object),
    );
    v.catch(() => validators.delete(name)); // a failed download can be tried again
    validators.set(name, v);
  }
  return v;
}

/** Fetches these schemas now, so a run doesn't need the network for them at its very end. */
export function loadContracts(...names: ContractName[]) {
  for (const name of names) {
    validatorFor(name).catch(() => {
      /* checking the first answer that needs it will try again, and say so if that fails too */
    });
  }
}

/** Returns the value typed as its contract, or throws ApiError("bad_response"). */
export async function checkContract<N extends ContractName>(
  name: N,
  value: unknown,
): Promise<Contracts[N]> {
  let validate: Validator;
  try {
    validate = await validatorFor(name);
  } catch {
    // The schema's code couldn't be downloaded. That says nothing about the answer, or about the run.
    throw new ApiError("network", MESSAGES.network);
  }
  if (!validate(value)) throw new ApiError("bad_response", MESSAGES.badResponse);
  return value as Contracts[N];
}
