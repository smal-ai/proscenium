import { useEffect, useState } from "react";
import type { ProjectSnapshot } from "../api";
import { readPrivate, writePrivate } from "./storage";

export interface StudioLayout {
  mode: "split" | "chat_focus";
  navigatorCollapsed: boolean;
  assistantCollapsed: boolean;
  navigatorWidth: number;
  assistantWidth: number;
}
const defaults: StudioLayout = { mode: "split", navigatorCollapsed: false, assistantCollapsed: false, navigatorWidth: 280, assistantWidth: 400 };
export const clampWidth = (width: number, min: number, max: number) => Math.max(min, Math.min(max, width));
function savedLayout(projectId: string | null): StudioLayout | null {
  const saved = projectId ? readPrivate<StudioLayout>(`layout.v1.${projectId}`) : null;
  if (!saved || !["split", "chat_focus"].includes(saved.mode) || typeof saved.navigatorCollapsed !== "boolean" || typeof saved.assistantCollapsed !== "boolean" || !Number.isFinite(saved.navigatorWidth) || !Number.isFinite(saved.assistantWidth)) return null;
  return { ...saved, navigatorWidth: clampWidth(saved.navigatorWidth, 220, 520), assistantWidth: clampWidth(saved.assistantWidth, 320, 760) };
}

/** Split widths are preferences, not transient available-space measurements. */
export function useStudioLayout(projectId: string | null, snapshot: ProjectSnapshot | null, requestCount: number, itemCount: number, jobCount: number) {
  const [initial] = useState(() => savedLayout(projectId));
  const [layout, setLayout] = useState<StudioLayout>(initial ?? defaults);
  const [settled, setSettled] = useState(!!initial);
  useEffect(() => {
    if (!projectId || !snapshot || settled) return;
    const created = readPrivate(`created.${projectId}`) === true;
    const empty = !snapshot.project.native_session_id && requestCount === 0 && itemCount === 0 && jobCount === 0;
    setLayout(created || empty ? { ...defaults, mode: "chat_focus", navigatorCollapsed: true } : defaults);
    setSettled(true);
  }, [projectId, snapshot, requestCount, itemCount, jobCount, settled]);
  useEffect(() => {
    if (!projectId || !settled) return;
    writePrivate(`layout.v1.${projectId}`, layout);
    writePrivate(`created.${projectId}`, null);
  }, [projectId, settled, layout]);
  return { layout, setLayout, settled };
}
