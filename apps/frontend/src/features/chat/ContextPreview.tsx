import { useEffect, useState } from "react";
import type { ApiClient, ContextSnapshot } from "../../api";
import type { TurnSelection } from "../../api/studio";
import { routes } from "../../api/generated/operations";
import { describeError, readPrivate } from "../../app/storage";
import { targetLabel } from "./target";

export function artifactUrl(projectId: string, artifactId: string): string {
  return routes.getArtifactContent.path.replace("{project_id}", encodeURIComponent(projectId)).replace("{artifact_id}", encodeURIComponent(artifactId));
}

export function ContextPreview({ selection, projectId }: { selection: TurnSelection; projectId: string }) {
  return <div className="context-preview">
    {selection.request_target && <><span className="target-badge">Target: {targetLabel(selection.request_target)}</span><code>{JSON.stringify(selection.request_target)}</code></>}
    {!!selection.reference_versions?.length && <div><strong>Reference versions</strong>{selection.reference_versions.map(ref => <code key={`${ref.item_id}:${ref.version_id}`}>{ref.item_id} · {ref.version_id}</code>)}</div>}
    {selection.execution_scope && <span>Execution scope: {selection.execution_scope === "planning_only" ? "Planning only · no generation or scene execution" : "Default"}</span>}
    <span>{selection.version_id ? `Version ${selection.version_id}` : "Project context"} · {selection.view_mode.replaceAll("_", " ")}</span>
    {selection.selected_objects.length > 0 && <span>{selection.selected_objects.length} object/instance selections</span>}
    {selection.target && <code>{JSON.stringify(selection.target)}</code>}
    {selection.camera && <span>Captured inspection camera</span>}
    {selection.range && <code>Range {JSON.stringify(selection.range)}</code>}
    {selection.annotations.map(annotation => <div key={annotation.annotation_id} className="annotation-context"><span>Annotation {annotation.annotation_id}</span><code>{JSON.stringify(annotation.target)}</code></div>)}
    {selection.approved_findings.length > 0 && <span>{selection.approved_findings.length} explicitly approved findings</span>}
    <div className="attachment-previews">{selection.attachments.map((a, i) => <figure key={`${a.artifact_id}.${i}`}><a href={artifactUrl(projectId, a.artifact_id)} target="_blank" rel="noreferrer"><img src={artifactUrl(projectId, a.artifact_id)} alt={`${a.role} attachment`} loading="lazy" /></a><figcaption>{a.role}{a.use ? ` · ${a.use}` : ""}</figcaption></figure>)}</div>
  </div>;
}

export function StoredContext({ api, projectId, contextId, items = [] }: { api: ApiClient; projectId: string; contextId: string; items?: import("../../api").ItemRecord[] }) {
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<ContextSnapshot | null>(() => readPrivate(`context.${projectId}.${contextId}`));
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    if (!context) void api.call("getContext", { path: { project_id: projectId, context_id: contextId } }).then(c => { if (!disposed) setContext(c); }).catch(e => { if (!disposed) setError(describeError(e)); });
    return () => { disposed = true; };
  }, [api, projectId, contextId, context, open]);
  return <div className="stored-context-row">{context?.request_target && <span className="target-badge" title={JSON.stringify(context.request_target)}>Target: {targetLabel(context.request_target, items)}</span>}<details className="stored-context" onToggle={e => setOpen(e.currentTarget.open)}><summary>Original captured context</summary>{context ? <ContextPreview projectId={projectId} selection={context} /> : <small>{error || "Loading original context…"}</small>}</details></div>;
}
