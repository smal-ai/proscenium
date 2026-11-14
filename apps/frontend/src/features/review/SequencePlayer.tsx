import { useEffect, useRef, useState } from "react";
import type { Artifact, FrameMap } from "../../api/generated/contracts";
import { mediaUrl } from "./media";

/** Complete assembly playback. A composite never pretends to be one contributor. */
export function SequencePlayer({projectId, artifact, mapping, onCapture}: {
  projectId: string; artifact: Artifact; mapping: FrameMap;
  onCapture: (blob: Blob, frame: number, width: number, height: number) => Promise<void>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const capture = useRef<{canvas: HTMLCanvasElement; frame: number} | null>(null);
  const [presented, setPresented] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = video.current; if (!element) return;
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
  },[mapping]);

  async function annotate() {
    video.current?.pause(); const actual = capture.current; if (!actual || busy) return;
    setBusy(true);setError("");
    try {
      const blob = await new Promise<Blob>((resolve,reject) => actual.canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Frame capture failed")),"image/png"));
      await onCapture(blob,actual.frame,actual.canvas.width,actual.canvas.height);
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }

  return <section className="review-player" aria-label="Mapped sequence animation">
    <video ref={video} src={mediaUrl(projectId,artifact.artifact_id)} controls muted playsInline preload="auto" onError={() => setError(`media_unavailable: ${artifact.artifact_id}`)}/>
    <div className="review-toolbar"><span>Sequence frame {presented ?? "not presented"} · {mapping.fps.numerator}/{mapping.fps.denominator} fps · mapped contributors</span><button disabled={busy || presented === null} onClick={() => void annotate()}>{busy ? "Saving clean frame" : "Annotate presented sequence frame"}</button></div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
