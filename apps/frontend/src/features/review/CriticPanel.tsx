import { useEffect, useRef, useState } from "react";
import type { ReviewFinding, ReviewRequest, ReviewTicket } from "../../api/generated/contracts";
import type { CriticSurfaceProps } from "../../api/studio";
import { mediaUrl } from "./media";
import { allPages } from "./records";

export function CriticPanel({api, projectId, selection, refreshRevision, onQueue, onSteer}: CriticSurfaceProps) {
  const [opened, setOpened] = useState(false);
  const [criteria, setCriteria] = useState("");
  const [tickets, setTickets] = useState<ReviewTicket[]>([]);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [findings, setFindings] = useState<ReviewFinding[]>([]);
  const [approved, setApproved] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const pendingRequest = useRef<ReviewRequest | null>(null);
  const queueLatch = useRef(false);
  const submitted = useRef(new Set<string>());

  useEffect(() => {
    if (!opened) return;
    let active = true;
    allPages(cursor => api.call("listOwnerReviews", {path: {project_id: projectId}, query: {cursor, limit: 100}})).then(value => {if (active) setTickets(value);}).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [api, projectId, opened, refreshRevision]);

  useEffect(() => {
    if (!opened || !reviewId) return;
    let active = true;
    allPages(cursor => api.call("listOwnerReviewFindings", {path: {project_id: projectId, review_id: reviewId}, query: {cursor, limit: 100}})).then(value => {if (active) setFindings(value);}).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [api, projectId, opened, reviewId, refreshRevision]);

  async function requestReview() {
    if (!selection.target || !criteria.trim()) return;
    pendingRequest.current ??= {schema_version: "1.0", review_id: crypto.randomUUID(), project_id: projectId,
      targets: [structuredClone(selection.target)], criteria, telemetry_artifact_ids: []};
    setBusy(true); setOpened(true); setError("");
    try {
      const request = pendingRequest.current;
      const ticket = await api.call("requestOwnerReview", {path: {project_id: projectId}, body: request, idempotencyKey: request.review_id});
      setTickets(previous => [ticket, ...previous.filter(t => t.review_id !== ticket.review_id)]); setReviewId(ticket.review_id);
      pendingRequest.current = null;
    } catch (e) {setError(`Review request retained for exact retry: ${String(e)}`);} finally {setBusy(false);}
  }

  async function reject(finding: ReviewFinding) {
    const operation_id = crypto.randomUUID(); setBusy(true);
    try {
      const ack = await api.call("rejectReviewFinding", {path: {project_id: projectId, review_id: finding.review_id, finding_id: finding.finding_id},
        body: {schema_version: "1.0", operation_id, project_id: projectId, expected_revision: finding.revision}, idempotencyKey: operation_id});
      setFindings(previous => previous.map(f => f.finding_id === finding.finding_id ? {...f, state: "rejected", revision: ack.state_revision} : f));
      setApproved(previous => {const next = {...previous}; delete next[finding.finding_id]; return next;});
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }

  async function queue(steer = false) {
    if (queueLatch.current) return;
    const selected = findings.filter(f => approved[f.finding_id]?.trim() && f.state !== "submitted" && !submitted.current.has(`${f.finding_id}:${f.revision}`));
    if (!selected.length) return;
    const associated = new Set(selected.flatMap(f => [f.target.artifact_id, ...f.evidence.filter(e => e.kind === "visual").map(e => e.artifact_id)]));
    queueLatch.current = true; setBusy(true); setError("");
    const target = selected[0].target;
    try {await (steer && onSteer ? onSteer : onQueue)(selected.map(f => approved[f.finding_id]).join("\n\n"), {...selection,
      item_id: target.item_id, version_id: target.version_id, target: structuredClone(target), camera: null,
      view_mode: target.media_artifact_id ? "animation" : "keyframes", range: null, selected_objects: [], annotations: [],
      attachments: [...associated].map(artifact_id => ({artifact_id, role: "render"})),
      approved_findings: selected.map(f => ({finding_id: f.finding_id, finding_revision: f.revision, approved_text: approved[f.finding_id], target: structuredClone(f.target)})),
    });
      for (const finding of selected) submitted.current.add(`${finding.finding_id}:${finding.revision}`);
      setQueued(true);
    } catch (e) {setError(`Submission failed. Findings remain available: ${String(e)}`); queueLatch.current = false;}
    finally {setBusy(false);}
  }

  return <section className="review-critic" aria-label="Critic">
    <label>Review criteria<textarea value={criteria} disabled={!!pendingRequest.current} onChange={e => setCriteria(e.target.value)}/></label>
    <button disabled={busy || !selection.target || !criteria.trim()} onClick={() => void requestReview()}>{pendingRequest.current ? "Retry original review" : "Request visual review"}</button>
    <button onClick={() => setOpened(true)}>Load existing reviews</button>
    {opened && <>
      <label>Review<select aria-label="Review" value={reviewId ?? ""} onChange={e => {setReviewId(e.target.value); setApproved({}); setQueued(false); queueLatch.current = false;}}><option value="">Select a review</option>{tickets.map(t => <option key={t.review_id} value={t.review_id}>{t.review_id} · {t.status}</option>)}</select></label>
      {findings.map(f => <article key={f.finding_id}>
        <img src={mediaUrl(projectId, f.target.artifact_id)} alt={`Reviewed target ${f.target.artifact_id}`}/>
        <p>{f.observation}</p><p>{f.recommendation}</p>
        <ul>{f.evidence.map((e,i) => <li key={i}><strong>{e.kind === "measured" ? "Measured telemetry" : "Visual observation"}</strong>: {e.description} <small>{e.artifact_id}</small></li>)}</ul>
        <p>{f.state} · revision {f.revision} · frame {f.target.sequence_frame ?? f.target.source_frame ?? "image"}</p>
        <label><input type="checkbox" disabled={queued || f.state === "submitted"} checked={f.finding_id in approved} onChange={e => setApproved(previous => {const next = {...previous}; if (e.target.checked) next[f.finding_id] = f.recommendation || f.observation; else delete next[f.finding_id]; return next;})}/> Include finding</label>
        {f.finding_id in approved && <textarea aria-label={`Approved instruction ${f.finding_id}`} disabled={queued} value={approved[f.finding_id]} onChange={e => setApproved(previous => ({...previous, [f.finding_id]: e.target.value}))}/>}
        <button disabled={busy || queued || f.state !== "unreviewed"} onClick={() => void reject(f)}>Reject finding</button>
      </article>)}
      <button disabled={busy || queued || !Object.values(approved).some(t => t.trim())} onClick={() => void queue()}>{queued ? "Queued selected findings" : "Queue selected findings once"}</button>
      {onSteer && <button disabled={busy || queued || !Object.values(approved).some(t => t.trim())} onClick={() => void queue(true)}>Steer selected findings once</button>}
      <p>Only your selected instructions enter Copilot. No review automatically changes Blender.</p>
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
