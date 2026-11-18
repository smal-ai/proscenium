import { PerspectiveCamera, type OrthographicCamera } from "three";

type Dimensions = { width: number; height: number };

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
  // Match the native capture's aspect correction without touching the live camera.
  if (base instanceof PerspectiveCamera) base.aspect = viewportAspect;
  else {
    const centre = (base.left + base.right) / 2;
    const halfWidth = (base.top - base.bottom) * viewportAspect / 2;
    base.left = centre - halfWidth;
    base.right = centre + halfWidth;
  }
  base.updateProjectionMatrix();
  base.updateMatrixWorld();

  const scaleX = gate.width / viewportWidth;
  const scaleY = gate.height / viewportHeight;
  const targetAspect = targetDimensions.width / targetDimensions.height;
  const { width, height } = targetDimensions;
  const projection = base.projectionMatrix.elements;
  const view = base.view;
  const subviewWidth = view?.enabled ? width * ((view.width / view.fullWidth) / (view.height / view.fullHeight)) : width;

  let framed: PerspectiveCamera | OrthographicCamera;
  if (base instanceof PerspectiveCamera) {
    framed = base.clone();
    // Fold any sub-view's vertical crop into FOV so the frozen optical parameters
    // reproduce the matrix in Blender. Zoom remains on the cloned camera.
    const tanHalfV = scaleY * (view?.enabled ? base.zoom / projection[5] : Math.tan((base.fov * Math.PI) / 360));
    framed.fov = (Math.atan(tanHalfV) * 360) / Math.PI;
    framed.aspect = targetAspect;
    const shiftX = projection[8] / scaleX;
    const shiftY = projection[9] / scaleY;
    framed.filmOffset = 0;
    if (shiftX || shiftY || subviewWidth !== width) {
      framed.setViewOffset(width, height, (width - subviewWidth + shiftX * subviewWidth) / 2, -shiftY * height / 2, subviewWidth, height);
    } else framed.clearViewOffset();
  } else {
    framed = base.clone();
    const vCentre = view?.enabled ? -projection[13] / projection[5] : (base.top + base.bottom) / 2;
    const hCentre = view?.enabled ? -projection[12] / projection[0] : (base.left + base.right) / 2;
    const halfH = scaleY * (view?.enabled ? base.zoom / projection[5] : (base.top - base.bottom) / 2);
    const halfW = halfH * targetAspect;
    framed.top = vCentre + halfH;
    framed.bottom = vCentre - halfH;
    framed.left = hCentre - halfW;
    framed.right = hCentre + halfW;
    if (subviewWidth !== width) {
      framed.setViewOffset(width, height, (width - subviewWidth) / 2, 0, subviewWidth, height);
    } else framed.clearViewOffset();
  }
  framed.updateProjectionMatrix();
  framed.updateMatrixWorld();
  return framed;
}
