/**
 * Checks live responses against the contracts the backend exports to shared/contracts/schema.
 *
 * ajv and the schemas are loaded on first use, so mock mode never downloads them. A response that does not
 * match its schema is rejected with ApiError("bad_response"): the screens never build on a guessed shape.
 */
import type { CriteriaSet } from "@/types/contracts/CriteriaSet";
import type { IntakeResponse } from "@/types/contracts/IntakeResponse";
import type { Intent } from "@/types/contracts/Intent";
import type { InterviewStep } from "@/types/contracts/InterviewStep";
import type { PresearchSession } from "@/types/contracts/PresearchSession";
import type { ReplyRequest } from "@/types/contracts/ReplyRequest";
import type { SearchResult } from "@/types/contracts/SearchResult";
import { ApiError, MESSAGES } from "./client";

export interface Contracts {
  IntakeResponse: IntakeResponse;
  Intent: Intent;
  CriteriaSet: CriteriaSet;
  InterviewStep: InterviewStep;
  PresearchSession: PresearchSession;
  ReplyRequest: ReplyRequest;
  SearchResult: SearchResult;
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
  return ajv;
}

function validatorFor(name: ContractName): Promise<Validator> {
  let v = validators.get(name);
  if (!v) {
    v = Promise.all([loadAjv(), SCHEMAS[name]()]).then(([a, schema]) =>
      a.compile(schema.default as object),
    );
    validators.set(name, v);
  }
  return v;
}

/** Returns the value typed as its contract, or throws ApiError("bad_response"). */
export async function checkContract<N extends ContractName>(
  name: N,
  value: unknown,
): Promise<Contracts[N]> {
  const validate = await validatorFor(name);
  if (!validate(value)) throw new ApiError("bad_response", MESSAGES.badResponse);
  return value as Contracts[N];
}
