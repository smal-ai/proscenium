import { useState } from "react";
import { ArrowUp, Paperclip, Square, X, MessageSquare } from "lucide-react";
import type { ApiClient, Artifact, Attachment, Event, JobRecord, ProjectSnapshot, RequestRecord, ResumeCommand, RevisionCommand } from "../../api";
import type { TurnSelection } from "../../api/studio";
import { describeError, durableCommand, id, readPrivate, writePrivate } from "../../app/storage";
import { DecisionCard } from "./DecisionCard";
import { ContextPreview } from "./ContextPreview";
import { Conversation } from "./Conversation";
import type { useRequestComposer } from "./useRequestComposer";

interface Upload { uploadId: string; name: string; file: File; artifact: Artifact | null; busy: boolean; error: string }
export function ChatPanel({ api, projectId, snapshot, requests, events, jobs, selection, composer, onRefresh }: {
  api: ApiClient; projectId: string; snapshot: ProjectSnapshot | null; requests: RequestRecord[]; events: Event[]; jobs: JobRecord[]; selection: TurnSelection;
  composer: ReturnType<typeof useRequestComposer>; onRefresh: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(() => readPrivate<string>(`draft.${projectId}`) ?? "");
  const [attachments, setAttachments] = useState<Attachment[]>(() => readPrivate<Attachment[]>(`attachments.${projectId}`) ?? []);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [mode, setMode] = useState<"queue" | "steer">("queue");
  const [targetWork, setTargetWork] = useState("");
  const [resumeMode, setResumeMode] = useState<ResumeCommand["mode"]>("reconcile_only");
  const [error, setError] = useState("");
  const [resuming, setResuming] = useState(false);
  const work = snapshot?.active_work;
  const active = work && !["completed", "failed", "stopped"].includes(work.state);
  const currentSelection = { ...selection, attachments: [...selection.attachments, ...attachments] };
  function changeDraft(value: string) { setDraft(value); writePrivate(`draft.${projectId}`, value); }
  function changeAttachments(value: Attachment[]) { setAttachments(value); writePrivate(`attachments.${projectId}`, value); }
  async function upload(value: Upload) {
    setUploads(old => old.map(u => u.uploadId === value.uploadId ? { ...u, busy: true, error: "" } : u));
    try {
      const artifact = await api.call("uploadImage", { path: { project_id: projectId, upload_id: value.uploadId }, body: value.file, idempotencyKey: value.uploadId });
      if (artifact.state !== "ready" || artifact.kind !== "image") throw new Error("Image is not ready for context capture.");
      setUploads(old => old.filter(u => u.uploadId !== value.uploadId));
      setAttachments(old => { const next = [...old, { artifact_id: artifact.artifact_id, role: "reference" as const }]; writePrivate(`attachments.${projectId}`, next); return next; });
    } catch (e) { setUploads(old => old.map(u => u.uploadId === value.uploadId ? { ...u, busy: false, error: describeError(e) } : u)); }
  }
  async function send() {
    const text = draft;
    const submittedIds = new Set(attachments.map(a => a.artifact_id));
    const ok = await composer.submit(text, currentSelection, mode, mode === "steer" ? targetWork : null, snapshot?.settings.default_profile_id ?? null);
    if (ok) {
      if (readPrivate<string>(`draft.${projectId}`) === text) changeDraft("");
      setAttachments(old => { const next = old.filter(a => !submittedIds.has(a.artifact_id)); writePrivate(`attachments.${projectId}`, next); return next; });
    }
  }
  async function resume() {
    if (!snapshot) return;
    setResuming(true); setError("");
    try {
      await durableCommand<ResumeCommand, unknown>(`resume.${projectId}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: projectId, expected_revision: snapshot.project.state_revision, mode: resumeMode, work_id: work?.work_id ?? null }), body => api.call("resumeProjectWork", { path: { project_id: projectId }, body, idempotencyKey: body.operation_id }));
      await onRefresh();
    } catch (e) { setError(describeError(e)); }
    finally { setResuming(false); }
  }
  return <div className="chat-panel"><div className="chat-scroll">
    <div className="copilot-identity"><span className="copilot-mark"><MessageSquare size={16} /></span><div><strong>Project Copilot</strong><small>{work ? `Work ${work.work_id} · ${work.state}` : "No active work"}</small></div></div>
    {snapshot?.pending_decisions.map(decision => <DecisionCard key={`${decision.decision_id}.${decision.state_revision}`} api={api} decision={decision} onRefresh={onRefresh} />)}
    {(snapshot?.project.state === "awaiting_resume" || requests.some(r => r.receipt.state === "held") || (work && ["stopped", "awaiting_resume", "outcome_uncertain"].includes(work.state))) && <section className="resume-card"><h3>Continue deliberately</h3><p>Held requests stay held until explicitly released. Reconcile inspects known outcomes without sending a new prompt.</p><select aria-label="Resume mode" value={resumeMode} disabled={resuming} onChange={e => setResumeMode(e.target.value as ResumeCommand["mode"])}><option value="reconcile_only">Reconcile only</option><option value="continue_work">Continue original work</option><option value="release_held_queue">Release held queue</option></select><button disabled={resuming} onClick={() => void resume()}>Resume</button></section>}
    <Conversation api={api} projectId={projectId} requests={requests} events={events} onRefresh={onRefresh} onError={e => setError(describeError(e))} />
    <details className="jobs"><summary>Independent jobs <span>{jobs.length}</span></summary><p>Stop Copilot does not stop these jobs.</p>{jobs.map(job => <div className="job-row" key={job.job_id}><strong>{job.kind}</strong><small>{job.job_id} · {job.state}</small>{!["succeeded", "failed", "cancelled"].includes(job.state) && <button onClick={() => void durableCommand<RevisionCommand, unknown>(`job-cancel.${job.job_id}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: projectId, expected_revision: job.state_revision }), body => api.call("requestJobCancellation", { path: { project_id: projectId, job_id: job.job_id }, body, idempotencyKey: body.operation_id })).then(onRefresh).catch(e => setError(describeError(e)))}>Request job cancellation</button>}</div>)}{!jobs.length && <small>No jobs recorded.</small>}</details>
    <small className="usage-note">Usage is shown only when reported by the runtime. Missing usage is unavailable, not zero.</small>
  </div>
    <div className="composer">
      {(composer.error || error) && <p role="alert">{composer.error || error}</p>}
      {composer.pending && <div className="retry-card"><strong>Request awaiting confirmation</strong><code>{composer.pending.submission.request_id}</code><p>Its original text and context are frozen. Your draft is retained.</p><button disabled={composer.busy} onClick={() => void composer.retry(false)}>Reconcile original request / retry same ID</button></div>}
      {composer.pendingStop && <div className="retry-card"><strong>Stop awaiting confirmation</strong><button disabled={composer.stopping} onClick={() => void composer.retry(true)}>Reconcile original Stop</button></div>}
      <details className="capture-details"><summary>Next request context · {currentSelection.attachments.length} images</summary><ContextPreview projectId={projectId} selection={currentSelection} /><p>Relevant active-project files remain available through visible tool activity.</p>{attachments.map((a, i) => <div className="attachment-controls" key={`${a.artifact_id}.${i}`}><select aria-label={`Image ${i + 1} role`} value={a.role} onChange={e => changeAttachments(attachments.map((x, j) => i === j ? { ...x, role: e.target.value as Attachment["role"] } : x))}>{["reference", "concept", "render", "markup", "mask"].map(role => <option key={role}>{role}</option>)}</select><button aria-label={`Remove image ${i + 1}`} onClick={() => changeAttachments(attachments.filter((_, j) => i !== j))}><X size={13} /></button></div>)}</details>
      {uploads.map(u => <div className="upload-state" key={u.uploadId}><span>{u.name} · {u.busy ? "Uploading and validating…" : u.error}</span>{!u.busy && <><button onClick={() => void upload(u)}>Retry same upload</button><button onClick={() => setUploads(old => old.filter(x => x.uploadId !== u.uploadId))}>Remove</button></>}</div>)}
      <label className="sr-only" htmlFor="message-draft">Message Copilot</label><textarea id="message-draft" aria-label="Message Copilot" value={draft} onChange={e => changeDraft(e.target.value)} placeholder="Describe the next iteration…" onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (!uploads.length && !composer.pending) void send(); } }} />
      {mode === "steer" && <label className="steer-target">Active work target<select aria-label="Steer active work target" value={targetWork} onChange={e => setTargetWork(e.target.value)}><option value="">Select active work explicitly</option>{active && <option value={work.work_id}>{work.work_id} · {work.state}</option>}</select><small>Steer corrects active work. It does not undo completed actions.</small></label>}
      <div className="composer-actions"><label className="attach-button" title="Attach PNG, JPEG or WebP"><Paperclip size={15} /><span className="sr-only">Attach images</span><input aria-label="Attach images" type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={e => { const next = [...(e.target.files ?? [])].map(file => ({ uploadId: id(), name: file.name, file, artifact: null, busy: true, error: "" })); setUploads(old => [...old, ...next]); next.forEach(u => void upload(u)); e.target.value = ""; }} /></label>
        <select aria-label="Request mode" value={mode} onChange={e => { setMode(e.target.value as "queue" | "steer"); setTargetWork(""); }}><option value="queue">Queue</option><option value="steer">Steer</option></select>
        <button className="stop" disabled={!active || composer.stopping || !!composer.pendingStop || work?.state === "stop_requested"} onClick={() => work && void composer.stop(work.work_id)}><Square size={11} fill="currentColor" />{composer.stopping || work?.state === "stop_requested" ? "Stop requested" : "Stop"}</button>
        <button className="primary send" aria-label={mode === "queue" ? "Queue message" : "Steer active work"} disabled={!draft.trim() || !!uploads.length || composer.busy || !!composer.pending || (mode === "steer" && (!active || targetWork !== work.work_id))} onClick={() => void send()}><ArrowUp size={16} /></button>
      </div><small className="composer-hint">Ctrl+Enter to {mode} · Context is captured on submit</small>
    </div>
  </div>;
}
