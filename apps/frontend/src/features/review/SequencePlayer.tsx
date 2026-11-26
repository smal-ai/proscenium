import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { PenLine } from "lucide-react";
import type { Artifact, FrameMap } from "../../api/generated/contracts";
import { mediaUrl } from "./media";

/** Complete assembly playback. A composite never pretends to be one contributor. */
export function SequencePlayer({visible = true, projectId, artifact, mapping, requestedFrame = 1, onCapture}: {
  visible?: boolean;
  projectId: string; artifact: Artifact; mapping: FrameMap;
  requestedFrame?: number;
  onCapture: (blob: Blob, frame: number, width: number, height: number) => Promise<void>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const bufferCanvas = useRef<HTMLCanvasElement | null>(null);
  const bufferContext = useRef<CanvasRenderingContext2D | null>(null);
  const presentedRef = useRef<number | null>(null);
  const capturing = useRef(false);
  const validFrames = useMemo(() => new Set(mapping.frames.map(row => row.sequence_frame)), [mapping.frames]);
  const {numerator, denominator} = mapping.fps;
  const [presented, setPresented] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useLayoutEffect(() => {
    if (!visible) { video.current?.pause(); presentedRef.current = null; setPresented(null); }
    return () => {
      const canvas = bufferCanvas.current;
      if (canvas) { canvas.width = 0; canvas.height = 0; }
      bufferCanvas.current = null;
      bufferContext.current = null;
    };
  }, [visible]);
  useLayoutEffect(() => {
    const element = video.current; if (!visible || !element) return;
    presentedRef.current = null; setPresented(null);
    const seek = () => {
      element.pause();
      // Seek inside the frame interval. Rounded MP4 PTS can put its exact
      // mathematical left boundary a fraction before the intended sample.
      element.currentTime = (Math.min(mapping.frame_count, Math.max(1, requestedFrame)) - 0.5) * denominator / numerator;
    };
    if (element.readyState >= 1) seek();
    else element.addEventListener("loadedmetadata", seek, {once:true});
    return () => element.removeEventListener("loadedmetadata", seek);
  }, [requestedFrame, artifact.artifact_id, projectId, mapping.frame_count, numerator, denominator, visible]);
  useLayoutEffect(() => {
    const element = video.current; if (!visible || !element) return;
    if (!element.requestVideoFrameCallback) {setError("Presented-frame provenance is unavailable in this browser"); return;}
    if (presentedRef.current !== null && !validFrames.has(presentedRef.current)) {
      presentedRef.current = null; setPresented(null);
    }
    let callback = 0; let disposed = false;
    const observe: VideoFrameRequestCallback = (_now, metadata) => {
      if (disposed) return;
      const frame = 1 + Math.floor(metadata.mediaTime * numerator / denominator + 1e-4);
      const {videoWidth, videoHeight} = element;
      if (videoWidth && videoHeight && validFrames.has(frame)) {
        const canvas = bufferCanvas.current ??= document.createElement("canvas");
        if (canvas.width !== videoWidth) canvas.width = videoWidth;
        if (canvas.height !== videoHeight) canvas.height = videoHeight;
        const context = bufferContext.current ??= canvas.getContext("2d");
        if (context) {
          context.drawImage(element, 0, 0);
          if (presentedRef.current !== frame) { presentedRef.current = frame; setPresented(frame); }
        }
      }
      callback = element.requestVideoFrameCallback(observe);
    };
    callback = element.requestVideoFrameCallback(observe);
    return () => {disposed=true;element.cancelVideoFrameCallback(callback);};
  }, [artifact.artifact_id, projectId, numerator, denominator, validFrames, visible]);

  async function annotate() {
    video.current?.pause();
    const buffer = bufferCanvas.current;
    const frame = presentedRef.current;
    if (!visible || !buffer || frame === null || capturing.current) return;
    capturing.current = true;
    setBusy(true);setError("");
    try {
      // Keep the exact presented pixels even if playback resumes or the
      // reusable buffer is released while PNG encoding is pending.
      const snapshot = document.createElement("canvas");
      const {width, height} = buffer;
      snapshot.width = width; snapshot.height = height;
      const context = snapshot.getContext("2d");
      if (!context) throw new Error("Frame capture failed");
      context.drawImage(buffer, 0, 0);
      const blob = await new Promise<Blob>((resolve, reject) => snapshot.toBlob(blob => {
        snapshot.width = 0; snapshot.height = 0;
        if (blob) resolve(blob);
        else reject(new Error("Frame capture failed"));
      }, "image/png"));
      await onCapture(blob, frame, width, height);
    } catch (e) {setError(String(e));} finally {capturing.current = false; setBusy(false);}
  }

  return <section className="review-player" aria-label="Mapped sequence animation">
    <video ref={video} src={mediaUrl(projectId,artifact.artifact_id)} controls playsInline preload="auto" onError={() => setError(`media_unavailable: ${artifact.artifact_id}`)}/>
    <div className="review-bar review-player-overlay-bar"><span className="toolbar-meta">Sequence frame {presented ?? "—"} · {mapping.fps.numerator}/{mapping.fps.denominator} fps · mapped contributors</span><div className="toolbar-spacer"/><button className="toolbar-btn toolbar-btn-accent" disabled={!visible || busy || presented === null} onClick={() => void annotate()}><PenLine size={14}/>{busy ? "Saving clean frame…" : "Annotate presented sequence frame"}</button></div>
    {error && <div className="review-status-banner" role="alert">{error}</div>}
  </section>;
}
