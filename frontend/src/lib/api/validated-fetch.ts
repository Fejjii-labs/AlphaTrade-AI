import type { ValidateFunction } from "ajv";
import { apiFetch, ApiError } from "./client";

type Options = Parameters<typeof apiFetch>[1] & {
  bodyValue?: unknown;
  requestValidator?: ValidateFunction;
  responseValidator: ValidateFunction;
  errorValidators?: Record<number, ValidateFunction>;
};

/** Validation uses generated schemas; the existing authenticated transport owns I/O. */
export async function validatedFetch<T>(path: string, options: Options): Promise<T> {
  const { bodyValue, requestValidator, responseValidator, errorValidators, ...transport } = options;
  if (requestValidator && !requestValidator(bodyValue)) {
    throw new ApiError("Invalid API request. Check the supplied fields.", 0, null);
  }
  let result: unknown;
  try {
    result = await apiFetch<unknown>(path, {
      ...transport,
      ...(requestValidator ? { body: JSON.stringify(bodyValue) } : {}),
    });
  } catch (error) {
    if (error instanceof ApiError && errorValidators?.[error.status] && !errorValidators[error.status](error.body)) {
      throw new ApiError("API error did not match its contract. Keep the original request for recovery.", error.status, null);
    }
    throw error;
  }
  if (!responseValidator(result)) {
    // Never include rejected payloads or validation data in user-visible errors/logs.
    throw new ApiError("API response did not match its contract. Refresh or try again later.", 0, null);
  }
  return result as T; // Narrowing occurs only after generated runtime validation.
}
