import type { Camera, FrameMap, FrameReceipt, FrameTarget, PreviewSegment, VersionRecord } from "../../api/generated/contracts";

export const mediaUrl = (project: string, artifact: string) => `/api/v1/projects/${encodeURIComponent(project)}/artifacts/${encodeURIComponent(artifact)}/content`;

export const stableJson = (value: unknown) => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a],[b]) => a.localeCompare(b))) : item);

export function imagePoint(x: number, y: number, rect: {left: number; top: number; width: number; height: number}): [number, number] | null {
  if (!rect.width || !rect.height || x < rect.left || y < rect.top || x > rect.left + rect.width || y > rect.top + rect.height) return null;
  return [(x - rect.left) / rect.width, (y - rect.top) / rect.height];
}

export function contentRect(box: {left: number; top: number; width: number; height: number}, width: number, height: number) {
  const scale = Math.min(box.width / width, box.height / height);
  return {left: box.left + (box.width - width * scale) / 2, top: box.top + (box.height - height * scale) / 2, width: width * scale, height: height * scale};
}

type CameraMatrices = Pick<Camera, "camera_to_world" | "projection">;

/** Invert a row-major 4×4 via its adjugate. Preserve Three.js Matrix4's operation order. */
function invertMatrix4(matrix: readonly number[]): number[] | null {
  const [n11, n12, n13, n14, n21, n22, n23, n24, n31, n32, n33, n34, n41, n42, n43, n44] = matrix;
  const t1 = n11 * n22 - n21 * n12, t2 = n11 * n32 - n31 * n12, t3 = n11 * n42 - n41 * n12;
  const t4 = n21 * n32 - n31 * n22, t5 = n21 * n42 - n41 * n22, t6 = n31 * n42 - n41 * n32;
  const t7 = n13 * n24 - n23 * n14, t8 = n13 * n34 - n33 * n14, t9 = n13 * n44 - n43 * n14;
  const t10 = n23 * n34 - n33 * n24, t11 = n23 * n44 - n43 * n24, t12 = n33 * n44 - n43 * n34;
  const det = t1 * t12 - t2 * t11 + t3 * t10 + t4 * t9 - t5 * t8 + t6 * t7;
  if (det === 0 || !Number.isFinite(det)) return null;
  const inv = 1 / det;
  return [
    (n22 * t12 - n32 * t11 + n42 * t10) * inv,
    (n32 * t9 - n12 * t12 - n42 * t8) * inv,
    (n12 * t11 - n22 * t9 + n42 * t7) * inv,
    (n22 * t8 - n12 * t10 - n32 * t7) * inv,
    (n31 * t11 - n21 * t12 - n41 * t10) * inv,
    (n11 * t12 - n31 * t9 + n41 * t8) * inv,
    (n21 * t9 - n11 * t11 - n41 * t7) * inv,
    (n11 * t10 - n21 * t8 + n31 * t7) * inv,
    (n24 * t6 - n34 * t5 + n44 * t4) * inv,
    (n34 * t3 - n14 * t6 - n44 * t2) * inv,
    (n14 * t5 - n24 * t3 + n44 * t1) * inv,
    (n24 * t2 - n14 * t4 - n34 * t1) * inv,
    (n33 * t5 - n23 * t6 - n43 * t4) * inv,
    (n13 * t6 - n33 * t3 + n43 * t2) * inv,
    (n23 * t3 - n13 * t5 - n43 * t1) * inv,
    (n13 * t4 - n23 * t2 + n33 * t1) * inv,
  ];
}

/** Return homogeneous clip coordinates from the row-major wire matrices. */
export function blenderPointToClip(camera: CameraMatrices, point: readonly number[]): [number, number, number, number] | null {
  const view = invertMatrix4(camera.camera_to_world);
  if (!view) return null;
  const [x, y, z] = point;
  const p = camera.projection;
  const row = (i: number) => {
    const column = (j: number) => p[i] * view[j] + p[i + 1] * view[j + 4] + p[i + 2] * view[j + 8] + p[i + 3] * view[j + 12];
    return column(0) * x + column(1) * y + column(2) * z + column(3);
  };
  return [row(0), row(4), row(8), row(12)];
}

export function projectBlenderPoint(camera: CameraMatrices, point: readonly number[]): [number, number] {
  const clip = blenderPointToClip(camera, point);
  // A singular Matrix4 inverse is zero in Three.js, giving 0 * Infinity.
  if (!clip) return [NaN, NaN];
  // Match Vector3.applyMatrix4: multiply by the reciprocal rather than divide.
  const w = 1 / clip[3];
  return [(clip[0] * w + 1) / 2, (1 - clip[1] * w) / 2];
}

export function frameTarget(frame: FrameReceipt, version: VersionRecord, segment?: PreviewSegment): FrameTarget {
  return {origin: "blender_render", artifact_id: frame.artifact_id, displayed_sha256: frame.sha256,
    dimensions: frame.dimensions, item_id: version.item_id, version_id: version.version_id,
    shot_id: frame.shot_id, render_job_id: frame.job_id, source_frame: frame.source_frame,
    media_artifact_id: segment?.artifact_id ?? null};
}

export function sequenceTarget(frame: number, artifact: FrameTarget, map: FrameMap, mapArtifactId: string): FrameTarget {
  if (!map.frames.some(row => row.sequence_frame === frame)) throw new Error("Sequence frame is unavailable");
  return {...artifact, origin: "sequence_render", version_id: map.sequence_version_id,
    sequence_version_id: map.sequence_version_id, sequence_frame: frame, frame_map_artifact_id: mapArtifactId,
    source_frame: null, shot_id: null, render_job_id: null};
}

export function presentedFrame(mediaTime: number, segment: PreviewSegment): number | null {
  // rVFC timestamps are quantised to microseconds. Tolerance is 0.0001 frame,
  // not a currentTime guess or a rounding policy for sequence retiming.
  const offset = Math.floor(mediaTime * segment.fps.numerator / segment.fps.denominator + 1e-4);
  if (offset < 0 || offset >= segment.frame_count) return null;
  return segment.source_frames.start + offset;
}

export const frameCentre = (frame: number, segment: PreviewSegment) =>
  (frame - segment.source_frames.start + 0.5) * segment.fps.denominator / segment.fps.numerator;

export function segmentAt(segments: PreviewSegment[], frame: number) {
  return segments.find(s => s.source_frames.start <= frame && s.source_frames.end >= frame);
}

export function typingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || !!target.closest("input,textarea,select,[role=textbox]"));
}
