/** SYNTHETIC HTTP/socket fixture. No backend, model, native application or GPU is exercised. */
import { expect, type Page, type WebSocketRoute } from "@playwright/test";
import type { ContextSnapshot, Decision, Event, MessageSubmit, ProjectRecord, ProjectSnapshot, RequestRecord, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";

export const now = "2026-11-14T10:00:00Z";
export const session = { schema_version: "1.0", owner_session_id: "owner-synthetic", csrf_token: "synthetic-csrf-token-not-a-real-secret-0000", expires_at: "2026-11-15T10:00:00Z" };
export const project: ProjectRecord = { schema_version: "1.0", project_id: "project-test", repository_id: "repo-test", name: "Synthetic production", path: "/workstation/synthetic", native_session_id: "native-synthetic", state: "active", state_revision: 2 };
export const permission: Decision = { schema_version: "1.0", decision_id: "decision-test", project_id: project.project_id, work_id: "work-test", kind: "permission", state: "pending", state_revision: 7, fingerprint: "a".repeat(64), prompt: "Publish the selected image to the declared external destination?", options: ["Only the declared image and destination"], delivery_state: "not_sent" };
export const version = (versionId: string): VersionRecord => ({ schema_version: "1.0", project_id: project.project_id, item_id: "item-test", version_id: versionId, kind: "scene", name: "Lighting study", publication_id: `publication-${versionId}`, source_commit: "b".repeat(40), source: { entry_path: "scene.blend", files: [{ destination: "scene.blend", origin: { kind: "git", repository_id: project.repository_id, commit: "b".repeat(40), path: "scene.blend", sha256: "c".repeat(64) } }], dependencies: [], required_runtime: { application: "Blender", version: "synthetic", features: [] }, digest: "d".repeat(64) }, artifact_ids: [], preview_requests: [], preview_job_ids: [], created_at: now, actor: { kind: "lead", id: "copilot" } });

export class SyntheticStudio {
  projects = [structuredClone(project)];
  snapshot: ProjectSnapshot = {
    schema_version: "1.0", project: structuredClone(project), settings: { generation_attempt_limit: 3, default_profile_id: "profile-test", state_revision: 1 }, event_sequence: 0,
    active_work: { schema_version: "1.0", work_id: "work-test", project_id: project.project_id, originating_request_id: "request-original", directive_request_ids: [], state: "running", state_revision: 1, native_session_id: "native-synthetic", job_ids: [], generation_task_id: "generation-test" },
    held_requests: [], items: [{ schema_version: "1.0", project_id: project.project_id, item_id: "item-test", name: "Lighting study", kind: "scene", current_version_id: "version-new", selected_version_id: null, state_revision: 2 }], jobs: [], pending_decisions: [],
  };
  requests: RequestRecord[] = [];
  contexts = new Map<string, ContextSnapshot>();
  events: Event[] = [];
  posts: { path: string; body: Record<string, unknown>; key: string | undefined }[] = [];
  sockets: WebSocketRoute[] = [];
  abortNextRequest = false;
  rejectNextRequest = false;
  pagination = false;
  pageReads: string[] = [];
  pairAttempts: Record<string, unknown>[] = [];
  abortPairing = false;
  async install(page: Page, authenticated = true) {
    if (authenticated) await page.addInitScript(value => localStorage.setItem("proscenium.owner.session", JSON.stringify(value)), session);
    await page.routeWebSocket("**/api/v1/events", ws => {
      this.sockets.push(ws);
      ws.onMessage(data => {
        const subscription = JSON.parse(String(data));
        expect(subscription.type).toBe("subscribe");
        ws.send(JSON.stringify({ schema_version: "1.0", type: "welcome", project_id: this.snapshot.project.project_id, earliest_sequence: 1, last_sequence: this.events.length }));
        for (const event of this.events.filter(e => e.sequence > subscription.after_sequence)) ws.send(JSON.stringify(event));
        ws.send(JSON.stringify({ schema_version: "1.0", type: "caught_up", project_id: this.snapshot.project.project_id, through_sequence: this.events.length }));
      });
    });
    await page.route("**/api/v1/**", async route => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const method = request.method();
      const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      const pageOf = (items: unknown[]) => ({ schema_version: "1.0", items, next_cursor: null });
      if (method === "GET") {
        this.pageReads.push(url.pathname + url.search);
        if (path.endsWith("/configuration")) return reply({ schema_version: "1.0", revision: 1, generation_attempt_limit: 3, profiles: [{ profile_id: "profile-test", name: "Configured lead", lead_model_id: "synthetic-model", worker_model_ids: ["synthetic-worker"], scope_status: "project_scoped" }], services: [{ provider_id: "image-service", endpoint: "http://independent.invalid", capabilities: ["image"], model_id: "synthetic-image-model", available: false }] });
        if (path.endsWith("/projects")) return reply(pageOf(this.projects));
        if (path.endsWith("/snapshot")) return reply(this.snapshot);
        if (path.endsWith("/items")) return reply(pageOf(this.snapshot.items));
        if (path.endsWith("/versions")) return reply(pageOf([version("version-new"), version("version-old")]));
        if (path.endsWith("/jobs")) return reply(pageOf(this.snapshot.jobs));
        if (path.endsWith("/requests")) {
          if (this.pagination && !url.searchParams.has("cursor")) return reply({ ...pageOf(this.requests.slice(0, 1)), next_cursor: "synthetic-page-2" });
          return reply(pageOf(this.pagination ? this.requests.slice(1) : this.requests));
        }
        if (path.endsWith("/events")) {
          const after = Number(url.searchParams.get("after") ?? 0);
          return reply({ schema_version: "1.0", events: this.events.filter(e => e.sequence > after), next_after: this.events.length, last_available: this.events.length });
        }
        if (path.includes("/contexts/")) return this.contexts.has(path.split("/").at(-1)!) ? reply(this.contexts.get(path.split("/").at(-1)!)) : reply({ error: { code: "not_found", message: "Context not found", retryable: false } }, 404);
        if (path.includes("/requests/")) {
          const record = this.requests.find(r => r.request_id === path.split("/").at(-1));
          return record ? reply(record) : reply({ error: { code: "not_found", message: "Request not found", retryable: false } }, 404);
        }
        if (path.endsWith("/content")) return route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0uUAAAAASUVORK5CYII=", "base64") });
      }
      const body = method === "PUT" && path.includes("/uploads/") ? {} : request.postDataJSON() as Record<string, unknown>;
      const commandReceipt = { schema_version: "1.0", operation_id: body.operation_id, resource_id: String(body.project_id ?? this.snapshot.project.project_id), state: "accepted", state_revision: 1, replayed: false };
      const key = request.headers()["idempotency-key"];
      if (path.endsWith("/owner/session")) {
        this.pairAttempts.push(body);
        expect(key).toBe(body.operation_id);
        if (this.abortPairing) { this.abortPairing = false; return route.abort("failed"); }
        return route.fulfill({ status: 201, contentType: "application/json", headers: { "Set-Cookie": "synthetic_owner=cookie; HttpOnly; SameSite=Strict; Path=/" }, body: JSON.stringify(session) });
      }
      this.posts.push({ path, body, key });
      expect(request.headers()["x-csrf-token"]).toBe(session.csrf_token);
      expect(key).toBe(body.operation_id ?? body.request_id ?? body.context_id ?? path.split("/").at(-1));
      if (path.endsWith("/contexts")) { this.contexts.set(String(body.context_id), body as unknown as ContextSnapshot); return reply(body, 201); }
      if (path.endsWith("/requests")) {
        if (this.rejectNextRequest) { this.rejectNextRequest = false; return route.abort("failed"); }
        const record = this.record(body as unknown as MessageSubmit);
        if (this.abortNextRequest) { this.abortNextRequest = false; return route.abort("failed"); }
        return reply(record.receipt, 202);
      }
      if (path.endsWith("/answer")) { this.snapshot.pending_decisions = []; return reply(commandReceipt); }
      if (path.endsWith("/resume")) { this.snapshot.project.state = "active"; return reply(commandReceipt); }
      if (path.endsWith("/cancel")) { const record = this.requests.find(r => path.includes(r.request_id)); if (record) record.receipt.state = "cancelled"; return reply(commandReceipt); }
      if (path.endsWith("/projects") && method === "POST") {
        const record: ProjectRecord = { ...project, project_id: "project-opened", name: String(body.name ?? "Opened project"), path: String(body.path), state: "inactive", state_revision: 0 };
        this.projects.push(record); return reply(record, 201);
      }
      if (path.endsWith("/activate")) { for (const p of this.projects) p.state = path.includes(p.project_id) ? "active" : "inactive"; this.snapshot = { ...this.snapshot, project: this.projects.find(p => p.state === "active")! }; return reply(commandReceipt); }
      if (path.endsWith("/rebind")) { this.projects = this.projects.map(p => p.project_id === body.project_id ? { ...p, path: String(body.new_path), state_revision: p.state_revision + 1 } : p); return reply(commandReceipt); }
      if (path.includes("/uploads/")) return reply({ schema_version: "1.0", project_id: project.project_id, artifact_id: "image-ready", kind: "image", purpose: "reference", state: "ready", sha256: "e".repeat(64), bytes: 50, media_type: "image/png", derived_from: [] }, 201);
      if (path.endsWith("/settings")) { this.snapshot.settings = { default_profile_id: String(body.default_profile_id), generation_attempt_limit: Number(body.generation_attempt_limit), state_revision: this.snapshot.settings.state_revision + 1 }; return reply(commandReceipt); }
      throw new Error(`Unexpected synthetic API operation ${method} ${path}`);
    });
  }
  record(submission: MessageSubmit, state: RequestRecord["receipt"]["state"] = "held"): RequestRecord {
    const existing = this.requests.find(r => r.request_id === submission.request_id);
    if (existing) return existing;
    const record: RequestRecord = { schema_version: "1.0", request_id: submission.request_id, project_id: submission.project_id, submission, receipt: { schema_version: "1.0", request_id: submission.request_id, project_id: submission.project_id, type: submission.type, state: submission.type === "stop" ? "stop_requested" : state, work_id: "work-test", native_message_id: null, state_revision: 1, replayed: false }, accepted_at: now, effective_profile_id: "profile-test", generation_task_id: "generation-test" };
    this.requests.push(record); return record;
  }
  emit(eventType: Event["event_type"], payload: object, send = true): Event {
    const event: Event = { schema_version: "1.0", project_id: this.snapshot.project.project_id, sequence: this.events.length + 1, occurred_at: now, event_type: eventType, request_id: null, work_id: "work-test", actor: { kind: "lead", id: "copilot" }, native_session_id: "native-synthetic", native_event_id: `native-${this.events.length + 1}`, payload };
    this.events.push(event);
    if (send) this.sockets.at(-1)?.send(JSON.stringify(event));
    return event;
  }
}
