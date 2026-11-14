/** Browser-private recovery data. Never copy these records into project context. */
import { ApiError } from "../api";
const prefix = "proscenium.owner.";
export const id = () => crypto.randomUUID();
export function readPrivate<T>(key: string): T | null {
  try { return JSON.parse(localStorage.getItem(prefix + key) ?? "null") as T | null; }
  catch { return null; }
}
export function writePrivate(key: string, value: unknown): void {
  if (value === null) localStorage.removeItem(prefix + key);
  else localStorage.setItem(prefix + key, JSON.stringify(value));
}
export function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "Operation unavailable. Refresh metadata before retrying.";
}
/** Freeze a command before sending. A failed operation retains its exact body for an explicit retry. */
export async function durableCommand<T extends { operation_id: string }, R>(
  key: string, create: () => T, send: (body: T) => Promise<R>,
): Promise<R> {
  const saved = readPrivate<T>(`command.${key}`);
  const proposed = create();
  if (saved) {
    const intent = (value: T) => JSON.stringify(Object.fromEntries(Object.entries(value).filter(([field]) => !["operation_id", "expected_revision"].includes(field))));
    if (intent(saved) !== intent(proposed)) throw new Error("An unconfirmed command with different inputs is retained. Retry the original action and inputs first. No changed command has been sent.");
  }
  const body = saved ?? proposed;
  writePrivate(`command.${key}`, body);
  try {
    const result = await send(body);
    writePrivate(`command.${key}`, null);
    return result;
  } catch (error) {
    // A definitive validation/CAS rejection admitted no new effect. Transport/server
    // uncertainty and conflicting operation reuse must retain their original evidence.
    if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.envelope.error.code !== "idempotency_conflict") {
      writePrivate(`command.${key}`, null);
    }
    throw error;
  }
}
