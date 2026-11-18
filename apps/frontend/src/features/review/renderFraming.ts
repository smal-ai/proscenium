import { PerspectiveCamera, type OrthographicCamera } from "three";
import { captureCamera } from "./media";

type Dimensions = { width: number; height: number };
type CanvasRect = Dimensions & { left: number; top: number };

const PROJECTION_TOLERANCE = 1e-5;

export type RenderPresetId = "16:9" | "16:10" | "viewport";

export interface RenderPresetDefinition {
  id: RenderPresetId;
  ratioLabel: string;
  dimensions: Dimensions | null;
}

export const RENDER_PRESETS: readonly RenderPresetDefinition[] = [
  { id: "16:9", ratioLabel: "16:9", dimensions: { width: 1920, height: 1080 } },
  { id: "16:10", ratioLabel: "16:10", dimensions: { width: 1920, height: 1200 } },
  { id: "viewport", ratioLabel: "Match viewport", dimensions: null },
];

export function formatRenderPresetOption(presetId: RenderPresetId, viewportPixels?: Dimensions | null): string {
  const { ratioLabel, dimensions } = RENDER_PRESETS.find(preset => preset.id === presetId)!;
  const pixels = dimensions ?? viewportPixels;
  return pixels && pixels.width > 0 && pixels.height > 0
    ? `${ratioLabel} · ${pixels.width} × ${pixels.height}`
    : ratioLabel;
}

export function resolveRenderTargetDimensions(presetId: RenderPresetId, viewportPixels?: Dimensions | null): Dimensions | null {
  const dimensions = RENDER_PRESETS.find(preset => preset.id === presetId)!.dimensions;
  return dimensions ? { ...dimensions } : viewportPixels ?? null;
}

export function computeFramingGate(viewportWidth: number, viewportHeight: number, targetDimensions: Dimensions | null) {
  if (!targetDimensions || viewportWidth <= 0 || viewportHeight <= 0 || targetDimensions.width <= 0 || targetDimensions.height <= 0) {
    return { active: false, left: 0, top: 0, width: Math.max(0, viewportWidth), height: Math.max(0, viewportHeight) };
  }
  const targetAspect = targetDimensions.width / targetDimensions.height;
  const pillarbox = viewportWidth / viewportHeight >= targetAspect;
  const width = pillarbox ? viewportHeight * targetAspect : viewportWidth;
  const height = pillarbox ? viewportHeight : viewportWidth / targetAspect;
  return { active: true, left: (viewportWidth - width) / 2, top: (viewportHeight - height) / 2, width, height };
}

/** Express the capture's canvas-local crop in overlay host coordinates. */
export function computeCanvasFramingGate(canvasRect: CanvasRect, targetDimensions: Dimensions | null) {
  const gate = computeFramingGate(canvasRect.width, canvasRect.height, targetDimensions);
  return { ...gate, left: canvasRect.left + gate.left, top: canvasRect.top + gate.top };
}

function hasSquarePixels(projection: readonly number[], aspect: number) {
  return Math.abs(projection[0] * aspect - projection[5]) <= PROJECTION_TOLERANCE;
}

export function deriveFramedCamera(
  liveCamera: PerspectiveCamera | OrthographicCamera,
  viewportWidth: number,
  viewportHeight: number,
  targetDimensions: Dimensions,
): PerspectiveCamera | OrthographicCamera {
  const gate = computeFramingGate(viewportWidth, viewportHeight, targetDimensions);
  if (!gate.active) throw new RangeError("Framing requires positive viewport and target dimensions");

  const viewportAspect = viewportWidth / viewportHeight;
  const base = liveCamera.clone();
  base.updateProjectionMatrix();
  // Keep a sub-view whose original optics already match the canvas, including
  // unequal crop fractions shown at the sub-view's own aspect ratio.
  if (!base.view?.enabled || !hasSquarePixels(base.projectionMatrix.elements, viewportAspect)) {
    // Otherwise match the native capture's aspect correction on the clone.
    if (base instanceof PerspectiveCamera) base.aspect = viewportAspect;
    else {
      const centre = (base.left + base.right) / 2;
      const halfWidth = (base.top - base.bottom) * viewportAspect / 2;
      base.left = centre - halfWidth;
      base.right = centre + halfWidth;
    }
    base.updateProjectionMatrix();
  }
  const projection = base.projectionMatrix.elements;
  if (!hasSquarePixels(projection, viewportAspect)) {
    throw new RangeError("Anisotropic camera sub-view cannot be reproduced with square-pixel Blender optics");
  }
  base.updateMatrixWorld();

  const scaleX = gate.width / viewportWidth;
  const scaleY = gate.height / viewportHeight;
  const targetAspect = targetDimensions.width / targetDimensions.height;
  const { width, height } = targetDimensions;

  let framed: PerspectiveCamera | OrthographicCamera;
  if (base instanceof PerspectiveCamera) {
    framed = base.clone();
    // Fold magnification into FOV and keep the view offset for shift only.
    const tanHalfV = scaleY * base.zoom / projection[5];
    framed.fov = (Math.atan(tanHalfV) * 360) / Math.PI;
    framed.aspect = targetAspect;
    const shiftX = projection[8] / scaleX;
    const shiftY = projection[9] / scaleY;
    framed.filmOffset = 0;
    if (shiftX || shiftY) {
      framed.setViewOffset(width, height, shiftX * width / 2, -shiftY * height / 2, width, height);
    } else framed.clearViewOffset();
  } else {
    framed = base.clone();
    const vCentre = -projection[13] / projection[5];
    const hCentre = -projection[12] / projection[0];
    const halfH = scaleY * base.zoom / projection[5];
    const halfW = halfH * targetAspect;
    framed.top = vCentre + halfH;
    framed.bottom = vCentre - halfH;
    framed.left = hCentre - halfW;
    framed.right = hCentre + halfW;
    framed.clearViewOffset();
  }
  framed.updateProjectionMatrix();
  // Blender sets vertical optics from the wire record and solves only the
  // shift terms. Its vertical and horizontal scales must already match.
  const record = captureCamera(framed);
  const verticalScale = record.kind === "perspective"
    ? 1 / Math.tan(record.vertical_fov_degrees! * Math.PI / 360)
    : 2 / record.orthographic_height_m!;
  if (!(Math.abs(record.projection[0] - record.projection[5] / targetAspect) <= PROJECTION_TOLERANCE &&
    Math.abs(record.projection[0] - verticalScale / targetAspect) <= PROJECTION_TOLERANCE &&
    Math.abs(record.projection[5] - verticalScale) <= PROJECTION_TOLERANCE &&
    Math.abs(record.aspect - targetAspect) <= 1e-6)) {
    throw new RangeError("Framed camera projection cannot be reproduced with square-pixel Blender optics");
  }
  return framed;
}
