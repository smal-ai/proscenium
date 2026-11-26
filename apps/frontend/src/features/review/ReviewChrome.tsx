import { useLayoutEffect, useRef } from "react";
import { ArrowLeft, Box, Download, Film, Image, Layers, Music, X } from "lucide-react";
import type { Artifact, VersionRecord } from "../../api";
import type { ReviewSurfaceProps } from "../../api/studio";
import { isReadyAudio } from "../retainedAudio";
import { isReadyVideo } from "../retainedVideo";
import { mediaUrl } from "./media";
import "./review.css";

export type ReviewMode = "3d_inspection" | "keyframes" | "animation";

export function ReviewModeSwitcher({mode, onChange}: {mode: ReviewMode; onChange: (mode: ReviewMode) => void}) {
  return <div className="segmented-control">{([
    {id: "3d_inspection", label: "3D Inspection", icon: Box},
    {id: "keyframes", label: "Keyframes", icon: Image},
    {id: "animation", label: "Animation", icon: Film},
  ] as const).map(({id, label, icon: Icon}, index) => <button key={id} aria-pressed={mode === id} aria-keyshortcuts={String(index + 1)} title={`${label} (${index + 1})`} onClick={() => onChange(id)}><Icon size={14}/>{label}</button>)}</div>;
}

export function ReviewEmpty({mode, onChange, hasProject = true, version}: {mode: ReviewMode; onChange: (mode: ReviewMode) => void; hasProject?: boolean; version?: ReviewSurfaceProps["version"]}) {
  return <section className="review-workspace" aria-label="Review workspace">
    <nav className="review-bar" aria-label="Review mode"><ReviewModeSwitcher mode={mode} onChange={onChange}/></nav>
    <div className="viewer-empty" data-review-mode={mode}>
      <div className="viewer-symbol"><Layers size={34} strokeWidth={1}/></div>
      <h2>{!hasProject ? "Your next production starts here" : !version ? "Choose a published item" : "Review surface unavailable"}</h2>
      <p>{!hasProject ? "Create or open a project on the workstation, then activate it to begin." : !version ? "Select a source version from Assets. Older publications stay pinned while new work arrives." : "This shell does not include the media review component. Your selected source version is retained."}</p>
      <small>No render starts when changing mode</small>
    </div>
  </section>;
}

export function currentRetainedPreview(
  projectId: string, inspected: ReviewSurfaceProps["inspectedArtifact"], observedArtifacts: ReadonlyMap<string, Artifact>,
) {
  if (inspected?.project_id !== projectId) return null;
  return observedArtifacts.get(inspected.artifact_id) ?? inspected;
}

export function ReviewVideo({visible, url, label}: {visible: boolean; url: string; label: string}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  useLayoutEffect(() => {
    const video = videoRef.current;
    if (!visible) video?.pause();
    return () => { video?.pause(); };
  }, [visible, url]);
  return <div className="review-video"><video ref={videoRef} controls preload="metadata" playsInline src={url} aria-label={label}/></div>;
}

function VideoFrameRate({fps}: Pick<Artifact, "fps">) {
  return fps ? <span className="toolbar-chip" title={`${fps.numerator}/${fps.denominator} fps`}>{Number((fps.numerator / fps.denominator).toFixed(3))} fps</span> : null;
}

export function clipSequenceDelivery(artifacts: readonly Artifact[], versionId: string): Artifact | undefined {
  return artifacts.find(a => a.source_version_id === versionId && a.purpose === "final" && isReadyVideo(a)
    && a.derived_from.some(t => t.kind === "encode" && "assembly" in t.parameters && t.parameters.assembly === "clip_concat"));
}

export function directPublishedVideo(artifacts: readonly Artifact[], version: Pick<VersionRecord, "version_id" | "artifact_ids" | "kind">): Artifact | undefined {
  const byId = new Map(artifacts.map(artifact => [artifact.artifact_id, artifact]));
  for (const id of version.artifact_ids) {
    const artifact = byId.get(id);
    if (artifact && isReadyVideo(artifact) && !id.startsWith("attempt-") && !id.startsWith("preview-")
      && artifact.purpose !== "inspection" && artifact.purpose !== "draft_motion"
      && !artifact.derived_from.some(t => t.kind === "encode" && "frame_map_sha256" in t.parameters)) return artifact;
  }
  return undefined;
}

