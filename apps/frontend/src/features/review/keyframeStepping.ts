import type { FrameAvailability, FrameRange, FrameReceipt, JobRecord } from "../../api/generated/contracts";

export function sortedReadyFrames(frames: readonly FrameReceipt[]): FrameReceipt[] {
  const byOrdinal = new Map<number, FrameReceipt>();
  for (const frame of frames) {
    if (!byOrdinal.has(frame.source_frame)) byOrdinal.set(frame.source_frame, frame);
  }
  return [...byOrdinal.values()].sort((a, b) => a.source_frame - b.source_frame);
}

export function stepKeyframePass(readyFrames: readonly FrameReceipt[], displayedFrame: number, delta: -1 | 1): FrameReceipt | null {
  let closest: FrameReceipt | null = null;
  for (const frame of readyFrames) {
    if ((frame.source_frame - displayedFrame) * delta > 0 && (!closest || (closest.source_frame - frame.source_frame) * delta > 0)) closest = frame;
  }
  return closest;
}

export function canStepKeyframePass(readyFrames: readonly FrameReceipt[], displayedFrame: number, delta: -1 | 1, storyboardCount: number): boolean {
  if (readyFrames.length > 0) return readyFrames.length > 1 && stepKeyframePass(readyFrames, displayedFrame, delta) !== null;
  const storyboardIndex = displayedFrame - 1;
  return storyboardCount > 1 && storyboardIndex + delta >= 0 && storyboardIndex + delta < storyboardCount;
}

const isRendering = (job: JobRecord | undefined) => Boolean(job && ["queued", "waiting_resource", "running"].includes(job.state));
const isTerminalJob = (job: JobRecord | undefined) => Boolean(job && ["succeeded", "failed", "cancelled"].includes(job.state));
const inIntervals = (n: number, ranges: readonly FrameRange[]) => ranges.some(r => r.start <= n && n <= r.end);

export function isFrameRendering(ordinal: number, job: JobRecord | undefined, availability: FrameAvailability | null): boolean {
  if (isTerminalJob(job)) return false;
  return availability ? inIntervals(ordinal, availability.pending_ranges)
    : Boolean(isRendering(job) && job?.render_spec && inIntervals(ordinal, [job.render_spec.frames]));
}

export function describeKeyframePassStatus({readyFrames, displayedFrame, job, availability}: {
  readyFrames: readonly FrameReceipt[]; displayedFrame: number; job: JobRecord | undefined; availability: FrameAvailability | null;
}): string | null {
  const count = readyFrames.length;
  if (count === 0) return null;
  const hasPendingRanges = !isTerminalJob(job) && ((availability?.pending_ranges.length ?? 0) > 0 || isRendering(job));
  if (count === 1) return hasPendingRanges ? "1 frame ready · rendering…" : "Only rendered frame";
  const idx = readyFrames.findIndex(frame => frame.source_frame === displayedFrame) + 1;
  const readiness = idx > 0 ? `${idx} of ${count} ready` : `${count} frames ready`;
  return hasPendingRanges ? `${readiness} · rendering…` : readiness;
}

export function resolveDirectKeyframeJump({requestedOrdinal, displayedFrame, readyFrames, job, availability}: {
  requestedOrdinal: number; displayedFrame: number; readyFrames: readonly FrameReceipt[]; job: JobRecord | undefined; availability: FrameAvailability | null;
}): { kind: "found"; frame: FrameReceipt } | { kind: "unavailable"; revertFrame: number; message: string } {
  const frame = readyFrames.find(frame => frame.source_frame === requestedOrdinal);
  if (frame) return {kind: "found", frame};
  const status = availability && inIntervals(requestedOrdinal, availability.failed_ranges) ? "failed to render"
    : isFrameRendering(requestedOrdinal, job, availability) ? "is rendering" : "is outside this pass";
  return {kind: "unavailable", revertFrame: displayedFrame, message: `Frame ${requestedOrdinal} ${status}. Showing frame ${displayedFrame}.`};
}
