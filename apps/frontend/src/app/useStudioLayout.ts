import { useEffect, useState } from "react";
import type { ProjectSnapshot } from "../api";
import { readPrivate, writePrivate } from "./storage";

export const NAVIGATOR_MIN_WIDTH = 220;
export const NAVIGATOR_MAX_WIDTH = 520;
export const ASSISTANT_MIN_WIDTH = 320;
export const ASSISTANT_MAX_WIDTH = 760;

export interface StudioLayout {
  mode: "split" | "chat_focus";
  navigatorCollapsed: boolean;
  assistantCollapsed: boolean;
  navigatorWidth: number;
  assistantWidth: number;
}
export const defaultStudioLayout: StudioLayout = { mode: "split", navigatorCollapsed: false, assistantCollapsed: false, navigatorWidth: NAVIGATOR_MAX_WIDTH, assistantWidth: ASSISTANT_MAX_WIDTH };
export const clampWidth = (width: number, min: number, max: number) => Math.max(min, Math.min(max, width));
function savedLayout(projectId: string | null): StudioLayout | null {
  const saved = projectId ? readPrivate<StudioLayout>(`layout.v1.${projectId}`) : null;
  if (!saved || !["split", "chat_focus"].includes(saved.mode) || typeof saved.navigatorCollapsed !== "boolean" || typeof saved.assistantCollapsed !== "boolean" || !Number.isFinite(saved.navigatorWidth) || !Number.isFinite(saved.assistantWidth)) return null;
  return { ...saved, navigatorWidth: clampWidth(saved.navigatorWidth, NAVIGATOR_MIN_WIDTH, NAVIGATOR_MAX_WIDTH), assistantWidth: clampWidth(saved.assistantWidth, ASSISTANT_MIN_WIDTH, ASSISTANT_MAX_WIDTH) };
}

/** Split widths are preferences, not transient available-space measurements. */
export function useStudioLayout(projectId: string | null, snapshot: ProjectSnapshot | null, requestCount: number, itemCount: number, jobCount: number, hydrated = snapshot !== null) {
  const [initial] = useState(() => savedLayout(projectId));
  const [layout, setLayout] = useState<StudioLayout>(initial ?? defaultStudioLayout);
  const [settled, setSettled] = useState(!!initial);
  useEffect(() => {
    if (!projectId || !snapshot || !hydrated || settled) return;
    const created = readPrivate(`created.${projectId}`) === true;
    const empty = !snapshot.project.native_session_id && requestCount === 0 && itemCount === 0 && jobCount === 0;
    setLayout(created || empty ? { ...defaultStudioLayout, mode: "chat_focus", navigatorCollapsed: true } : defaultStudioLayout);
    setSettled(true);
  }, [projectId, snapshot, hydrated, requestCount, itemCount, jobCount, settled]);
  useEffect(() => {
    if (!projectId || !settled) return;
    writePrivate(`layout.v1.${projectId}`, layout);
    writePrivate(`created.${projectId}`, null);
  }, [projectId, settled, layout]);
  return { layout, setLayout, settled };
}
