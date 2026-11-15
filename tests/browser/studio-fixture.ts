import { expect, type Page, type WebSocketRoute } from "@playwright/test";
import type { ContextSnapshot, Event, ItemRecord, JobRecord, MessageSubmit, ProjectSnapshot, RequestRecord, RetainedArtifactSummary, ShotPlan, VersionRecord, WorkRecord } from "../../apps/frontend/src/api";
import { installFakeStudioApi, type MockRoute } from "./fixtures";
import type { ConfigurationStatus, ConfigurationView } from "../../apps/frontend/src/api";

export const projectId = "isolated-uat";
export const base = `/api/v1/projects/${projectId}`;
export const stamp = "2026-11-15T08:00:00Z";
export const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");
export const project = { schema_version: "1.0" as const, project_id: projectId, repository_id: "repository", name: "Isolated UAT", path: "/synthetic/uat", native_session_id: null, state: "active" as const, state_revision: 0 };
export const pageOf = <T>(items: T[]) => ({ items, next_cursor: null });
export function request(id: string, text: string, state: RequestRecord["receipt"]["state"] = "completed", accepted_at = stamp): RequestRecord {
  return { schema_version: "1.0", project_id: projectId, request_id: id, accepted_at, effective_profile_id: null, generation_task_id: "task", submission: { schema_version: "1.0", project_id: projectId, request_id: id, type: "queue", text, context_id: "context" }, receipt: { schema_version: "1.0", project_id: projectId, request_id: id, type: "queue", state, work_id: "work", native_message_id: null, state_revision: 0, replayed: false } };
}
export function work(state: WorkRecord["state"] = "running", revision = 1): WorkRecord {
  return { schema_version: "1.0", project_id: projectId, work_id: "work", originating_request_id: "initial", directive_request_ids: [], generation_task_id: "task", native_session_id: "synthetic-session", state, state_revision: revision, job_ids: [] };
}
export function job(job_id: string, state: JobRecord["state"]): JobRecord {
  return { schema_version: "1.0", project_id: projectId, job_id, state, state_revision: 1, kind: "image_generation", work_id: "work", owner_operation_id: null, target: { target_id: "task", version: null, shot_id: null }, attempt_id: `attempt-${job_id}`, attempt_number: 1, availability: "pending", input_digest: "a".repeat(64), external_job_id: null, provider_id: "synthetic", owner_epoch: "synthetic", completed_frames: null, expected_frames: null, artifact_ids: [], render_spec: null };
}
export function snapshot(active_work: WorkRecord | null = null): ProjectSnapshot {
  return { schema_version: "1.0", project, settings: { generation_attempt_limit: 3, default_profile_id: null, state_revision: 0 }, event_sequence: 0, active_work, held_requests: [], items: [], jobs: [], pending_decisions: [], execution: { fenced: false, fence_reason: null, queue_released: true, can_steer_work_id: active_work?.state === "running" ? active_work.work_id : null, can_stop_work_id: active_work?.work_id ?? null, hold_reason_code: null, hold_reason: null, active_agents: [], work_outputs: [] } };
}
export function version(item_id: string, version_id: string, name: string, kind: VersionRecord["kind"] = "asset"): VersionRecord {
  return { schema_version: "1.0", project_id: projectId, item_id, version_id, name, kind, publication_id: "publication", source_commit: "a".repeat(40), source: { entry_path: "source.json", files: [{ destination: "source.json", origin: { kind: "retained", artifact_id: "synthetic-source", sha256: "a".repeat(64) } }], dependencies: [], required_runtime: { application: "synthetic", version: "1", features: [] }, digest: "a".repeat(64) }, artifact_ids: kind === "asset" ? ["verified"] : [], preview_requests: [], preview_job_ids: [], created_at: stamp, actor: { kind: "tool", id: "synthetic" } };
}
export const versions = [version("asset-a", "a-v1", "Asset A v1"), version("asset-a", "a-v2", "Asset A v2"), version("board-b", "b-v1", "Storyboard B", "storyboard")];
export const items: ItemRecord[] = [
  { schema_version: "1.0", project_id: projectId, item_id: "asset-a", kind: "asset", name: "Asset A", current_version_id: "a-v2", selected_version_id: null, state_revision: 1 },
  { schema_version: "1.0", project_id: projectId, item_id: "board-b", kind: "storyboard", name: "Storyboard B", current_version_id: "b-v1", selected_version_id: null, state_revision: 1 },
];
export function retained(id: string, attribution_status: "verified" | "unrecorded", media_type = "image/png"): RetainedArtifactSummary {
  return { schema_version: "1.0", artifact: { schema_version: "1.0", project_id: projectId, artifact_id: id, kind: media_type === "text/html" ? "document" : "image", purpose: "concept", sha256: "a".repeat(64), bytes: png.length, media_type, state: "ready", dimensions: { width: 1, height: 1 }, derived_from: [] }, attribution_status, request_id: null, work_id: null, invocation_id: null, job_id: null, published_versions: [] };
}
export class StudioFixture {
  configuration: ConfigurationView = {
    schema_version: "1.0", revision: 0, generation_attempt_limit: 3,
    profiles: [{ profile_id: "authoring", name: "Project authoring", lead_model_id: "gpt-6.1-sol", worker_model_ids: ["gpt-6.1-sol"], scope_status: "project_scoped" }],
    services: [
      { provider_id: "local-comfy", endpoint: "http://127.0.0.1:8188", capabilities: ["image"], model_id: "flux-2-klein-4b.safetensors", available: null },
      { provider_id: "local-vlm", endpoint: "http://127.0.0.1:1234", capabilities: ["vlm"], model_id: "qwen/qwen3.8-27b", available: true },
    ],
  };
  configurationStatus: ConfigurationStatus = {
    schema_version: "1.0", observed_at: stamp, rendering_ready: true, blender_mcp_configured: true, blendswap_mcp_configured: true, unity_configured: false,
    providers: [
      { provider_id: "local-comfy", capability: "image", status: "ready", profile_revision: "comfy-image-1", selected_models: ["flux-2-klein-4b.safetensors"], compatible_models: [], reference_dimensions: null, reason_code: null, summary: "Observed recipe preflight passed", default_dimensions: { width: 512, height: 512 }, default_steps: 4, max_pixels: 1048576 },
      { provider_id: "local-vlm", capability: "vlm", status: "configured", profile_revision: null, selected_models: ["qwen/qwen3.8-27b"], compatible_models: [], reference_dimensions: null, reason_code: null, summary: "Model listing does not prove vision inference" },
    ],
  };
  snapshot = snapshot();
  requests: RequestRecord[] = [];
  retained = [retained("verified", "verified"), retained("legacy", "unrecorded")];
  events: Event[] = [];
  contexts: ContextSnapshot[] = [];
  submissions: MessageSubmit[] = [];
  pageErrors: string[] = [];
  sockets: WebSocketRoute[] = [];
  resumePolls = 0;
  resumeComplete = false;
  contextGate: Promise<void> | null = null;
  snapshotGate: Promise<void> | null = null;
  requestsGate: Promise<void> | null = null;
  guard: Awaited<ReturnType<typeof installFakeStudioApi>> | null = null;
  constructor(readonly page: Page, readonly origin: string) {}
  async install(options: { catalogue?: boolean; savedLayout?: boolean; created?: boolean; shotPlan?: ShotPlan } = {}) {
    this.page.on("pageerror", error => this.pageErrors.push(error.message));
    if (options.catalogue) this.snapshot.items = items;
    await this.page.addInitScript(({ id, saved, created }) => {
      localStorage.setItem("proscenium.owner.session", JSON.stringify({ csrf_token: "synthetic-only" }));
      if (saved) localStorage.setItem(`proscenium.owner.layout.v1.${id}`, JSON.stringify({ mode: "split", navigatorCollapsed: false, assistantCollapsed: false, navigatorWidth: 280, assistantWidth: 400 }));
      if (created) localStorage.setItem(`proscenium.owner.created.${id}`, "true");
    }, { id: projectId, saved: options.savedLayout ?? false, created: options.created ?? false });
    const routes: MockRoute[] = [
      { path: "/api/v1/projects", body: pageOf([project]) },
      { path: "/api/v1/configuration", handler: route => route.fulfill({ json: this.configuration }) },
      { path: "/api/v1/configuration/status", handler: route => route.fulfill({ json: this.configurationStatus }) },
      { method: "PUT", path: /\/api\/v1\/configuration\/profiles\/[^/]+$/, handler: route => { const body = route.request().postDataJSON(); const profile = { profile_id: body.profile_id, name: body.name, lead_model_id: body.lead_model_id, worker_model_ids: body.worker_model_ids, scope_status: "project_scoped" as const }; const index = this.configuration.profiles.findIndex(p => p.profile_id === profile.profile_id); if (index < 0) this.configuration.profiles.push(profile); else this.configuration.profiles[index] = profile; this.configuration.revision++; return route.fulfill({ json: this.configuration }); } },
      { method: "POST", path: /\/api\/v1\/configuration\/profiles\/[^/]+\/delete$/, handler: route => { const body = route.request().postDataJSON(); this.configuration.profiles = this.configuration.profiles.filter(p => p.profile_id !== body.profile_id); this.configuration.revision++; return route.fulfill({ json: this.configuration }); } },
      { method: "PUT", path: `${base}/settings`, handler: route => { const body = route.request().postDataJSON(); Object.assign(this.snapshot.settings, { default_profile_id: body.default_profile_id, generation_attempt_limit: body.generation_attempt_limit, state_revision: this.snapshot.settings.state_revision + 1 }); return route.fulfill({ json: { schema_version: "1.0", operation_id: body.operation_id, resource_id: projectId, state: "updated", state_revision: this.snapshot.settings.state_revision, replayed: false } }); } },
      { path: `${base}/snapshot`, handler: async route => { const body = structuredClone(this.snapshot); if (this.snapshotGate) await this.snapshotGate; await route.fulfill({ json: body }); } },
      { method: "GET", path: `${base}/requests`, handler: async route => { const body = pageOf(structuredClone(this.requests)); if (this.requestsGate) await this.requestsGate; await route.fulfill({ json: body }); } },
      { path: `${base}/items`, handler: route => route.fulfill({ json: pageOf(this.snapshot.items) }) },
      { path: new RegExp(`${base}/items/[^/]+/versions$`), handler: route => route.fulfill({ json: pageOf(versions.filter(v => route.request().url().includes(`/items/${v.item_id}/`))) }) },
      { path: `${base}/jobs`, handler: route => route.fulfill({ json: pageOf(this.snapshot.jobs) }) },
      { path: `${base}/events`, handler: route => route.fulfill({ json: { schema_version: "1.0", project_id: projectId, events: this.events.filter(e => e.sequence > Number(new URL(route.request().url()).searchParams.get("after") ?? 0)), last_available: this.events.length } }) },
      { path: `${base}/artifacts`, handler: route => route.fulfill({ json: pageOf(this.retained) }) },
      { path: `${base}/selections`, body: pageOf([]) },
      { path: `${base}/annotations`, body: pageOf([]) },
      { path: new RegExp(`${base}/versions/[^/]+/proxy-mapping$`), status: 404, body: { schema_version: "1.0", error: { code: "not_found", message: "Synthetic fixture has no proxy", details: {} } } },
      { path: `${base}/versions/b-v1/shot-plan`, body: options.shotPlan ?? { schema_version: "1.0", project_id: projectId, plan_id: "plan", storyboard_version: { item_id: "board-b", version_id: "b-v1" }, asset_dependencies: [], unresolved: [], shots: [{ shot_id: "shot-1", intent: "Shot one", camera_intent: "Static", motion_beats: [], scene_version: null, source_range: null, fps: null, frames: [] }] } },
      // Changing picker item can briefly leave the previous version selected. The
      // backend rejects this optional read rather than fabricating an asset plan.
      { path: new RegExp(`${base}/versions/a-v[12]/shot-plan$`), status: 404, body: { schema_version: "1.0", error: { code: "not_found", message: "Not a storyboard", details: {} } } },
      { path: new RegExp(`${base}/artifacts/[^/]+/content$`), contentType: "image/png", body: png },
      { path: new RegExp(`${base}/artifacts/[^/]+$`), handler: route => route.fulfill({ json: this.retained.find(r => route.request().url().endsWith(`/${r.artifact.artifact_id}`))?.artifact }) },
      { method: "POST", path: `${base}/contexts`, handler: async route => { const body = route.request().postDataJSON() as ContextSnapshot; this.contexts.push(body); if (this.contextGate) await this.contextGate; await route.fulfill({ status: 201, json: body }); } },
      { method: "GET", path: new RegExp(`${base}/contexts/[^/]+$`), handler: route => route.fulfill({ json: this.contexts.find(c => route.request().url().endsWith(`/${c.context_id}`)) ?? { schema_version: "1.0", context_id: "context", project_id: projectId, captured_at: stamp, item_id: null, version_id: null, view_mode: "none", target: null, camera: null, range: null, selected_objects: [], attachments: [], annotations: [], approved_findings: [] } }) },
      { method: "POST", path: `${base}/requests`, handler: route => { const body = route.request().postDataJSON() as MessageSubmit; this.submissions.push(body); const record = request(body.request_id, body.type === "stop" ? "" : body.text, "accepted"); record.submission = body; record.receipt.type = body.type; this.requests.push(record); return route.fulfill({ status: 202, json: record.receipt }); } },
      { method: "GET", path: new RegExp(`${base}/requests/[^/]+$`), handler: route => route.fulfill({ json: this.requests.find(r => route.request().url().endsWith(`/${r.request_id}`)) }) },
      { method: "POST", path: new RegExp(`${base}/requests/[^/]+/cancel$`), handler: route => { const record = this.requests.find(r => route.request().url().includes(`/${r.request_id}/cancel`))!; record.receipt.state = "cancelled"; record.receipt.state_revision++; return route.fulfill({ status: 202, json: {} }); } },
      { method: "POST", path: `${base}/resume`, status: 202, body: { state: "accepted" } },
      { path: new RegExp(`${base}/resumes/[^/]+$`), handler: route => { this.resumePolls++; return route.fulfill({ json: { schema_version: "1.0", project_id: projectId, operation_id: new URL(route.request().url()).pathname.split("/").at(-1), mode: "continue_work", work_id: "work", state: this.resumeComplete ? "completed" : "pending", detail_code: null } }); } },
    ];
    this.guard = await installFakeStudioApi(this.page, routes, this.origin);
    await this.page.routeWebSocket("**/*", socket => {
      expect(new URL(socket.url()).pathname).toBe("/api/v1/events");
      expect(new URL(socket.url()).origin).toBe(this.origin.replace(/^http/, "ws"));
      this.sockets.push(socket);
      socket.onMessage(() => {
        socket.send(JSON.stringify({ schema_version: "1.0", type: "welcome", project_id: projectId, earliest_sequence: 0, last_sequence: this.events.length }));
        socket.send(JSON.stringify({ schema_version: "1.0", type: "caught_up", project_id: projectId, through_sequence: this.events.length }));
      });
    });
  }
  async open() { await this.page.goto(this.origin); }
  async ready() { await expect(this.page.locator(".connection")).toHaveText("Connected"); }
  emit(event_type: Event["event_type"], payload: unknown, occurred_at = stamp): Event {
    const event = { schema_version: "1.0", project_id: projectId, sequence: this.events.length + 1, occurred_at, event_type, actor: { kind: event_type.startsWith("subagent.") ? "subagent" : "lead", id: event_type.startsWith("subagent.") ? "synthetic-child" : "synthetic" }, work_id: "work", request_id: "initial", native_session_id: "synthetic-session", native_event_id: `native-${this.events.length + 1}`, payload } as Event;
    this.events.push(event); this.snapshot.event_sequence = event.sequence;
    this.sockets.at(-1)!.send(JSON.stringify(event));
    return event;
  }
  async setWork(state: WorkRecord["state"] | null, fenced = false) {
    const next = state ? work(state, this.events.length + 2) : work("completed", this.events.length + 2);
    this.snapshot.active_work = state ? next : null;
    this.snapshot.execution = { ...this.snapshot.execution!, fenced, can_steer_work_id: state === "running" && !fenced ? "work" : null, can_stop_work_id: state ? "work" : null, hold_reason: fenced ? "Execution awaits explicit Resume" : null, hold_reason_code: fenced ? "awaiting_resume" : null };
    this.emit("work.state_changed", next);
  }
  assertHermetic() { expect(this.guard?.unconfigured).toEqual([]); expect(this.pageErrors).toEqual([]); }
}

export function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}
