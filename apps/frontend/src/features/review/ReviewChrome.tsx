import { ArrowLeft, Box, Download, Film, Image, Layers, X } from "lucide-react";
import type { ReviewSurfaceProps } from "../../api/studio";
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

export function RetainedReview({projectId, version, inspectedArtifact: artifact, inspectedArtifactSummary: summary, onClearArtifact}: Pick<ReviewSurfaceProps, "projectId" | "version" | "inspectedArtifact" | "inspectedArtifactSummary" | "onClearArtifact">) {
  if (!artifact) return null;
  return <section className="review-workspace retained-review" aria-label="Review workspace">
    <div className="review-bar retained-review-toolbar"><Image size={14}/><strong>Retained output preview</strong><span>Unpublished retained output · Concept images are not 3D meshes</span>
      <span className="attribution-badge">{summary?.attribution_status === "verified" ? "Verified" : "Attribution unrecorded"}</span>
      <div className="toolbar-spacer"/><div className="toolbar-group">
        <a className="toolbar-btn" href={mediaUrl(projectId, artifact.artifact_id)} download={artifact.artifact_id}><Download size={13}/>Download image</a>
        {onClearArtifact && <button className="toolbar-btn" onClick={onClearArtifact}>{version ? <><ArrowLeft size={13}/>Back to pinned version</> : <><X size={13}/>Clear preview</>}</button>}
      </div>
    </div>
    {artifact.state === "ready" && artifact.kind === "image" && ["image/png", "image/jpeg", "image/webp"].includes(artifact.media_type) ? <div className="review-image"><img src={mediaUrl(projectId, artifact.artifact_id)} alt={`Retained ${artifact.purpose} image ${artifact.artifact_id}`}/></div> : <div className="viewer-empty">No safe ready image preview available.</div>}
    <footer className="review-bar review-transport-bar">
      {artifact.dimensions && <span className="toolbar-chip">{artifact.dimensions.width} × {artifact.dimensions.height}</span>}
      <span className="toolbar-chip">{artifact.purpose}</span><code className="toolbar-chip">SHA-256 {artifact.sha256.slice(0, 12)}</code><div className="toolbar-spacer"/>
      <span className="toolbar-meta">{summary?.published_versions.length ? `Published to ${summary.published_versions.length} catalogue version(s)` : "Not published to catalogue"}</span>
    </footer>
  </section>;
}
