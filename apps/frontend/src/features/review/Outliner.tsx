import { useEffect, useState } from "react";
import type { ProxyMapping } from "../../api/generated/contracts";
import type { ReviewSurfaceProps } from "../../api/studio";
import { useReviewSelection } from "./state";
import { proxyJobsFingerprint } from "./refreshKeys";

export function Outliner({api, projectId, version, jobs}: ReviewSurfaceProps) {
  const [mapping, setMapping] = useState<ProxyMapping | null>(null);
  const [error, setError] = useState("");
  const choice = useReviewSelection(); const scope = `${projectId}:${version?.version_id}`;
  const versionId = version?.version_id;
  const digest = version?.source.digest;
  const proxyJobsKey = proxyJobsFingerprint(jobs, versionId);
  useEffect(() => {
    setMapping(null); setError("");
    if (!versionId || !digest) return;
    const controller = new AbortController();
    const {signal} = controller;
    api.call("getVersionProxyMapping", {path: {project_id: projectId, version_id: versionId}, signal}).then(value => {
      if (value.project_id !== projectId || value.version_id !== versionId || value.source_closure_digest !== digest) throw new Error("Proxy source mismatch");
      if (!signal.aborted) setMapping(value);
    }).catch(e => {if (!signal.aborted) setError(String(e));});
    return () => controller.abort();
  }, [api, projectId, versionId, digest, proxyJobsKey]);
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
