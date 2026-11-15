import { useEffect, useState } from "react";
import type { ApiClient, Artifact, RetainedArtifactSummary } from "../../api";
import { describeError } from "../../app/storage";
import { artifactUrl } from "./ContextPreview";

export function OutputCards({ api, projectId, artifactIds, retainedArtifacts = [], onInspectArtifact }: {
  api: ApiClient; projectId: string; artifactIds: string[]; retainedArtifacts?: RetainedArtifactSummary[]; onInspectArtifact?: (artifact: Artifact) => void;
}) {
  return <div className="retained-output-cards">{[...new Set(artifactIds)].map(id => <OutputCard key={id} api={api} projectId={projectId} artifactId={id} known={retainedArtifacts.find(summary => summary.artifact.artifact_id === id)?.artifact} onInspectArtifact={onInspectArtifact} />)}</div>;
}
function OutputCard({ api, projectId, artifactId, known, onInspectArtifact }: { api: ApiClient; projectId: string; artifactId: string; known?: Artifact; onInspectArtifact?: (artifact: Artifact) => void }) {
  const [artifact, setArtifact] = useState<Artifact | null>(known ?? null);
  const [error, setError] = useState("");
  const [previewUnavailable, setPreviewUnavailable] = useState(false);
  useEffect(() => {
    let disposed = false;
    if (known) setArtifact(known);
    else void api.call("getArtifact", { path: { project_id: projectId, artifact_id: artifactId } }).then(value => { if (!disposed) setArtifact(value); }).catch(e => { if (!disposed) setError(describeError(e)); });
    return () => { disposed = true; };
  }, [api, projectId, artifactId, known]);
  const preview = artifact?.state === "ready" && artifact.kind === "image" && ["image/png", "image/jpeg", "image/webp"].includes(artifact.media_type);
  return <figure className="retained-output-card">
    {preview && !previewUnavailable && (onInspectArtifact ? <button type="button" className="retained-output-preview" aria-label={`Inspect retained ${artifact.purpose} output ${artifactId}`} onClick={() => onInspectArtifact(artifact)}><img loading="lazy" src={artifactUrl(projectId, artifactId)} alt={`Retained ${artifact.purpose} output`} onError={() => setPreviewUnavailable(true)} /></button> : <img loading="lazy" src={artifactUrl(projectId, artifactId)} alt={`Retained ${artifact.purpose} output`} onError={() => setPreviewUnavailable(true)} />)}
    <figcaption><strong>{artifact ? `${artifact.kind} · ${artifact.purpose}` : "Retained output"}</strong><code title={artifactId}>{artifactId}</code>
      {previewUnavailable && <small>Image preview unavailable. Inspect the retained output metadata before retrying.</small>}
      {artifact?.state === "ready" ? <><a href={artifactUrl(projectId, artifactId)} download={artifactId}>Download output</a>{preview && onInspectArtifact && <button onClick={() => onInspectArtifact(artifact)}>Inspect image</button>}</> : <small>{error || (artifact ? `Output ${artifact.state}` : "Checking output metadata…")}</small>}
    </figcaption>
  </figure>;
}
