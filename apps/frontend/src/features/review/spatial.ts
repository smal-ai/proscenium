import type { Camera, Markup, ProxyMapping, SpatialHit } from "../../api/generated/contracts";
import { blenderPointToClip, projectBlenderPoint } from "./media";

/** Blender world B(x, y, z) -> Proxy P(x, z, -y) in metres. */
export function blenderPointToProxy(point: readonly number[]): [number, number, number] {
  const result: [number, number, number] = [point[0], point[2], -point[1]];
  if (!result.every(Number.isFinite)) throw new Error("Non-finite Blender world coordinate");
  return result;
}

export function projectBlenderPointVisible(
  camera: Pick<Camera, "camera_to_world" | "projection">,
  point: readonly number[],
): { uv: [number, number]; visible: boolean } {
  const clip = blenderPointToClip(camera, point);
  if (!clip) return { uv: [0.5, 0.5], visible: false };
  const [x, y, z, w] = clip;
  if (!Number.isFinite(w) || w <= 1e-6) return { uv: [0.5, 0.5], visible: false };
  const ndcX = x / w;
  const ndcY = y / w;
  const ndcZ = z / w;
  const u = (ndcX + 1) / 2;
  const v = (1 - ndcY) / 2;
  const visible = Number.isFinite(u) && Number.isFinite(v) && Number.isFinite(ndcZ) && ndcZ >= -1 && ndcZ <= 1 && u >= 0 && u <= 1 && v >= 0 && v <= 1;
  return { uv: [u, v], visible };
}

export function pinMarkupLabel(noteText: string, fallback: string): string {
  const compact = noteText.trim().replace(/\s+/g, " ").slice(0, 72);
  return compact || fallback;
}

export function syncSpatialPinMarkup(markups: Markup[], camera: Camera, hit: SpatialHit, noteText: string): Markup[] {
  const uv = projectBlenderPoint(camera, hit.position);
  if (!Number.isFinite(uv[0]) || !Number.isFinite(uv[1])) return markups;
  const clamped: [number, number] = [Math.min(1, Math.max(0, uv[0])), Math.min(1, Math.max(0, uv[1]))];
  const text = pinMarkupLabel(noteText, hit.instance_id);
  const existingIndex = markups.findIndex(m => m.kind === "pin");
  if (existingIndex >= 0) {
    return markups.map((m, index) => index === existingIndex ? { ...m, points: [clamped], text } : m);
  }
  return [{ id: crypto.randomUUID(), kind: "pin", points: [clamped], width_px: 6, colour: "#ff5555", text }, ...markups];
}

export function verifySpatialMapping(
  mapping: ProxyMapping,
  hit: Pick<SpatialHit, "mapping_artifact_id" | "proxy_artifact_id" | "version_id" | "sample_frame" | "object_id" | "instance_id">,
  expectedDigest: string,
): string | null {
  if (mapping.mapping_artifact_id && mapping.mapping_artifact_id !== hit.mapping_artifact_id) {
    return `Mapping artefact mismatch (${mapping.mapping_artifact_id} vs ${hit.mapping_artifact_id})`;
  }
  if (mapping.proxy_artifact_id !== hit.proxy_artifact_id) {
    return `Proxy artefact mismatch (${mapping.proxy_artifact_id} vs ${hit.proxy_artifact_id})`;
  }
  if (mapping.version_id !== hit.version_id || mapping.sample_frame !== hit.sample_frame) {
    return "Pinned version or sample frame does not match proxy mapping";
  }
  if (mapping.source_closure_digest !== expectedDigest) {
    return "Pinned source closure digest does not match proxy mapping";
  }
  const matches = mapping.nodes.filter(n => n.object_id === hit.object_id && n.instance_id === hit.instance_id);
  if (matches.length !== 1) {
    return `Instance ${hit.object_id}:${hit.instance_id} is unavailable in proxy mapping`;
  }
  return null;
}

export function paintMarkups(ctx: CanvasRenderingContext2D, markups: Markup[], width: number, height: number, mask: boolean) {
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (const mark of markups) {
    if ((mark.kind === "mask") !== mask) continue;
    const points = mark.points.map(p => [p[0] * width, p[1] * height]);
    ctx.strokeStyle = ctx.fillStyle = mask ? "white" : mark.colour ?? "#ff5555";
    ctx.lineWidth = mark.width_px ?? 4;
    ctx.beginPath(); ctx.moveTo(points[0][0], points[0][1]);
    for (const p of points.slice(1)) ctx.lineTo(p[0], p[1]);
    if (mark.kind === "region") ctx.closePath();
    if (mark.kind === "pin") {
      ctx.arc(points[0][0], points[0][1], Math.max(5, ctx.lineWidth), 0, 2 * Math.PI);
      ctx.fill(); ctx.font = `${Math.max(14, width / 60)}px sans-serif`;
      ctx.fillText(mark.text ?? "", points[0][0] + 10, points[0][1]);
    } else {
      if (points.length === 1) { ctx.lineTo(points[0][0] + 0.01, points[0][1]); }
      ctx.stroke();
      if (mark.kind === "arrow" && points.length > 1) {
        const end = points.at(-1)!; const prev = points.at(-2)!;
        const angle = Math.atan2(end[1] - prev[1], end[0] - prev[0]);
        const size = Math.max(12, ctx.lineWidth * 4);
        ctx.beginPath(); ctx.moveTo(end[0] - size * Math.cos(angle - 0.5), end[1] - size * Math.sin(angle - 0.5));
        ctx.lineTo(end[0], end[1]); ctx.lineTo(end[0] - size * Math.cos(angle + 0.5), end[1] - size * Math.sin(angle + 0.5)); ctx.stroke();
      }
    }
  }
}

export const canvasPngBlob = (canvas: HTMLCanvasElement): Promise<Blob> =>
  new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("PNG export failed")), "image/png"));

export async function renderCompositeFromBlob(cleanBlob: Blob, markups: Markup[], width: number, height: number): Promise<Blob> {
  const bitmap = await createImageBitmap(cleanBlob);
  try {
    if (bitmap.width !== width || bitmap.height !== height) {
      throw new Error("Frozen clean capture dimensions do not match target record");
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D composite context is unavailable");
    ctx.drawImage(bitmap, 0, 0);
    paintMarkups(ctx, markups, width, height, false);
    return await canvasPngBlob(canvas);
  } finally {
    bitmap.close();
  }
}

export async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}
