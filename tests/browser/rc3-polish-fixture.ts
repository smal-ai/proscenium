import { createHash } from "node:crypto";
import type { Page } from "@playwright/test";
import { PerspectiveCamera } from "three";
import type { AnnotationSnapshot, FrameAvailability, FrameReceipt, JobRecord, SelectionRecord, SelectionSave, ShotPlan } from "../../apps/frontend/src/api";
import { captureCamera } from "../../apps/frontend/src/features/review/media";
import type { MockRoute } from "./fixtures";
import { base, job, pageOf, projectId, retained, StudioFixture, version } from "./studio-fixture";

export const kitName = "SIGNAL LOST — Native Asset Kit / First Lookdev";
export const boardName = "SIGNAL LOST — Orbital Relay";
export const kitId = "signal-lost-native-kit";
export const boardId = "signal-lost-orbital-relay";
export const kitV = (n: number) => `${kitId}-v${n}`;
const boardV = (n: number) => `${boardId}-v${n}`;
const publishedAt = (n: number) => `2026-11-${14 + n}T08:00:00Z`;
const dimensions = { width: 1280, height: 720 };

const sourceCamera = new PerspectiveCamera(50, 4 / 3, 0.01, 10000);
sourceCamera.position.set(5, 4, 5);
sourceCamera.lookAt(0, 0, 0);
export const restoredRenderCamera = captureCamera(sourceCamera);

