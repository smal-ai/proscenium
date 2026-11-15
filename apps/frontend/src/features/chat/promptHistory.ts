import type { RequestRecord } from "../../api";

export function promptHistory(projectId: string, requests: RequestRecord[]): string[] {
  const records = new Map<string, RequestRecord>();
  for (const record of requests) {
    if (record.project_id !== projectId || record.submission.type === "stop") continue;
    const previous = records.get(record.request_id);
    if (!previous || record.receipt.state_revision > previous.receipt.state_revision) records.set(record.request_id, record);
  }
  return [...records.values()].sort((a, b) => Date.parse(a.accepted_at) - Date.parse(b.accepted_at) || a.request_id.localeCompare(b.request_id))
    .flatMap(record => record.submission.type === "stop" ? [] : [record.submission.text]);
}

export interface HistoryCursor { index: number; saved: string; recalled: string }
export function canNavigateHistory(event: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean; isComposing: boolean }, draft: string, start: number, end: number, cursor: HistoryCursor | null): boolean {
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing || !["ArrowUp", "ArrowDown"].includes(event.key)) return false;
  return !!cursor && cursor.recalled === draft || event.key === "ArrowUp" && (draft === "" || start === 0 && end === 0);
}
export function stepHistory(history: string[], cursor: HistoryCursor | null, draft: string, direction: "older" | "newer"): { cursor: HistoryCursor | null; text: string } {
  if (!history.length || (!cursor && direction === "newer")) return { cursor, text: draft };
  const index = cursor ? cursor.index + (direction === "older" ? -1 : 1) : history.length - 1;
  if (index >= history.length) return { cursor: null, text: cursor?.saved ?? draft };
  const bounded = Math.max(0, index);
  return { cursor: { index: bounded, saved: cursor?.saved ?? draft, recalled: history[bounded] }, text: history[bounded] };
}
