import { createHash } from "node:crypto";
import { expect, type Page, type WebSocketRoute } from "@playwright/test";
import type { AnnotationSave, AnnotationSnapshot, Artifact, ContextSnapshot, Event, ItemRecord, JobRecord, MessageSubmit, ProjectSnapshot, ProxyMapping, RequestRecord, RetainedArtifactSummary, ReviewDetail, ReviewRequest, ReviewTicket, RevisionCommand, ShotPlan, VersionRecord, WorkRecord } from "../../apps/frontend/src/api";
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

const IDENTITY_M4: ProxyMapping["nodes"][number]["world_matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

export function buildSyntheticProxyGlb(): Buffer {
  const s = 0.8;
  const faces: Array<[[number, number, number], [number, number, number], [number, number, number], [number, number, number]]> = [
    [[-s, -s, s], [s, -s, s], [s, s, s], [-s, s, s]],
    [[s, -s, -s], [-s, -s, -s], [-s, s, -s], [s, s, -s]],
    [[-s, s, s], [s, s, s], [s, s, -s], [-s, s, -s]],
    [[-s, -s, -s], [s, -s, -s], [s, -s, s], [-s, -s, s]],
    [[s, -s, s], [s, -s, -s], [s, s, -s], [s, s, s]],
    [[-s, -s, -s], [-s, -s, s], [-s, s, s], [-s, s, -s]],
  ];
  const pushQuad = (out: number[], [a, b, c, d]: typeof faces[number]) => {
    out.push(...a, ...b, ...c, ...a, ...c, ...d);
  };
  const primA: number[] = [];
  for (const face of faces.slice(0, 3)) pushQuad(primA, face);
  const primB: number[] = [];
  for (const face of faces.slice(3)) pushQuad(primB, face);
  const fullBox = [...primA, ...primB];
  const floats = new Float32Array([...primA, ...primB, ...fullBox]);
  const bin = Buffer.from(floats.buffer);
  const gltf = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    materials: [
      { name: "HousingSteel", pbrMetallicRoughness: { baseColorFactor: [0.35, 0.55, 0.82, 1], metallicFactor: 0.2, roughnessFactor: 0.6 } },
      { name: "HousingTrim", pbrMetallicRoughness: { baseColorFactor: [0.28, 0.44, 0.72, 1], metallicFactor: 0.2, roughnessFactor: 0.6 } },
      { name: "CollarCopper", pbrMetallicRoughness: { baseColorFactor: [0.85, 0.52, 0.28, 1], metallicFactor: 0.3, roughnessFactor: 0.5 } },
    ],
    nodes: [
      {
        name: "Housing",
        mesh: 0,
        translation: [1.6, 0, -1.6],
        children: [1],
        extras: { proscenium_object_id: "housing", proscenium_instance_id: "housing-1" },
      },
      {
        name: "Collar",
        mesh: 1,
        translation: [-3.2, 0.2, 3.2],
        rotation: [Math.sin(Math.PI / 12), 0, 0, Math.cos(Math.PI / 12)],
        scale: [1.5, 0.6, 1.1],
        extras: { proscenium_object_id: "collar", proscenium_instance_id: "collar-1" },
      },
    ],
    meshes: [
      { primitives: [{ attributes: { POSITION: 0 }, material: 0 }, { attributes: { POSITION: 1 }, material: 1 }] },
      { primitives: [{ attributes: { POSITION: 2 }, material: 2 }] },
    ],
    accessors: [
      { bufferView: 0, componentType: 5126, count: primA.length / 3, type: "VEC3", min: [-s, -s, -s], max: [s, s, s] },
      { bufferView: 1, componentType: 5126, count: primB.length / 3, type: "VEC3", min: [-s, -s, -s], max: [s, s, s] },
      { bufferView: 2, componentType: 5126, count: fullBox.length / 3, type: "VEC3", min: [-s, -s, -s], max: [s, s, s] },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: primA.length * 4 },
      { buffer: 0, byteOffset: primA.length * 4, byteLength: primB.length * 4 },
      { buffer: 0, byteOffset: (primA.length + primB.length) * 4, byteLength: fullBox.length * 4 },
    ],
    buffers: [{ byteLength: bin.length }],
  };
  const json = Buffer.from(JSON.stringify(gltf));
  const padded = (json.length + 3) & ~3;
  const total = 12 + 8 + padded + 8 + bin.length;
  const out = Buffer.alloc(total);
  out.write("glTF", 0);
  out.writeUInt32LE(2, 4);
  out.writeUInt32LE(total, 8);
  out.writeUInt32LE(padded, 12);
  out.write("JSON", 16);
  out.fill(0x20, 20, 20 + padded);
  json.copy(out, 20);
  out.writeUInt32LE(bin.length, 20 + padded);
  out.write("BIN\0", 24 + padded);
  bin.copy(out, 28 + padded);
  return out;
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
  versionRecords: VersionRecord[] = structuredClone(versions);
  requests: RequestRecord[] = [];
  retained = [retained("verified", "verified"), retained("legacy", "unrecorded")];
  artifactContent = new Map<string, { contentType: string; bytes: Buffer }>();
  proxyMappings = new Map<string, ProxyMapping[]>();
  unavailableProxyIds = new Set<string>();
  annotations: AnnotationSnapshot[] = [];
  annotationOperations = new Map<string, { state_revision: number; snapshot: AnnotationSnapshot }>();
  uploads = new Map<string, Artifact>();
  uploadCalls: string[] = [];
  saveAnnotationCalls: Array<{ operation_id: string; idempotencyKey: string | null; annotation_id: string }> = [];
  dropNextSaveAck = false;
  saveGate: Promise<void> | null = null;
  reviews: ReviewDetail[] = [];
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

  installProxyFixture(versionId = "a-v2", options: { proxyId?: string; mapId?: string; sampleFrame?: number } = {}): ProxyMapping {
    const targetVersion = this.versionRecords.find(v => v.version_id === versionId);
    if (!targetVersion) throw new Error(`Unknown fixture version ${versionId}`);
    const proxyId = options.proxyId ?? `proxy-${versionId}`;
    const mapId = options.mapId ?? `map-${versionId}`;
    const sampleFrame = options.sampleFrame ?? 1;
    const glb = buildSyntheticProxyGlb();
    const glbSha = createHash("sha256").update(glb).digest("hex");
    const mapping: ProxyMapping = {
      schema_version: "1.0",
      project_id: projectId,
      version_id: versionId,
      proxy_artifact_id: proxyId,
      mapping_artifact_id: mapId,
      source_closure_digest: targetVersion.source.digest,
      sample_frame: sampleFrame,
      space: "proxy_world_m",
      nodes: [
        { node_path: [0], object_id: "housing", instance_id: "housing-1", display_name: "Housing", world_matrix: IDENTITY_M4 },
        { node_path: [0, 0], object_id: "collar", instance_id: "collar-1", display_name: "Collar", world_matrix: IDENTITY_M4 },
      ],
    };
    const mapBytes = Buffer.from(JSON.stringify(mapping));
    const mapSha = createHash("sha256").update(mapBytes).digest("hex");
    const proxyArtifact: Artifact = { schema_version: "1.0", project_id: projectId, artifact_id: proxyId, kind: "proxy", purpose: "inspection", sha256: glbSha, bytes: glb.length, media_type: "model/gltf-binary", state: "ready", derived_from: [] };
    const mapArtifact: Artifact = { schema_version: "1.0", project_id: projectId, artifact_id: mapId, kind: "proxy_map", purpose: "metadata", sha256: mapSha, bytes: mapBytes.length, media_type: "application/json", state: "ready", derived_from: [] };
    this.retained.push(
      { schema_version: "1.0", artifact: proxyArtifact, attribution_status: "verified", request_id: null, work_id: null, invocation_id: null, job_id: null, published_versions: [{ item_id: targetVersion.item_id, version_id: versionId }] },
      { schema_version: "1.0", artifact: mapArtifact, attribution_status: "verified", request_id: null, work_id: null, invocation_id: null, job_id: null, published_versions: [{ item_id: targetVersion.item_id, version_id: versionId }] },
    );
    this.artifactContent.set(proxyId, { contentType: "model/gltf-binary", bytes: glb });
    this.artifactContent.set(mapId, { contentType: "application/json", bytes: mapBytes });
    if (!targetVersion.artifact_ids.includes(proxyId)) targetVersion.artifact_ids.push(proxyId);
    if (!targetVersion.artifact_ids.includes(mapId)) targetVersion.artifact_ids.push(mapId);
    const list = this.proxyMappings.get(versionId) ?? [];
    list.push(mapping);
    this.proxyMappings.set(versionId, list);
    return mapping;
  }

  addAssetVersion(versionId: string, name: string): VersionRecord {
    const next = version("asset-a", versionId, name, "asset");
    this.versionRecords.push(next);
    const item = this.snapshot.items.find(i => i.item_id === "asset-a");
    if (item) {
      item.current_version_id = versionId;
      item.state_revision += 1;
    }
    return next;
  }

  async install(options: { catalogue?: boolean; savedLayout?: boolean; created?: boolean; shotPlan?: ShotPlan } = {}) {
    this.page.on("pageerror", error => this.pageErrors.push(error.message));
    if (options.catalogue) this.snapshot.items = structuredClone(items);
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
      { path: `${base}/snapshot`, handler: async route => { const body = structuredClone({ ...this.snapshot, reviews: this.reviews.map(r => r.ticket) }); if (this.snapshotGate) await this.snapshotGate; await route.fulfill({ json: body }); } },
      { method: "GET", path: `${base}/reviews`, handler: route => { const filter = new URL(route.request().url()).searchParams.get("filter") ?? "all"; const items = this.reviews.map(r => r.ticket).filter(t => filter === "pending" ? t.awaiting_human === true : true); return route.fulfill({ json: { schema_version: "1.0", items, next_cursor: null } }); } },
      { method: "POST", path: `${base}/reviews`, handler: route => { const body = route.request().postDataJSON() as ReviewRequest; const ticket: ReviewTicket = { schema_version: "1.0", review_id: body.review_id, job_id: `job-${body.review_id}`, status: "pending", title: body.title ?? body.criteria.slice(0, 60), created_at: stamp, revision: 1, human_review_state: body.human_review === false ? "not_requested" : "pending", awaiting_human: body.human_review !== false, question: body.question ?? null, finding_count: 0, unreviewed_count: 0, error_code: null }; this.reviews.unshift({ schema_version: "1.0", project_id: projectId, ticket, criteria: body.criteria, targets: body.targets, telemetry_artifact_ids: body.telemetry_artifact_ids, findings: [], diagnostics: null }); return route.fulfill({ status: 202, json: ticket }); } },
      { method: "POST", path: new RegExp(`${base}/reviews/[^/]+/findings/[^/]+/reject$`), handler: route => { const parts = new URL(route.request().url()).pathname.split("/"); const findingId = parts.at(-2)!; const reviewId = parts.at(-4)!; const body = route.request().postDataJSON() as RevisionCommand; const detail = this.reviews.find(r => r.ticket.review_id === reviewId)!; const finding = detail.findings.find(f => f.finding_id === findingId)!; finding.state = "rejected"; finding.revision++; const unreviewed = detail.findings.filter(f => f.state === "unreviewed").length; detail.ticket = { ...detail.ticket, unreviewed_count: unreviewed, awaiting_human: detail.ticket.awaiting_human ? (unreviewed > 0 || Boolean(detail.ticket.question)) : false, human_review_state: detail.ticket.human_review_state === "not_requested" ? "not_requested" : ((unreviewed > 0 || Boolean(detail.ticket.question)) ? "pending" : "resolved"), revision: (detail.ticket.revision ?? 1) + 1 }; return route.fulfill({ json: { schema_version: "1.0", operation_id: body.operation_id, resource_id: findingId, state: "rejected", state_revision: finding.revision, replayed: false } }); } },
      { method: "POST", path: new RegExp(`${base}/reviews/[^/]+/resolve$`), handler: route => { const reviewId = new URL(route.request().url()).pathname.split("/").at(-2)!; const body = route.request().postDataJSON() as RevisionCommand; const detail = this.reviews.find(r => r.ticket.review_id === reviewId)!; const nextRev = (detail.ticket.revision ?? 1) + 1; detail.ticket = { ...detail.ticket, awaiting_human: false, human_review_state: "resolved", revision: nextRev }; return route.fulfill({ json: { schema_version: "1.0", operation_id: body.operation_id, resource_id: reviewId, state: "resolved", state_revision: nextRev, replayed: false } }); } },
      { method: "GET", path: new RegExp(`${base}/reviews/[^/]+/findings$`), handler: route => { const reviewId = new URL(route.request().url()).pathname.split("/").at(-2)!; const detail = this.reviews.find(r => r.ticket.review_id === reviewId); return route.fulfill({ json: { schema_version: "1.0", items: detail?.findings ?? [], next_cursor: null } }); } },
      { method: "GET", path: new RegExp(`${base}/reviews/[^/]+$`), handler: route => { const reviewId = new URL(route.request().url()).pathname.split("/").at(-1)!; const detail = this.reviews.find(r => r.ticket.review_id === reviewId); return detail ? route.fulfill({ json: detail }) : route.fulfill({ status: 404, json: { schema_version: "1.0", error: { code: "not_found", message: "Review not found", details: {} } } }); } },
      { method: "GET", path: `${base}/requests`, handler: async route => { const body = pageOf(structuredClone(this.requests)); if (this.requestsGate) await this.requestsGate; await route.fulfill({ json: body }); } },
      { path: `${base}/items`, handler: route => route.fulfill({ json: pageOf(this.snapshot.items) }) },
      { path: new RegExp(`${base}/items/[^/]+/versions$`), handler: route => route.fulfill({ json: pageOf(this.versionRecords.filter(v => route.request().url().includes(`/items/${v.item_id}/`))) }) },
      { path: `${base}/jobs`, handler: route => route.fulfill({ json: pageOf(this.snapshot.jobs) }) },
      { path: `${base}/events`, handler: route => route.fulfill({ json: { schema_version: "1.0", project_id: projectId, events: this.events.filter(e => e.sequence > Number(new URL(route.request().url()).searchParams.get("after") ?? 0)), last_available: this.events.length } }) },
      { path: `${base}/artifacts`, handler: route => route.fulfill({ json: pageOf(this.retained) }) },
      { path: `${base}/selections`, body: pageOf([]) },
      { method: "GET", path: `${base}/annotations`, handler: route => {
        const versionFilter = new URL(route.request().url()).searchParams.get("version_id");
        const list = versionFilter ? this.annotations.filter(a => a.target.version_id === versionFilter) : this.annotations;
        return route.fulfill({ json: pageOf(structuredClone(list)) });
      } },
      { method: "POST", path: `${base}/annotations`, handler: async route => {
        const body = route.request().postDataJSON() as AnnotationSave;
        const idempotencyKey = route.request().headers()["idempotency-key"] ?? null;
        this.saveAnnotationCalls.push({ operation_id: body.operation_id, idempotencyKey, annotation_id: body.annotation.annotation_id });
        if (this.saveGate) await this.saveGate;
        const replayed = this.annotationOperations.get(body.operation_id);
        if (replayed) {
          return route.fulfill({ json: { schema_version: "1.0", operation_id: body.operation_id, resource_id: body.annotation.annotation_id, state: "saved", state_revision: replayed.state_revision, replayed: true } });
        }
        const existingIndex = this.annotations.findIndex(a => a.annotation_id === body.annotation.annotation_id);
        const currentRev = existingIndex >= 0 ? (this.annotations[existingIndex].saved_revision ?? 0) : 0;
        if (body.expected_revision !== currentRev) {
          return route.fulfill({ status: 409, json: { schema_version: "1.0", error: { code: "revision_conflict", message: "Annotation revision conflict", operation_id: body.operation_id, retryable: false, details: { current_revision: currentRev } } } });
        }
        const nextRev = currentRev + 1;
        const savedSnapshot: AnnotationSnapshot = { ...structuredClone(body.annotation), saved_revision: nextRev };
        if (existingIndex >= 0) this.annotations[existingIndex] = savedSnapshot;
        else this.annotations.push(savedSnapshot);
        this.annotationOperations.set(body.operation_id, { state_revision: nextRev, snapshot: savedSnapshot });
        if (this.dropNextSaveAck) {
          this.dropNextSaveAck = false;
          await route.abort("failed");
          return;
        }
        return route.fulfill({ json: { schema_version: "1.0", operation_id: body.operation_id, resource_id: body.annotation.annotation_id, state: "saved", state_revision: nextRev, replayed: false } });
      } },
      { method: "PUT", path: new RegExp(`${base}/uploads/[^/]+$`), handler: route => {
        const url = new URL(route.request().url());
        const uploadId = decodeURIComponent(url.pathname.split("/").at(-1)!);
        this.uploadCalls.push(uploadId);
        const existing = this.uploads.get(uploadId);
        if (existing) return route.fulfill({ status: 200, json: existing });
        const bytes = route.request().postDataBuffer() ?? png;
        const sha256 = createHash("sha256").update(bytes).digest("hex");
        const isPng = bytes.length >= 24 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
        const width = isPng ? bytes.readUInt32BE(16) : 1;
        const height = isPng ? bytes.readUInt32BE(20) : 1;
        const artifactId = `upload-${uploadId}`;
        const artifact: Artifact = {
          schema_version: "1.0",
          project_id: projectId,
          artifact_id: artifactId,
          kind: "image",
          purpose: "reference",
          sha256,
          bytes: bytes.length,
          media_type: "image/png",
          state: "ready",
          dimensions: { width, height },
          derived_from: [],
        };
        this.uploads.set(uploadId, artifact);
        this.artifactContent.set(artifactId, { contentType: "image/png", bytes });
        this.retained.push({ schema_version: "1.0", artifact, attribution_status: "verified", request_id: null, work_id: null, invocation_id: null, job_id: null, published_versions: [] });
        return route.fulfill({ status: 201, json: artifact });
      } },
      { path: new RegExp(`${base}/versions/[^/]+/proxy-mapping$`), handler: route => {
        const url = new URL(route.request().url());
        const parts = url.pathname.split("/");
        const versionId = decodeURIComponent(parts.at(-2)!);
        const requestedMapId = url.searchParams.get("mapping_artifact_id");
        const mappings = this.proxyMappings.get(versionId) ?? [];
        if (!mappings.length) {
          return route.fulfill({ status: 404, json: { schema_version: "1.0", error: { code: "not_found", message: "Synthetic fixture has no proxy", operation_id: null, retryable: false, details: {} } } });
        }
        const chosen = requestedMapId ? mappings.find(m => m.mapping_artifact_id === requestedMapId) : mappings.at(-1);
        if (!chosen) {
          return route.fulfill({ status: 404, json: { schema_version: "1.0", error: { code: "not_found", message: "Requested proxy mapping not found", operation_id: null, retryable: false, details: {} } } });
        }
        if (this.unavailableProxyIds.has(chosen.proxy_artifact_id) || (chosen.mapping_artifact_id && this.unavailableProxyIds.has(chosen.mapping_artifact_id))) {
          return route.fulfill({ status: 422, json: { schema_version: "1.0", error: { code: "media_unavailable", message: "Pinned 3D proxy artefact is offline", operation_id: null, retryable: false, details: {} } } });
        }
        return route.fulfill({ json: chosen });
      } },
      { path: `${base}/versions/b-v1/shot-plan`, body: options.shotPlan ?? { schema_version: "1.0", project_id: projectId, plan_id: "plan", storyboard_version: { item_id: "board-b", version_id: "b-v1" }, asset_dependencies: [], unresolved: [], shots: [{ shot_id: "shot-1", intent: "Shot one", camera_intent: "Static", motion_beats: [], scene_version: null, source_range: null, fps: null, frames: [] }] } },
      // Changing picker item can briefly leave the previous version selected. The
      // backend rejects this optional read rather than fabricating an asset plan.
      { path: new RegExp(`${base}/versions/a-v[0-9]+/shot-plan$`), status: 404, body: { schema_version: "1.0", error: { code: "not_found", message: "Not a storyboard", details: {} } } },
      { path: new RegExp(`${base}/artifacts/[^/]+/content$`), handler: route => {
        const artifactId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
        if (this.unavailableProxyIds.has(artifactId)) {
          return route.fulfill({ status: 422, json: { schema_version: "1.0", error: { code: "media_unavailable", message: "Artefact offline", operation_id: null, retryable: false, details: {} } } });
        }
        const stored = this.artifactContent.get(artifactId);
        return route.fulfill({ contentType: stored?.contentType ?? "image/png", body: stored?.bytes ?? png });
      } },
      { path: new RegExp(`${base}/artifacts/[^/]+$`), handler: route => {
        const artifactId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1)!);
        return route.fulfill({ json: this.retained.find(r => r.artifact.artifact_id === artifactId)?.artifact });
      } },
      { method: "POST", path: `${base}/contexts`, handler: async route => { const body = route.request().postDataJSON() as ContextSnapshot; this.contexts.push(body); if (this.contextGate) await this.contextGate; await route.fulfill({ status: 201, json: body }); } },
      { method: "GET", path: new RegExp(`${base}/contexts/[^/]+$`), handler: route => { const contextId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1)!); return route.fulfill({ json: this.contexts.find(c => c.context_id === contextId) ?? { schema_version: "1.0", context_id: "context", project_id: projectId, captured_at: stamp, item_id: null, version_id: null, view_mode: "none", target: null, camera: null, range: null, selected_objects: [], attachments: [], annotations: [], approved_findings: [] } }); } },
      { method: "POST", path: `${base}/requests`, handler: route => { const body = route.request().postDataJSON() as MessageSubmit; this.submissions.push(body); if (body.type !== "stop") { const ctx = this.contexts.find(c => c.context_id === body.context_id); for (const af of ctx?.approved_findings ?? []) { for (const detail of this.reviews) { const match = detail.findings.find(f => f.finding_id === af.finding_id); if (match) { match.state = "submitted"; match.revision++; const unreviewed = detail.findings.filter(f => f.state === "unreviewed").length; detail.ticket = { ...detail.ticket, unreviewed_count: unreviewed, awaiting_human: unreviewed > 0 ? Boolean(detail.ticket.awaiting_human) : false, human_review_state: unreviewed > 0 ? detail.ticket.human_review_state : "resolved", revision: (detail.ticket.revision ?? 1) + 1 }; } } } } const record = request(body.request_id, body.type === "stop" ? "" : body.text, "accepted"); record.submission = body; record.receipt.type = body.type; this.requests.push(record); return route.fulfill({ status: 202, json: record.receipt }); } },
      { method: "GET", path: new RegExp(`${base}/requests/[^/]+$`), handler: route => { const requestId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1)!); return route.fulfill({ json: this.requests.find(r => r.request_id === requestId) }); } },
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
