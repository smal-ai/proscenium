import { useLayoutEffect, useRef } from "react";
import { ArrowLeft, Box, Download, Film, Image, Layers, Music, X } from "lucide-react";
import type { Artifact } from "../../api";
import type { ReviewSurfaceProps } from "../../api/studio";
import { isReadyAudio } from "../retainedAudio";
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

export function RetainedReview({visible = true, projectId, version, inspectedArtifact: artifact, inspectedArtifactSummary: summary, onClearArtifact}: Pick<ReviewSurfaceProps, "visible" | "projectId" | "version" | "inspectedArtifact" | "inspectedArtifactSummary" | "onClearArtifact">) {
  const audioRef = useRef<HTMLAudioElement>(null);
  useLayoutEffect(() => { if (!visible) audioRef.current?.pause(); }, [visible]);
  if (!artifact) return null;
  const ready = artifact.state === "ready";
  const audio = isReadyAudio(artifact);
  const url = mediaUrl(projectId, artifact.artifact_id);
  return <section className="review-workspace retained-review" aria-label="Review workspace">
    <div className="review-bar retained-review-toolbar">{audio ? <Music size={14}/> : <Image size={14}/>}<strong>Retained output preview</strong><span>{audio ? "Retained audio" : "Unpublished retained output · Concept images are not 3D meshes"}</span>
      <span className="attribution-badge">{summary?.attribution_status === "verified" ? "Verified" : "Attribution unrecorded"}</span>
      <div className="toolbar-spacer"/><div className="toolbar-group">
        {ready && <a className="toolbar-btn" href={url} download={artifact.artifact_id}><Download size={13}/>Download {audio ? "audio" : "image"}</a>}
        {onClearArtifact && <button className="toolbar-btn" onClick={onClearArtifact}>{version ? <><ArrowLeft size={13}/>Back to pinned version</> : <><X size={13}/>Clear preview</>}</button>}
      </div>
    </div>
    {!ready ? <div className="viewer-empty" role="status">Retained output unavailable.</div> : audio ? <div className="review-audio"><audio ref={audioRef} controls preload="metadata" src={url} aria-label={`Retained audio ${artifact.artifact_id}`} /></div> : artifact.kind === "image" && ["image/png", "image/jpeg", "image/webp"].includes(artifact.media_type) ? <div className="review-image"><img src={url} alt={`Retained ${artifact.purpose} image ${artifact.artifact_id}`}/></div> : <div className="viewer-empty">No safe ready image preview available.</div>}
    <footer className="review-bar review-transport-bar">
      {artifact.dimensions && <span className="toolbar-chip">{artifact.dimensions.width} × {artifact.dimensions.height}</span>}
      {audio && artifact.duration_seconds !== undefined && <span className="toolbar-chip">{Number(artifact.duration_seconds.toFixed(3))} s</span>}
      {audio && artifact.sample_rate_hz !== undefined && <span className="toolbar-chip">{artifact.sample_rate_hz.toLocaleString("en-GB")} Hz</span>}
      {audio && artifact.channels !== undefined && <span className="toolbar-chip">{artifact.channels} {artifact.channels === 1 ? "channel" : "channels"}</span>}
      <span className="toolbar-chip">{artifact.purpose}</span><code className="toolbar-chip">SHA-256 {artifact.sha256.slice(0, 12)}</code><div className="toolbar-spacer"/>
      <span className="toolbar-meta">{summary?.published_versions.length ? `Published to ${summary.published_versions.length} catalogue version(s)` : "Not published to catalogue"}</span>
    </footer>
  </section>;
}
