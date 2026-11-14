import type { Error as WireError } from "./generated/contracts";
import { routes, type Operations } from "./generated/operations";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly envelope: WireError) {
    super(envelope.error.message);
  }
}

export interface CallOptions<K extends keyof Operations> {
  path?: Operations[K]["path"];
  query?: Operations[K]["query"];
  body?: Operations[K]["body"];
  /** Required for mutations. Always reuse this identity after acknowledgement loss. */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

/** Owner browser only. Tool and worker credentials are deliberately not accepted here. */
export class ApiClient {
  private csrfToken: string | null = null;

  setCsrfToken(token: string): void { this.csrfToken = token; }

  async call<K extends keyof Operations>(operation: K, options: CallOptions<K> = {}): Promise<Operations[K]["response"]> {
    const route = routes[operation];
    let path: string = route.path;
    for (const [name, value] of Object.entries(options.path ?? {})) {
      path = path.replace(`{${name}}`, encodeURIComponent(String(value)));
    }
    if (path.includes("{")) throw new Error("Missing path parameter");
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined) query.set(name, String(value));
    }
    const headers: Record<string, string> = {};
    const mutation = !["GET", "HEAD"].includes(route.method);
    if (mutation) {
      if (!options.idempotencyKey) throw new Error("Mutation requires its original idempotency key");
      headers["Idempotency-Key"] = options.idempotencyKey;
      if (this.csrfToken) headers["X-CSRF-Token"] = this.csrfToken;
      if (!(options.body instanceof Blob)) headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path + (query.size ? `?${query}` : ""), {
      method: route.method, credentials: "same-origin", headers, signal: options.signal,
      body: options.body === undefined ? undefined : options.body instanceof Blob ? options.body : JSON.stringify(options.body),
    });
    if (!response.ok) throw new ApiError(response.status, await response.json() as WireError);
    return (route.binary ? await response.arrayBuffer() : await response.json()) as Operations[K]["response"];
  }
}
