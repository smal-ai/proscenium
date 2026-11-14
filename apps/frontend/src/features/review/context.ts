import type { AnnotationSnapshot } from "../../api/generated/contracts";
import { emptySelection, type TurnSelection } from "../../api/studio";

export function annotationContext(annotation: AnnotationSnapshot): TurnSelection {
  const target = annotation.target;
  const masks = [...new Set(annotation.markups.filter(m => m.kind === "mask").map(m => m.mask_artifact_id!))];
  return {...emptySelection(), item_id: target.item_id, version_id: target.version_id,
    view_mode: target.media_artifact_id ? "animation" : "keyframes", target: structuredClone(target),
    camera: annotation.camera, range: annotation.range, annotations: [structuredClone(annotation)],
    attachments: [{artifact_id: target.artifact_id, role: target.origin === "concept" ? "concept" : target.origin === "reference" ? "reference" : "render"},
      ...(annotation.markup_artifact_id ? [{artifact_id: annotation.markup_artifact_id, role: "markup" as const}] : []),
      ...masks.map(artifact_id => ({artifact_id, role: "mask" as const}))]};
}
