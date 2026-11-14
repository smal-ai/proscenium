import { Matrix4, OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
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

/** World basis only. Camera-local optical axes do not undergo an object conjugation. */
export function captureCamera(camera: PerspectiveCamera | OrthographicCamera): Camera {
  camera.updateMatrixWorld();
  const proxyToBlender = new Matrix4().set(1,0,0,0, 0,0,-1,0, 0,1,0,0, 0,0,0,1);
  const world = proxyToBlender.multiply(camera.matrixWorld);
  const rowMajor = (m: Matrix4) => m.clone().transpose().toArray();
  const projection = rowMajor(camera.projectionMatrix);
  const common = {space: "blender_world_m" as const, camera_to_world: rowMajor(world), projection,
    aspect: camera instanceof PerspectiveCamera ? camera.aspect : (camera.right-camera.left)/(camera.top-camera.bottom),
    near_m: camera.near, far_m: camera.far, source_camera_id: null,
    shift: (camera instanceof PerspectiveCamera ? [-projection[2] / 2, projection[6] / 2] : [projection[3] / 2, -projection[7] / 2]) as [number, number]};
  return camera instanceof PerspectiveCamera
    ? {...common, kind: "perspective", vertical_fov_degrees: camera.getEffectiveFOV()}
    : {...common, kind: "orthographic", orthographic_height_m: (camera.top-camera.bottom)/camera.zoom};
}

export function projectBlenderPoint(camera: Pick<Camera,"camera_to_world" | "projection">, point: number[]): [number, number] {
  const view = new Matrix4().fromArray(camera.camera_to_world).transpose().invert();
  const projection = new Matrix4().fromArray(camera.projection).transpose();
  const clip = new Vector3(point[0],point[1],point[2]).applyMatrix4(projection.multiply(view));
  return [(clip.x+1)/2,(1-clip.y)/2];
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
