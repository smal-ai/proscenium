import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
  const capture = useRef<{canvas: HTMLCanvasElement; frame: number} | null>(null);
  const [presented, setPresented] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useLayoutEffect(() => {
    if (!visible) { video.current?.pause(); capture.current = null; setPresented(null); }
  }, [visible]);
  useEffect(() => {
    const element = video.current; if (!visible || !element) return;
    const seek = () => {
      element.pause(); capture.current = null; setPresented(null);
      // Seek inside the frame interval. Rounded MP4 PTS can put its exact
      // mathematical left boundary a fraction before the intended sample.
      element.currentTime = (Math.min(mapping.frame_count, Math.max(1, requestedFrame)) - 0.5) * mapping.fps.denominator / mapping.fps.numerator;
    };
    if (element.readyState >= 1) seek();
    else element.addEventListener("loadedmetadata", seek, {once:true});
    return () => element.removeEventListener("loadedmetadata", seek);
  }, [requestedFrame, artifact.artifact_id, mapping.frame_count, mapping.fps.numerator, mapping.fps.denominator, visible]);
  useEffect(() => {
    const element = video.current; if (!visible || !element) return;
    if (!element.requestVideoFrameCallback) {setError("Presented-frame provenance is unavailable in this browser"); return;}
    let callback = 0; let disposed = false;
    const observe: VideoFrameRequestCallback = (_now, metadata) => {
      if (disposed) return;
      const frame = 1 + Math.floor(metadata.mediaTime * mapping.fps.numerator / mapping.fps.denominator + 1e-4);
      if (mapping.frames.some(row => row.sequence_frame === frame)) {
        const canvas = document.createElement("canvas"); canvas.width=element.videoWidth;canvas.height=element.videoHeight;
        canvas.getContext("2d")!.drawImage(element,0,0);
        capture.current = {canvas,frame}; setPresented(frame);
      }
      callback = element.requestVideoFrameCallback(observe);
    };
    callback = element.requestVideoFrameCallback(observe);
    return () => {disposed=true;element.cancelVideoFrameCallback(callback);};
  },[mapping, visible]);

  async function annotate() {
    video.current?.pause(); const actual = capture.current; if (!visible || !actual || busy) return;
    setBusy(true);setError("");
    try {
      const blob = await new Promise<Blob>((resolve,reject) => actual.canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Frame capture failed")),"image/png"));
      await onCapture(blob,actual.frame,actual.canvas.width,actual.canvas.height);
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }

  return <section className="review-player" aria-label="Mapped sequence animation">
    <video ref={video} src={mediaUrl(projectId,artifact.artifact_id)} controls playsInline preload="auto" onError={() => setError(`media_unavailable: ${artifact.artifact_id}`)}/>
    <div className="review-bar review-player-overlay-bar"><span className="toolbar-meta">Sequence frame {presented ?? "—"} · {mapping.fps.numerator}/{mapping.fps.denominator} fps · mapped contributors</span><div className="toolbar-spacer"/><button className="toolbar-btn toolbar-btn-accent" disabled={!visible || busy || presented === null} onClick={() => void annotate()}><PenLine size={14}/>{busy ? "Saving clean frame…" : "Annotate presented sequence frame"}</button></div>
    {error && <div className="review-status-banner" role="alert">{error}</div>}
  </section>;
}