export function SequenceReelReview({visible, projectId, artifact, heading = "Assembled clip sequence"}: {visible: boolean; projectId: string; artifact: Artifact; heading?: string}) {
  const url = mediaUrl(projectId, artifact.artifact_id);
  return <section className="review-workspace" aria-label="Review workspace">
    <div className="review-bar"><Film size={14}/><strong>{heading}</strong><div className="toolbar-spacer"/>
      <a className="toolbar-btn" href={url} download={artifact.artifact_id}><Download size={13}/>Download video</a>
    </div>
    <ReviewVideo key={url} visible={visible} url={url} label={`${heading} ${artifact.artifact_id}`}/>
    <footer className="review-bar review-transport-bar"><MediaMetadata artifact={artifact} audio={false} video={true}/></footer>
  </section>;
}

export function MediaMetadata({artifact, audio, video}: {artifact: Artifact; audio: boolean; video: boolean}) {
  return <>
    {artifact.dimensions && <span className="toolbar-chip">{artifact.dimensions.width} × {artifact.dimensions.height}</span>}
    {video && <VideoFrameRate fps={artifact.fps}/>}
    {(audio || video) && artifact.duration_seconds !== undefined && <span className="toolbar-chip">{Number(artifact.duration_seconds.toFixed(3))} s</span>}
    {(audio || video) && artifact.sample_rate_hz !== undefined && <span className="toolbar-chip">{artifact.sample_rate_hz.toLocaleString("en-GB")} Hz</span>}
    {(audio || video) && artifact.channels !== undefined && <span className="toolbar-chip">{artifact.channels} {artifact.channels === 1 ? "channel" : "channels"}</span>}
  </>;
}

export function RetainedReview({visible = true, projectId, version, inspectedArtifact: artifact, inspectedArtifactSummary: summary, onClearArtifact}: Pick<ReviewSurfaceProps, "visible" | "projectId" | "version" | "inspectedArtifact" | "inspectedArtifactSummary" | "onClearArtifact">) {
  const audioRef = useRef<HTMLAudioElement>(null);
  useLayoutEffect(() => { if (!visible) audioRef.current?.pause(); }, [visible]);
  if (!artifact) return null;
  const ready = artifact.state === "ready";
  const audio = isReadyAudio(artifact);
  const video = isReadyVideo(artifact);
  const label = artifact.kind === "audio" || artifact.kind === "video" ? artifact.kind : "image";
  const Icon = label === "video" ? Film : label === "audio" ? Music : Image;
  const url = mediaUrl(projectId, artifact.artifact_id);
  return <section className="review-workspace retained-review" aria-label="Review workspace">
    <div className="review-bar retained-review-toolbar"><Icon size={14}/><strong>Retained output preview</strong><span>{label === "image" ? "Unpublished retained output · Concept images are not 3D meshes" : `Retained ${label}`}</span>
      <span className="attribution-badge">{summary?.attribution_status === "verified" ? "Verified" : "Attribution unrecorded"}</span>
      <div className="toolbar-spacer"/><div className="toolbar-group">
        {ready && <a className="toolbar-btn" href={url} download={artifact.artifact_id}><Download size={13}/>Download {label}</a>}
        {onClearArtifact && <button className="toolbar-btn" onClick={onClearArtifact}>{version ? <><ArrowLeft size={13}/>Back to pinned version</> : <><X size={13}/>Clear preview</>}</button>}
      </div>
    </div>
    {!ready ? <div className="viewer-empty" role="status">Retained output unavailable.</div>
      : video ? <ReviewVideo key={url} visible={visible} url={url} label={`Retained video ${artifact.artifact_id}`}/>
      : audio ? <div className="review-audio"><audio ref={audioRef} controls preload="metadata" src={url} aria-label={`Retained audio ${artifact.artifact_id}`}/></div>
      : artifact.kind === "image" && ["image/png", "image/jpeg", "image/webp"].includes(artifact.media_type) ? <div className="review-image"><img src={url} alt={`Retained ${artifact.purpose} image ${artifact.artifact_id}`}/></div>
      : <div className="viewer-empty">No safe ready {label} preview available.</div>}
    <footer className="review-bar review-transport-bar">
      <MediaMetadata artifact={artifact} audio={audio} video={video}/>
      <span className="toolbar-chip">{artifact.purpose}</span><code className="toolbar-chip">SHA-256 {artifact.sha256.slice(0, 12)}</code><div className="toolbar-spacer"/>
      <span className="toolbar-meta">{summary?.published_versions.length ? `Published to ${summary.published_versions.length} catalogue version(s)` : "Not published to catalogue"}</span>
    </footer>
  </section>;
}
