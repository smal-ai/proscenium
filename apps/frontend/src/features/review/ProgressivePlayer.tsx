import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FrameAvailability, FrameReceipt, FrameTarget, PreviewSegment, VersionRecord } from "../../api/generated/contracts";
import { frameCentre, frameTarget, mediaUrl, presentedFrame, segmentAt } from "./media";

/** Two independently decoded elements, at most current + immediate next interval.
 * Identity is admitted from rVFC mediaTime, never video.currentTime or a requested seek.
 */
export function ProgressivePlayer({visible = true, projectId, version, frames, segments, availability, expectedEndFrame, jobTerminal, requestedFrame, seekNonce, nextPassSegment, playing, onPlaying, onPresented, onUnpresented, onPlaybackStopped, onEndedPass, completionMessage = "Playback complete"}: {
  visible?: boolean;
  projectId: string; version: VersionRecord; frames: FrameReceipt[]; segments: PreviewSegment[];
  availability: FrameAvailability | null; requestedFrame: number; playing: boolean;
  seekNonce?: number; nextPassSegment?: PreviewSegment;
  expectedEndFrame?: number; jobTerminal?: boolean;
  onPlaying: (playing: boolean) => void; onPresented: (target: FrameTarget) => void;
  onUnpresented?: () => void; onPlaybackStopped?: () => void;
  onEndedPass?: () => boolean; completionMessage?: string;
}) {
  const [activeFrame, setActiveFrame] = useState(requestedFrame);
  const [message, setMessage] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const nextVideo = useRef<HTMLVideoElement>(null);
  const wantsPlay = useRef(playing); wantsPlay.current = visible && playing;
  const presented = useRef<FrameTarget | null>(null);
  const confirmedSegment = useRef("");
  const awaitingFirstPresentation = useRef(true);
  const frameAtSeek = useRef(activeFrame);
  const onPresentedRef = useRef(onPresented); onPresentedRef.current = onPresented;
  const previousRequest = useRef({frame: requestedFrame, nonce: seekNonce});
  const segment = segmentAt(segments, activeFrame);
  const next = segment ? segmentAt(segments, segment.source_frames.end + 1) ?? nextPassSegment : undefined;
  useLayoutEffect(() => {
    if (!visible) { video.current?.pause(); nextVideo.current?.pause(); confirmedSegment.current = ""; awaitingFirstPresentation.current = true; }
  }, [visible]);

  useEffect(() => {
    if (previousRequest.current.frame !== requestedFrame || previousRequest.current.nonce !== seekNonce) {
      previousRequest.current = {frame: requestedFrame, nonce: seekNonce};
      setActiveFrame(requestedFrame); setMessage("");
      const current = video.current; const selected = segmentAt(segments, requestedFrame);
      if (current && selected && selected.segment_id === segment?.segment_id) {
        const centre = frameCentre(requestedFrame, selected);
        if (!current.ended && confirmedSegment.current === selected.segment_id && presented.current?.render_job_id === selected.job_id && presented.current.source_frame === requestedFrame) {
          // A confirmed frame is already displayed, even if the media time was quantised.
          onPresentedRef.current(presented.current);
        } else if (current.ended || !awaitingFirstPresentation.current || activeFrame !== requestedFrame || Math.abs(current.currentTime - centre) >= 1e-3) {
          // Do not restart an in-flight initial presentation for the same frame.
          confirmedSegment.current = "";
          awaitingFirstPresentation.current = true;
          frameAtSeek.current = requestedFrame;
          current.pause(); current.currentTime = centre;
        }
      }
    }
  }, [requestedFrame, seekNonce, segment, segments, activeFrame]);

  useEffect(() => {
    const element = video.current;
    if (!visible || !element || !segment) return;
    if (!element.requestVideoFrameCallback) { setMessage("Frame provenance unavailable in this browser. Use receipted keyframes for annotation."); return; }
    if (confirmedSegment.current !== segment.segment_id) {
      awaitingFirstPresentation.current = true;
      frameAtSeek.current = activeFrame;
    }
    let callback = 0; let disposed = false;
    const observe: VideoFrameRequestCallback = (_now, metadata) => {
      if (disposed) return;
      const frame = presentedFrame(metadata.mediaTime, segment);
      // Initial decode can submit frame zero before an exact seek is presented.
      if (awaitingFirstPresentation.current && frame !== frameAtSeek.current) {
        callback = element.requestVideoFrameCallback(observe);
        return;
      }
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
    // A reused prebuffer may already have loaded metadata while hidden.
    if (element.readyState >= 1 && confirmedSegment.current !== segment.segment_id) {
      const centre = frameCentre(activeFrame, segment);
      if (Math.abs(element.currentTime - centre) >= 1e-3) element.currentTime = centre;
    }
    return () => {disposed = true; element.cancelVideoFrameCallback(callback);};
  }, [segment, frames, version, visible]);

  useEffect(() => {
    const element = video.current;
    if (!element) return;
    if (visible && playing && segment && element.readyState >= 2 && confirmedSegment.current === segment.segment_id && !awaitingFirstPresentation.current) void element.play().catch(e => setMessage(String(e)));
    else element.pause();
  }, [playing, segment, seekNonce, requestedFrame, visible]);

  function ready() {
    const element = video.current;
    if (!visible || !element || !segment) return;
    // Seek as soon as the timeline is known, before the default decode is accepted.
    // The rVFC acknowledgement of the requested frame still starts playback.
    const centre = frameCentre(activeFrame, segment);
    if (Math.abs(element.currentTime - centre) >= 1e-3) element.currentTime = centre;
  }

  function ended() {
    if (!segment || !wantsPlay.current) return;
    const nextFrame = segment.source_frames.end + 1;
    const endFrame = availability?.source_range.end ?? expectedEndFrame;
    if (endFrame !== undefined ? nextFrame > endFrame : !segments.some(clip => clip.source_frames.end >= nextFrame)) {
      if (!onEndedPass?.()) {onPlaying(false);setMessage(completionMessage);}
      return;
    }
    const nextSegment = segmentAt(segments, nextFrame);
    if (!nextSegment) {
      const failed = availability?.failed_ranges.some(r => r.start <= nextFrame && nextFrame <= r.end) ?? false;
      const missingTerminal = Boolean(jobTerminal && !frames.some(f => f.source_frame === nextFrame));
      if (failed || missingTerminal) {
        onUnpresented?.();
        onPlaying(false);
        onPlaybackStopped?.();
        setActiveFrame(nextFrame);
        setMessage(failed ? `Frame ${nextFrame} failed to render. Playback stopped.` : `Frame ${nextFrame} is unavailable. Playback stopped.`);
        return;
      }
    }
    // Do not skip holes, even when a later segment is already available.
    setActiveFrame(nextFrame);
    if (!nextSegment) setMessage(`Buffering at frame ${nextFrame}`);
  }

  const isolated = !segment ? frames.find(f => f.source_frame === activeFrame) : undefined;
  useEffect(() => {
    if (!segment && !isolated) onUnpresented?.();
  }, [segment, isolated, onUnpresented]);
  return <section className="review-player" aria-label="Progressive animation">
    {segment ? <video key={segment.segment_id} ref={video} src={mediaUrl(projectId, segment.artifact_id)} muted playsInline preload="auto" onLoadedMetadata={ready} onEnded={ended} onError={() => setMessage(`media_unavailable: ${segment.artifact_id}`)}/>
      : isolated ? <img src={mediaUrl(projectId, isolated.artifact_id)} alt={`Available frame ${activeFrame}`} onLoad={() => {const target = frameTarget(isolated, version); presented.current = target; onPresentedRef.current(target);}}/>
      : !message && <p role="status">Frame {activeFrame} pending. No later interval substituted.</p>}
    {next && <video key={next.segment_id} ref={nextVideo} src={mediaUrl(projectId, next.artifact_id)} muted preload="auto" className="review-prebuffer" aria-hidden="true"/>}
    {(availability || message) && <div className="review-player-status">
      {message && <output aria-live="polite">{message}</output>}
      {availability && <div className="review-intervals" aria-label="Available, pending and failed intervals">{([['Available',availability.available_ranges],['Playable',availability.playable_ranges],['Pending',availability.pending_ranges],['Failed',availability.failed_ranges]] as const).map(([label,ranges])=><span key={label}>{label}: {ranges.map(r=>`${r.start}–${r.end}`).join(", ")||"none"}</span>)}</div>}
    </div>}
  </section>;
}
