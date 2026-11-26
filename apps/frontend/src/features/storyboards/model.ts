import type { Artifact, FrameReceipt, ItemRecord, JobRecord, SelectionRecord, SelectionSave, SelectionSlot, ShotPlan, VersionRecord } from "../../api/generated/contracts";
import { emptySelection, type TurnSelection } from "../../api/studio";

export type PlanShot = ShotPlan["shots"][number];
export type PlanFrame = PlanShot["frames"][number];

export function storyboardHeads(items: readonly ItemRecord[]): [itemId: string, versionId: string | null][] {
  return items.filter(item => item.kind === "storyboard").map(item => [item.item_id, item.current_version_id]);
}

export async function readStoryboardCatalogue(
  suppliedHeads: ReturnType<typeof storyboardHeads> | null, reload: number, lastReload: number,
  readItems: () => Promise<ItemRecord[]>, readVersions: (itemId: string) => Promise<VersionRecord[]>,
) {
  const catalogue = suppliedHeads === null || reload !== lastReload ? await readItems() : null;
  const boards = catalogue === null ? suppliedHeads ?? [] : storyboardHeads(catalogue);
  const versions = (await Promise.all(boards.map(([itemId]) => readVersions(itemId)))).flat();
  return { catalogue, boards, versions };
}

export function shotPlanArtifactIds(plan: ShotPlan, versionId: string, records: readonly SelectionRecord[]): Set<string> {
  const ids = new Set(plan.shots.flatMap(shot => shot.frames.flatMap(frame => frame.artifact_id ? [frame.artifact_id] : [])));
  for (const {slot, project_id, state, choice} of records) {
    if (project_id === plan.project_id && slot.kind === "storyboard_frame" && slot.storyboard_version.version_id === versionId && state === "selected" && choice?.kind === "artifact") ids.add(choice.artifact_id);
  }
  return ids;
}

export function frameSlot(version: VersionRecord, shotId: string, frameId: string): SelectionSlot {
  return { kind: "storyboard_frame", storyboard_version: { item_id: version.item_id, version_id: version.version_id }, shot_id: shotId, frame_id: frameId };
}

export function selectionFor(records: SelectionRecord[], version: VersionRecord, shotId: string, frameId: string): SelectionRecord | undefined {
  return records.find(record => {
    const slot = record.slot;
    return slot.kind === "storyboard_frame" && slot.storyboard_version.item_id === version.item_id
      && slot.storyboard_version.version_id === version.version_id && slot.shot_id === shotId && slot.frame_id === frameId;
  });
}

export function selectionCommand(projectId: string, version: VersionRecord, shotId: string, frameId: string,
  artifactId: string | null, current?: SelectionRecord): SelectionSave {
  return {
    schema_version: "1.0", operation_id: crypto.randomUUID(), project_id: projectId,
    selection_id: current?.selection_id ?? crypto.randomUUID(), expected_revision: current?.state_revision ?? 0,
    slot: frameSlot(version, shotId, frameId),
    choice: artifactId ? { kind: "artifact", artifact_id: artifactId } : null,
    action: artifactId ? "select" : "withdraw",
  };
}

/** Never infer render provenance/source frames from a filename or storyboard index. */
export function frameContext(version: VersionRecord, shot: PlanShot, frame: PlanFrame, artifact?: Artifact,
  render?: { job: JobRecord; receipt: FrameReceipt }): TurnSelection {
  const selection: TurnSelection = { ...emptySelection(), item_id: version.item_id, version_id: version.version_id,
    request_target: { kind: "shot", storyboard_version: { item_id: version.item_id, version_id: version.version_id }, shot_id: shot.shot_id }, execution_scope: "default" };
  if (!artifact || artifact.project_id !== version.project_id || artifact.state !== "ready" || artifact.kind !== "image" || !artifact.dimensions) return selection;
  if (artifact.purpose !== "reference" && artifact.purpose !== "concept") {
    if (!render) return selection;
    const { job, receipt } = render;
    const source = job.target.version;
    if (!source || job.kind !== "render" || job.project_id !== version.project_id || receipt.project_id !== version.project_id
      || receipt.job_id !== job.job_id || receipt.input_digest !== job.input_digest
      || receipt.version_id !== source.version_id || receipt.artifact_id !== artifact.artifact_id
      || receipt.sha256 !== artifact.sha256 || receipt.dimensions.width !== artifact.dimensions.width
      || receipt.dimensions.height !== artifact.dimensions.height
      || (artifact.source_version_id && artifact.source_version_id !== receipt.version_id)) return selection;
    return {
      ...selection, view_mode: "keyframes",
      target: { origin: "blender_render", artifact_id: artifact.artifact_id, displayed_sha256: artifact.sha256,
        dimensions: artifact.dimensions, item_id: source.item_id, version_id: source.version_id,
        shot_id: receipt.shot_id, render_job_id: receipt.job_id, source_frame: receipt.source_frame, media_artifact_id: null },
      attachments: [{ artifact_id: artifact.artifact_id, role: "render", use: `${shot.shot_id} / ${frame.frame_id} / ${frame.role}: ${frame.composition}` }],
    };
  }
  return {
    ...selection, view_mode: "keyframes",
    target: {
      origin: artifact.purpose, artifact_id: artifact.artifact_id, displayed_sha256: artifact.sha256,
      dimensions: artifact.dimensions, item_id: version.item_id, version_id: version.version_id,
      shot_id: shot.shot_id, render_job_id: null, source_frame: null, media_artifact_id: null,
    },
    attachments: [{ artifact_id: artifact.artifact_id, role: artifact.purpose, use: `${shot.shot_id} / ${frame.frame_id} / ${frame.role}: ${frame.composition}` }],
  };
}

export function dependencyStatus(artifact: Artifact | undefined, shot: PlanShot, versionIds: Set<string>): string {
  if (!artifact) return "Image metadata unavailable";
  if (artifact.state !== "ready") return `Image ${artifact.state}`;
  if (artifact.purpose === "reference" || artifact.purpose === "concept") return `${artifact.purpose} image, not scene geometry`;
  if (!shot.scene_version) return "Render dependency unresolved: no authored scene binding";
  if (!artifact.source_version_id) return "Render dependency unknown: source version absent";
  if (artifact.source_version_id !== shot.scene_version.version_id) return "Stale render: image source differs from authored scene version";
  return versionIds.has(artifact.source_version_id) ? "Render pinned to authored scene version" : "Render dependency not in loaded catalogue";
}
