export type Dimensions = { width: number; height: number };
type CanvasRect = Dimensions & { left: number; top: number };

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