// Generated in Chromium, not taken from a workstation project. SVG gives the
// decoded frame and markup editor a recognisable, deterministic lookdev plate.
async function lookdevPlate(page: Page): Promise<Buffer> {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
    <defs>
      <radialGradient id="bg"><stop stop-color="#263e52"/><stop offset="1" stop-color="#0a121d"/></radialGradient>
      <linearGradient id="metal" x2="0.8" y2="1"><stop stop-color="#b1c4d2"/><stop offset="0.44" stop-color="#536b86"/><stop offset="1" stop-color="#182638"/></linearGradient>
      <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M0 40V0H40" fill="none" stroke="#89a9cc" stroke-opacity=".12"/></pattern>
    </defs>
    <path d="M0 0h1280v720H0z" fill="url(#bg)"/>
    <path d="M0 0h1280v720H0z" fill="url(#grid)"/>
    <g fill="none" stroke="#8fbbd6" stroke-opacity=".4"><circle cx="640" cy="360" r="270"/><circle cx="640" cy="360" r="248" stroke-dasharray="3 14"/><path d="M640 82v556M355 360h570" stroke-dasharray="2 10"/></g>
    <g transform="translate(640 350) rotate(-13)">
      <path d="M-356-42h712v95h-712z" fill="#1f3345" stroke="#7294aa" stroke-width="3"/>
      <path d="M-346-47h205v104h-205zm487 0h205v104H141z" fill="#243f62" stroke="#6e9fc0" stroke-width="3"/>
      <path d="M-332-42v95m26-95v95m26-95v95m26-95v95m26-95v95m26-95v95m26-95v95m26-95v95m302-95v95m26-95v95m26-95v95m26-95v95m26-95v95m26-95v95m26-95v95m26-95v95m-676-48h685" stroke="#8aaccf" stroke-opacity=".7"/>
      <path d="M-135-72l39-30H79l54 45v101l-53 53H-95l-40-35z" fill="url(#metal)" stroke="#9db8ce" stroke-width="3"/>
      <path d="M-122-64h228M-98 77H79M-129 43h249" stroke="#c5daea" stroke-opacity=".5" stroke-width="4"/>
      <ellipse cx="20" cy="-9" rx="99" ry="75" fill="#ad7f5a" stroke="#e9bb8d" stroke-width="4"/>
      <ellipse cx="32" cy="-17" rx="83" ry="65" fill="#44596d" stroke="#d5e0e9" stroke-width="4"/>
      <ellipse cx="43" cy="-24" rx="64" ry="50" fill="url(#metal)" stroke="#a6c4df" stroke-width="2"/>
      <path d="M45-26l90-92" stroke="#c3d6e7" stroke-width="9"/><circle cx="134" cy="-119" r="10" fill="#82e9fb"/>
      <circle cx="-104" cy="-30" r="5" fill="#86fff2"/><circle cx="-104" cy="-13" r="5" fill="#ffc889"/>
    </g>
    <g fill="#a9c5dc" font-family="monospace" letter-spacing="3"><text x="52" y="72" font-size="22">SIGNAL LOST / NATIVE ASSET KIT</text><text x="52" y="104" font-size="14">FIRST LOOKDEV · ORBITAL RELAY MODULE</text><text x="52" y="662" font-size="14">PROXY STUDY / HOUSING + SENSOR COLLAR</text></g>
    </svg>`;
  const data = await page.evaluate(async ({ svg, dimensions }) => {
    const image = new Image();
    image.src = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    await image.decode();
    const canvas = Object.assign(document.createElement("canvas"), dimensions);
    canvas.getContext("2d")!.drawImage(image, 0, 0);
    return canvas.toDataURL("image/png").split(",")[1];
  }, { svg, dimensions });
  return Buffer.from(data, "base64");
}

export async function makePolishStudio(page: Page, origin: string) {
  const studio = new StudioFixture(page, origin);
  const bytes = await lookdevPlate(page);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const routes: MockRoute[] = [];
  function image(id: string) {
    const record = retained(id, "verified");
    Object.assign(record.artifact, { dimensions, sha256, bytes: bytes.length });
    studio.artifactContent.set(id, { contentType: "image/png", bytes });
    studio.retained.push(record);
    return record.artifact;
  }
  studio.retained = [];
  const plate = image("native-kit-lookdev");
  studio.versionRecords = [1, 2, 3].map(n => ({ ...version(kitId, kitV(n), kitName), artifact_ids: [plate.artifact_id], created_at: publishedAt(n) }));
  studio.versionRecords.push(...[1, 2, 3, 4].map(n => ({ ...version(boardId, boardV(n), boardName, "storyboard"), created_at: publishedAt(n) })));
  const kit = { schema_version: "1.0" as const, project_id: projectId, item_id: kitId, kind: "asset" as const, name: kitName, current_version_id: kitV(3), selected_version_id: kitV(2), state_revision: 3 };
  studio.snapshot.items = [kit, { ...kit, item_id: boardId, kind: "storyboard", name: boardName, current_version_id: boardV(4), selected_version_id: null, state_revision: 4 }];
  // Enough real catalogue rows to exercise the scroll region independently of its toolbar.
  for (let n = 1; n <= 24; n++) {
    const itemId = `signal-lost-reference-material-for-orbital-relay-${n}`;
    const name = `Reference material · Panel study ${n}`;
    studio.versionRecords.push(version(itemId, `${itemId}-v1`, name));
    studio.snapshot.items.push({...kit, item_id: itemId, name, current_version_id: `${itemId}-v1`, selected_version_id: null, state_revision: 1});
  }
  studio.installProxyFixture(kitV(3));
  function pass(id: string, ordinals: number[], state: JobRecord["state"] = "succeeded") {
    const frames = { start: 1, end: ordinals.at(-1)! };
    const fps = { numerator: 24, denominator: 1 };
    const passJob: JobRecord = {
      ...job(id, state), kind: "render", target: { target_id: id, version: { item_id: kitId, version_id: kitV(3) }, shot_id: null },
      availability: state === "succeeded" ? "complete" : "partial", completed_frames: ordinals.length, expected_frames: frames.end,
      render_spec: { intent_id: id, shot_id: null, purpose: "keyframe", source_closure_digest: "a".repeat(64), scene_name: "First Lookdev", camera: id === "single-frame" ? restoredRenderCamera : null, frames, fps, dimensions, engine: "Cycles", device: "CPU", samples: 32, colour_management: { view_transform: "AgX", look: "None", exposure: 0, gamma: 1 }, renderer_revision: "synthetic" },
    };
    const receipts = ordinals.map((source_frame): FrameReceipt => {
      const artifact = image(`${id}-f${source_frame}`);
      passJob.artifact_ids.push(artifact.artifact_id);
      return { schema_version: "1.0", receipt_id: artifact.artifact_id, project_id: projectId, job_id: id, attempt_id: passJob.attempt_id, version_id: kitV(3), shot_id: null, source_frame, artifact_id: artifact.artifact_id, output_path: `${artifact.artifact_id}.png`, sha256, dimensions, input_digest: passJob.input_digest };
    });
    const availability: FrameAvailability = { schema_version: "1.0", project_id: projectId, job_id: id, version_id: kitV(3), source_range: frames, fps, available_ranges: ordinals.map(f => ({ start: f, end: f })), playable_ranges: [], pending_ranges: [], failed_ranges: [], state_revision: 1 };
    if (id === "sparse-lookdev") availability.pending_ranges = [{ start: 2, end: 11 }, { start: 13, end: 23 }];
    if (state === "running") {
      availability.source_range = { start: 1, end: 24 };
      availability.pending_ranges = [{ start: 2, end: 12 }];
      availability.failed_ranges = [{ start: 13, end: 24 }];
      passJob.render_spec!.frames = availability.source_range;
      passJob.expected_frames = 24;
    }
    studio.snapshot.jobs.push(passJob);
    routes.push({ path: `${base}/jobs/${id}/frames`, body: pageOf(receipts) }, { path: `${base}/jobs/${id}/segments`, body: pageOf([]) }, { path: `${base}/jobs/${id}/availability`, body: availability });
  }
  pass("single-frame", [1]);
  pass("sparse-lookdev", [1, 12, 24]);
  pass("in-progress", [1], "running");

  function note(id: string, text: string, item_id: string, version_id: string): AnnotationSnapshot {
    return { annotation_id: id, saved_revision: 1, target: { origin: "concept", artifact_id: plate.artifact_id, displayed_sha256: sha256, dimensions, item_id, version_id, shot_id: null, render_job_id: null, source_frame: null, media_artifact_id: null }, range: { start: 1, end: 1 }, camera: null, markups: [], text, spatial_hits: [], spatial_status: "unavailable" };
  }
  studio.annotations = [
    note("808c7db1-d285-4c8f-8549-e7dbabd4bf66", "Keep the housing silhouette readable", kitId, kitV(3)),
    note("86fb3468-0154-49ed-bd00-8bda75f11208", "Break the specular line at the collar seam", kitId, kitV(2)),
    note("fe0eee36-063d-432d-a32f-c8ad6b7e11ac", "Board only: hold the wide shot", boardId, boardV(4)),
  ];
  const plan: ShotPlan = {
    schema_version: "1.0", plan_id: "orbital-relay-plan", project_id: projectId, source_commit: "a".repeat(40),
    brief_artifact_ids: [], asset_dependencies: [{ item_id: kitId, version_id: kitV(2) }], unresolved: [],
    shots: [{ shot_id: "relay-establishing", intent: "Establish the relay before contact is lost", camera_intent: "Wide approach", motion_beats: [], scene_version: null, source_range: null, fps: null, frames: [{ frame_id: "relay-lookdev", role: "start", composition: "Hold the relay on the centreline", artifact_id: plate.artifact_id, state: "available", owner_selected: false }] }],
  };
  routes.push({ path: `${base}/versions/${boardV(4)}/shot-plan`, body: plan });
  let choice: SelectionRecord = { schema_version: "1.0", selection_id: "owner-kit", project_id: projectId, slot: { kind: "item", item_id: kitId }, choice: { kind: "version", version: { item_id: kitId, version_id: kitV(2) } }, state: "selected", state_revision: 1, actor: { kind: "user", id: "synthetic-owner" } };
  const commands: SelectionSave[] = [];
  routes.push(
    { path: `${base}/selections`, method: "GET", handler: route => route.fulfill({ json: pageOf([choice]) }) },
    { path: `${base}/selections`, method: "POST", handler: route => {
      const command = route.request().postDataJSON() as SelectionSave;
      commands.push(command);
      if (commands.length === 1) return route.abort("failed");
      choice = { ...choice, choice: command.choice, state: command.action === "withdraw" ? "withdrawn" : "selected", state_revision: 2 };
      return route.fulfill({ json: { schema_version: "1.0", operation_id: command.operation_id, resource_id: choice.selection_id, state: choice.state, state_revision: choice.state_revision, replayed: false } });
    } },
  );
  await studio.install({ savedLayout: true, routes });
  await studio.open();
  await studio.ready();
  function publishNextVersion() {
    const next = { ...version(kitId, kitV(4), kitName), artifact_ids: [plate.artifact_id], created_at: publishedAt(4) };
    studio.versionRecords.push(next);
    kit.current_version_id = next.version_id;
    kit.state_revision++;
    studio.emit("publication.created", next, next.created_at);
  }
  return { studio, commands, publishNextVersion };
}
