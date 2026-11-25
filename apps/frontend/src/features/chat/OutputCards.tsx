import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ApiClient, Artifact, RetainedArtifactSummary } from "../../api";
import { describeError } from "../../app/storage";
import { isReadyAudio } from "../retainedAudio";
import { artifactUrl } from "./ContextPreview";

const INLINE_CARD_LIMIT = 3;

export function isRenderPassStreamArtifact(summary: RetainedArtifactSummary): boolean {
  const { artifact, job_id, published_versions = [] } = summary;
  if (artifact.purpose === "draft_motion" || artifact.purpose === "inspection") return true;
  if (artifact.kind === "video" && artifact.artifact_id.startsWith("attempt-")) return true;
  if (job_id && (published_versions.length > 0 || artifact.artifact_id.startsWith("preview-") || artifact.purpose === "keyframe" || artifact.purpose === "final")) {
    return true;
  }
  return false;
}

export function OutputCards({ visible = true, api, projectId, artifactIds, retainedArtifacts = [], onInspectArtifact }: {
  visible?: boolean; api: ApiClient; projectId: string; artifactIds: string[]; retainedArtifacts?: RetainedArtifactSummary[]; onInspectArtifact?: (artifact: Artifact) => void;
}) {
  const uniqueIds = [...new Set(artifactIds)];
  if (!uniqueIds.length) return null;
  const knownById = new Map(retainedArtifacts.map(summary => [summary.artifact.artifact_id, summary.artifact]));
  const visibleIds = uniqueIds.slice(0, INLINE_CARD_LIMIT);
  const hiddenIds = uniqueIds.slice(INLINE_CARD_LIMIT);
  return <>
    <div className="retained-output-cards">
      {visibleIds.map(id => <OutputCard visible={visible} key={id} api={api} projectId={projectId} artifactId={id} known={knownById.get(id)} onInspectArtifact={onInspectArtifact} />)}
    </div>
    {hiddenIds.length > 0 && <details className="retained-output-more">
      <summary>Show {hiddenIds.length} more retained {hiddenIds.length === 1 ? "output" : "outputs"}</summary>
      <div className="retained-output-cards">
        {hiddenIds.map(id => <OutputCard visible={visible} key={id} api={api} projectId={projectId} artifactId={id} known={knownById.get(id)} onInspectArtifact={onInspectArtifact} />)}
      </div>
    </details>}
  </>;
}

function OutputCard({ visible, api, projectId, artifactId, known, onInspectArtifact }: { visible: boolean; api: ApiClient; projectId: string; artifactId: string; known?: Artifact; onInspectArtifact?: (artifact: Artifact) => void }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  useLayoutEffect(() => { if (!visible) audioRef.current?.pause(); }, [visible]);
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
  const audio = artifact && isReadyAudio(artifact);
  return <figure className="retained-output-card">
    {preview && !previewUnavailable && (onInspectArtifact ? <button type="button" className="retained-output-preview" aria-label={`Inspect retained ${artifact.purpose} output ${artifactId}`} onClick={() => onInspectArtifact(artifact)}><img loading="lazy" src={artifactUrl(projectId, artifactId)} alt={`Retained ${artifact.purpose} output`} onError={() => setPreviewUnavailable(true)} /></button> : <img loading="lazy" src={artifactUrl(projectId, artifactId)} alt={`Retained ${artifact.purpose} output`} onError={() => setPreviewUnavailable(true)} />)}
    {audio && <audio ref={audioRef} controls preload="metadata" src={artifactUrl(projectId, artifactId)} aria-label={`Retained audio ${artifactId}`} />}
    <figcaption><strong>{artifact ? `${artifact.kind} · ${artifact.purpose}` : "Retained output"}</strong><code title={artifactId}>{artifactId}</code>
      {previewUnavailable && <small>Image preview unavailable. Inspect the retained output metadata before retrying.</small>}
      {artifact?.state === "ready" ? <><a href={artifactUrl(projectId, artifactId)} download={artifactId}>Download output</a>{(preview || audio) && onInspectArtifact && <button onClick={() => onInspectArtifact(artifact)}>Inspect {audio ? "audio" : "image"}</button>}</> : <small>{error || (artifact ? `Output ${artifact.state}` : "Checking output metadata…")}</small>}
    </figcaption>
  </figure>;
}
