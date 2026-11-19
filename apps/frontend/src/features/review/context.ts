import type { AnnotationSnapshot, Attachment, FrameTarget, VersionRecord } from "../../api/generated/contracts";
import { emptySelection, type TurnSelection } from "../../api/studio";
import { versionRequestTarget } from "../chat/target";

export function cleanTargetRole(target: FrameTarget): Extract<Attachment["role"], "concept" | "reference" | "render"> {
  if (target.origin === "concept") return "concept";
  if (target.origin === "reference" || target.origin === "proxy_capture") return "reference";
  return "render";
}

export function annotationContext(annotation: AnnotationSnapshot, versionKind?: VersionRecord["kind"]): TurnSelection {
  const target = annotation.target;
  const effectiveKind = versionKind ?? (target.origin === "sequence_render" ? "sequence" : undefined);
  const masks = [...new Set(annotation.markups.filter(m => m.kind === "mask").map(m => m.mask_artifact_id!))];
  const hits = annotation.spatial_status === "valid" ? (annotation.spatial_hits ?? []) : [];
  const selectedObjects = [...new Map(hits.map(hit => [`${hit.object_id}\0${hit.instance_id}`, {object_id: hit.object_id, instance_id: hit.instance_id}])).values()];
  return {...emptySelection(), item_id: target.item_id, version_id: target.version_id,
    ...(target.item_id && target.version_id ? {request_target: versionRequestTarget({item_id: target.item_id, version_id: target.version_id}, effectiveKind, target.shot_id)} : {}),
    view_mode: hits.length > 0 ? "3d_inspection" : target.media_artifact_id ? "animation" : "keyframes",
    selected_objects: selectedObjects,
    target: structuredClone(target),
    camera: annotation.camera, range: annotation.range, annotations: [structuredClone(annotation)],
    attachments: [{artifact_id: target.artifact_id, role: cleanTargetRole(target)},
      ...(annotation.markup_artifact_id ? [{artifact_id: annotation.markup_artifact_id, role: "markup" as const}] : []),
      ...masks.map(artifact_id => ({artifact_id, role: "mask" as const}))]};
}
