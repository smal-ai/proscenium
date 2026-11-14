import { useEffect, useState } from "react";
import type { ProxyMapping } from "../../api/generated/contracts";
import type { ReviewSurfaceProps } from "../../api/studio";
import { useReviewSelection } from "./state";

export function Outliner({api, projectId, version, refreshRevision}: ReviewSurfaceProps) {
  const [mapping, setMapping] = useState<ProxyMapping | null>(null);
  const [error, setError] = useState("");
  const choice = useReviewSelection(); const scope = `${projectId}:${version?.version_id}`;
  useEffect(() => {
    setMapping(null); setError("");
    if (!version) return;
    let active = true;
    api.call("getVersionProxyMapping", {path: {project_id: projectId, version_id: version.version_id}}).then(value => {
      if (value.version_id !== version.version_id || value.source_closure_digest !== version.source.digest) throw new Error("Proxy source mismatch");
      if (active) setMapping(value);
    }).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [api, projectId, version, refreshRevision]);
  return <section className="review-outliner" aria-label="Published outliner">
    <p>{mapping ? `Static proxy · source frame ${mapping.sample_frame}` : "No published proxy mapping"}</p>
    {mapping?.nodes.map(node => <button key={`${node.object_id}:${node.instance_id}`} title={`${node.object_id} / ${node.instance_id}`}
      aria-pressed={choice.scope === scope && choice.selected.some(s => s.object_id === node.object_id && s.instance_id === node.instance_id)}
      onClick={event => {
        const pair = {object_id: node.object_id, instance_id: node.instance_id};
        const previous = choice.scope === scope ? choice.selected : [];
        choice.select(scope, event.ctrlKey || event.metaKey ? previous.some(s => s.object_id === pair.object_id && s.instance_id === pair.instance_id) ? previous.filter(s => s.object_id !== pair.object_id || s.instance_id !== pair.instance_id) : [...previous, pair] : [pair]);
      }}>{node.display_name}<small>{node.instance_id}</small></button>)}
    <p>Object context only. No spatial hit is inferred from this list.</p>
    {error && <p role="status">{error}</p>}
  </section>;
}
