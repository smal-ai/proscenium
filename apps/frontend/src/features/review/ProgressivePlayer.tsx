import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FrameAvailability, FrameReceipt, FrameTarget, PreviewSegment, VersionRecord } from "../../api/generated/contracts";
import { frameCentre, frameTarget, mediaUrl, presentedFrame, segmentAt } from "./media";

/** Two independently decoded elements, at most current + immediate next interval.
 * Identity is admitted from rVFC mediaTime, never video.currentTime or a requested seek.
 */
export function ProgressivePlayer({visible = true, projectId, version, frames, segments, availability, requestedFrame, playing, onPlaying, onPresented}: {
  visible?: boolean;
  projectId: string; version: VersionRecord; frames: FrameReceipt[]; segments: PreviewSegment[];
  availability: FrameAvailability | null; requestedFrame: number; playing: boolean;
  onPlaying: (playing: boolean) => void; onPresented: (target: FrameTarget) => void;
}) {
  const [activeFrame, setActiveFrame] = useState(requestedFrame);
  const [message, setMessage] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const nextVideo = useRef<HTMLVideoElement>(null);
  const wantsPlay = useRef(playing); wantsPlay.current = visible && playing;
  const presented = useRef<FrameTarget | null>(null);
  const confirmedSegment = useRef("");
  const awaitingFirstPresentation = useRef(true);
  const onPresentedRef = useRef(onPresented); onPresentedRef.current = onPresented;
  const previousRequest = useRef(requestedFrame);
  const segment = segmentAt(segments, activeFrame);
  const next = segment ? segmentAt(segments, segment.source_frames.end + 1) : undefined;
  useLayoutEffect(() => {
    if (!visible) { video.current?.pause(); nextVideo.current?.pause(); confirmedSegment.current = ""; awaitingFirstPresentation.current = true; }
  }, [visible]);

  useEffect(() => {
    if (previousRequest.current !== requestedFrame) {
      previousRequest.current = requestedFrame; setActiveFrame(requestedFrame);
      const current = video.current; const selected = segmentAt(segments, requestedFrame);
      if (current && selected && selected.segment_id === segment?.segment_id) {
        current.pause(); current.currentTime = frameCentre(requestedFrame, selected);
      }
    }
  }, [requestedFrame, segment, segments]);

  useEffect(() => {
    const element = video.current;
    if (!visible || !element || !segment) return;
    if (!element.requestVideoFrameCallback) { setMessage("Frame provenance unavailable in this browser. Use receipted keyframes for annotation."); return; }
    let callback = 0; let disposed = false;
    const observe: VideoFrameRequestCallback = (_now, metadata) => {
      if (disposed) return;
      const frame = presentedFrame(metadata.mediaTime, segment);
      const receipt = frames.find(f => f.job_id === segment.job_id && f.version_id === segment.version_id && f.shot_id === segment.shot_id && f.source_frame === frame);
      if (receipt) {
        const target = frameTarget(receipt, version, segment);
        presented.current = target; onPresentedRef.current(target); setMessage("");
        confirmedSegment.current = segment.segment_id;
        if (awaitingFirstPresentation.current) {
          awaitingFirstPresentation.current = false;
          if (wantsPlay.current) void element.play().catch(e => setMessage(String(e)));
        }
      } else setMessage(`Presented frame ${frame ?? "unknown"} has no verified frame receipt`);
      callback = element.requestVideoFrameCallback(observe);
    };
    callback = element.requestVideoFrameCallback(observe);
    // React reuses the already-decoded prebuffer element at this boundary. Its
    // loadeddata event happened while hidden, so explicitly request a presentation.
    if (element.readyState >= 2 && confirmedSegment.current !== segment.segment_id) {
      awaitingFirstPresentation.current = true;
      element.currentTime = frameCentre(activeFrame, segment);
    }
    return () => {disposed = true; element.cancelVideoFrameCallback(callback);};
  }, [segment, frames, version, visible]);

  useEffect(() => {
    const element = video.current;
    if (!element) return;
    if (visible && playing && segment && element.readyState >= 2 && confirmedSegment.current === segment.segment_id && !awaitingFirstPresentation.current) void element.play().catch(e => setMessage(String(e)));
    else element.pause();
  }, [playing, segment, visible]);

  function ready() {
    const element = video.current;
    if (!visible || !element || !segment) return;
    awaitingFirstPresentation.current = true;
    // A new segment already decodes frame zero. Seeking it and immediately playing
    // can discard that first presentation. The rVFC acknowledgement starts playback.
    element.currentTime = frameCentre(activeFrame, segment);
  }

  function ended() {
    if (!segment || !wantsPlay.current) return;
    const nextFrame = segment.source_frames.end + 1;
    if (availability && nextFrame > availability.source_range.end) {onPlaying(false);setMessage("Playback complete");return;}
    // Do not skip holes, even when a later segment is already available.
    setActiveFrame(nextFrame);
    if (!segmentAt(segments, nextFrame)) setMessage(`Buffering at frame ${nextFrame}`);
  }

  const isolated = !segment ? frames.find(f => f.source_frame === activeFrame) : undefined;
  return <section className="review-player" aria-label="Progressive animation">
    {segment ? <video key={segment.segment_id} ref={video} src={mediaUrl(projectId, segment.artifact_id)} muted playsInline preload="auto" onLoadedData={ready} onEnded={ended} onError={() => setMessage(`media_unavailable: ${segment.artifact_id}`)}/>
      : isolated ? <img src={mediaUrl(projectId, isolated.artifact_id)} alt={`Available frame ${activeFrame}`} onLoad={() => {const target = frameTarget(isolated, version); presented.current = target; onPresentedRef.current(target);}}/>
      : <p role="status">Frame {activeFrame} pending. No later interval substituted.</p>}
    {next && <video key={next.segment_id} ref={nextVideo} src={mediaUrl(projectId, next.artifact_id)} muted preload="auto" className="review-prebuffer" aria-hidden="true"/>}
    {(availability || message) && <div className="review-player-status">
      {message && <output aria-live="polite">{message}</output>}
      {availability && <div className="review-intervals" aria-label="Available, pending and failed intervals">{([['Available',availability.available_ranges],['Playable',availability.playable_ranges],['Pending',availability.pending_ranges],['Failed',availability.failed_ranges]] as const).map(([label,ranges])=><span key={label}>{label}: {ranges.map(r=>`${r.start}–${r.end}`).join(", ")||"none"}</span>)}</div>}
    </div>}
  </section>;
}
