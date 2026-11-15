import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, Paperclip, Square, X, MessageSquare } from "lucide-react";
import type { ApiClient, Artifact, Attachment, Event, JobRecord, ProjectSnapshot, RequestRecord, ResumeCommand } from "../../api";
import type { ContextPreparer, TurnSelection } from "../../api/studio";
import { describeError, durableCommand, id, readPrivate, writePrivate } from "../../app/storage";
import { DecisionCard } from "./DecisionCard";
import { ContextPreview } from "./ContextPreview";
import { Conversation } from "./Conversation";
import type { useRequestComposer } from "./useRequestComposer";
import { resumableWork, resumeCommand } from "./resume";
import { useChatScroll } from "./useChatScroll";
import { ComposerPanel, JobList } from "./ComposerPanel";
import "./chat.css";

interface Upload { uploadId: string; name: string; file: File; artifact: Artifact | null; busy: boolean; error: string }
export function ChatPanel({ api, projectId, snapshot, requests, events, jobs, selection, prepareContext, composer, onRefresh }: {
  api: ApiClient; projectId: string; snapshot: ProjectSnapshot | null; requests: RequestRecord[]; events: Event[]; jobs: JobRecord[]; selection: TurnSelection;
  composer: ReturnType<typeof useRequestComposer>; onRefresh: () => Promise<void>;
  prepareContext?: ContextPreparer;
}) {
  const [draft, setDraft] = useState(() => readPrivate<string>(`draft.${projectId}`) ?? "");
  const [attachments, setAttachments] = useState<Attachment[]>(() => readPrivate<Attachment[]>(`attachments.${projectId}`) ?? []);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [mode, setMode] = useState<"queue" | "steer">("queue");
  const [targetWork, setTargetWork] = useState("");
  const [resumeMode, setResumeMode] = useState<ResumeCommand["mode"]>("reconcile_only");
  const [resumeWorkId, setResumeWorkId] = useState("");
  const [pendingResume, setPendingResume] = useState<ResumeCommand | null>(() => readPrivate(`command.resume.${projectId}`));
  const [error, setError] = useState("");
  const [resuming, setResuming] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const preparingLatch = useRef(false);
  const messageRef = useRef<HTMLTextAreaElement>(null);
  const [composerPanel, setComposerPanel] = useState<"message" | "jobs" | "context">("message");
  const { scrollRef, contentRef, isFollowing, onScroll, jumpToLatest } = useChatScroll(projectId, requests, events);
  const work = snapshot?.active_work;
  const active = work && !["completed", "failed", "stopped"].includes(work.state);
  const resumable = resumableWork(snapshot, events);
  const chosenResumeWork = resumeWorkId
    ? resumable.find(record => record.work_id === resumeWorkId)
    : resumable.find(record => record.work_id === work?.work_id) ?? (resumable.length === 1 ? resumable[0] : undefined);
  const currentSelection = { ...selection, attachments: [...selection.attachments, ...attachments] };
  function changeDraft(value: string) { setDraft(value); writePrivate(`draft.${projectId}`, value); }
  function changeAttachments(value: Attachment[]) { setAttachments(value); writePrivate(`attachments.${projectId}`, value); }
  function closePanel() {
    setComposerPanel("message");
    requestAnimationFrame(() => messageRef.current?.focus());
  }
  function togglePanel(panel: "jobs" | "context") {
    if (composerPanel === panel) closePanel();
    else setComposerPanel(panel);
  }
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
    if (preparingLatch.current || composer.busy || composer.pending || uploads.length) return;
    preparingLatch.current = true; setPreparing(true); setError("");
    const text = draft;
    const capturedAttachments = structuredClone(attachments);
    const submittedIds = new Set(capturedAttachments.map(a => a.artifact_id));
    try {
      const captured = prepareContext ? await prepareContext() : structuredClone(selection);
      const context = { ...captured, attachments: [...captured.attachments, ...capturedAttachments] };
      const ok = await composer.submit(text, context, mode, mode === "steer" ? targetWork : null, snapshot?.settings.default_profile_id ?? null);
      if (ok) {
        if (readPrivate<string>(`draft.${projectId}`) === text) changeDraft("");
        setAttachments(old => { const next = old.filter(a => !submittedIds.has(a.artifact_id)); writePrivate(`attachments.${projectId}`, next); return next; });
      }
    } catch (e) { setError(describeError(e)); }
    finally { preparingLatch.current = false; setPreparing(false); }
  }
  async function resume(original?: ResumeCommand) {
    if (!snapshot || resuming) return;
    setResuming(true); setError("");
    try {
      const body = original ?? resumeCommand(snapshot, events, resumeMode, chosenResumeWork?.work_id ?? "", id());
      await durableCommand<ResumeCommand, unknown>(`resume.${projectId}`, () => body, command => api.call("resumeProjectWork", { path: { project_id: projectId }, body: command, idempotencyKey: command.operation_id }));
      setPendingResume(null);
      await onRefresh();
    } catch (e) { setError(describeError(e)); }
    finally { setPendingResume(readPrivate(`command.resume.${projectId}`)); setResuming(false); }
  }
  return <div className="chat-panel"><div className="chat-scroll" ref={scrollRef} onScroll={onScroll}><div className="chat-scroll-content" ref={contentRef}>
    <div className="copilot-identity"><span className="copilot-mark"><MessageSquare size={16} /></span><div><strong>Project Copilot</strong><small title={work?.work_id}>{work ? work.state.replaceAll("_", " ") : "No active work"}</small></div></div>
    {work?.state === "awaiting_resume" && requests.some(r => r.receipt.work_id === work.work_id && r.receipt.detail_code === "native_turn_aborted") && <p className="run-error" role="status">Copilot interrupted this turn. Review the latest failure, then use Resume to continue the original work.</p>}
    {snapshot?.pending_decisions.map(decision => <DecisionCard key={`${decision.decision_id}.${decision.state_revision}`} api={api} decision={decision} onRefresh={onRefresh} />)}
    {(snapshot?.project.state === "awaiting_resume" || requests.some(r => r.receipt.state === "held") || (work && ["stopped", "awaiting_resume", "outcome_uncertain"].includes(work.state))) && <section className="resume-card"><h3>Continue deliberately</h3><p>Held requests stay held until explicitly released. Reconcile inspects known outcomes without sending a new prompt.</p><select aria-label="Resume mode" value={resumeMode} disabled={resuming || !!pendingResume} onChange={e => setResumeMode(e.target.value as ResumeCommand["mode"])}><option value="reconcile_only">Reconcile only</option><option value="continue_work">Continue original work</option><option value="release_held_queue">Release held queue</option></select>{resumeMode === "continue_work" && <label>Original work<select aria-label="Original work to continue" value={chosenResumeWork?.work_id ?? ""} disabled={resuming || !!pendingResume} onChange={event => setResumeWorkId(event.target.value)}><option value="">Select original work</option>{resumable.map(record => <option key={record.work_id} value={record.work_id}>{record.work_id} · {record.state}</option>)}</select></label>}<button disabled={resuming || !!pendingResume || (resumeMode === "continue_work" && !chosenResumeWork)} onClick={() => void resume()}>Resume</button></section>}
    {pendingResume && <section className="retry-card" aria-label="Resume awaiting confirmation"><strong>Resume awaiting confirmation</strong><p>{pendingResume.mode} · {pendingResume.work_id ?? "project"}. The original command and target revision are retained.</p><button disabled={resuming} onClick={() => void resume(pendingResume)}>Reconcile original Resume</button></section>}
    <Conversation api={api} projectId={projectId} requests={requests} events={events} onRefresh={onRefresh} onError={e => setError(describeError(e))} />
  </div></div>
    {!isFollowing && <button className="chat-jump subtle" onClick={jumpToLatest}><ArrowDown size={13} />Jump to latest</button>}
    <div className="composer">
      {(composer.error || error) && <p role="alert">{composer.error || error}</p>}
      {composer.pending && <div className="retry-card"><strong>Request awaiting confirmation</strong><code>{composer.pending.submission.request_id}</code><p>Its original text and context are frozen. Your draft is retained.</p><button disabled={composer.busy} onClick={() => void composer.retry(false)}>Reconcile original request / retry same ID</button></div>}
      {composer.pendingStop && <div className="retry-card"><strong>Stop awaiting confirmation</strong><button disabled={composer.stopping} onClick={() => void composer.retry(true)}>Reconcile original Stop</button></div>}
      {uploads.map(u => <div className="upload-state" key={u.uploadId}><span>{u.name} · {u.busy ? "Uploading and validating…" : u.error}</span>{!u.busy && <><button onClick={() => void upload(u)}>Retry same upload</button><button onClick={() => setUploads(old => old.filter(x => x.uploadId !== u.uploadId))}>Remove</button></>}</div>)}
      {composerPanel === "message" && <><label className="sr-only" htmlFor="message-draft">Message Copilot</label><textarea ref={messageRef} id="message-draft" aria-label="Message Copilot" value={draft} onChange={e => changeDraft(e.target.value)} placeholder="Describe the next iteration…" onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (!uploads.length && !composer.pending) void send(); } }} /></>}
      {composerPanel === "jobs" && <ComposerPanel panelId="composer-jobs" title="Independent jobs" onClose={closePanel}><JobList api={api} projectId={projectId} jobs={jobs} onRefresh={onRefresh} onError={e => setError(describeError(e))} /></ComposerPanel>}
      {composerPanel === "context" && <ComposerPanel panelId="composer-context" title="Next request context" onClose={closePanel}>
        <ContextPreview projectId={projectId} selection={currentSelection} />
        {attachments.map((a, i) => <div className="attachment-controls" key={`${a.artifact_id}.${i}`}><select aria-label={`Image ${i + 1} role`} value={a.role} onChange={e => changeAttachments(attachments.map((x, j) => i === j ? { ...x, role: e.target.value as Attachment["role"] } : x))}>{["reference", "concept", "render", "markup", "mask"].map(role => <option key={role}>{role}</option>)}</select><button aria-label={`Remove image ${i + 1}`} onClick={() => changeAttachments(attachments.filter((_, j) => i !== j))}><X size={13} /></button></div>)}
      </ComposerPanel>}
      <nav className="composer-links" aria-label="Composer panels"><button aria-controls="composer-jobs" aria-expanded={composerPanel === "jobs"} onClick={() => togglePanel("jobs")}>Jobs{jobs.length > 0 && <span>{jobs.length}</span>}</button><button aria-controls="composer-context" aria-expanded={composerPanel === "context"} onClick={() => togglePanel("context")}>Context{currentSelection.attachments.length > 0 && <span>{currentSelection.attachments.length}</span>}</button></nav>
      {mode === "steer" && <label className="steer-target">Active work target<select aria-label="Steer active work target" value={targetWork} onChange={e => setTargetWork(e.target.value)}><option value="">Select active work explicitly</option>{active && <option value={work.work_id}>{work.work_id} · {work.state}</option>}</select><small>Steer corrects active work. It does not undo completed actions.</small></label>}
      <div className="composer-actions"><label className="attach-button" title="Attach PNG, JPEG or WebP"><Paperclip size={15} /><span className="sr-only">Attach images</span><input aria-label="Attach images" type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={e => { const next = [...(e.target.files ?? [])].map(file => ({ uploadId: id(), name: file.name, file, artifact: null, busy: true, error: "" })); setUploads(old => [...old, ...next]); next.forEach(u => void upload(u)); e.target.value = ""; }} /></label>
        <select aria-label="Request mode" value={mode} onChange={e => { setMode(e.target.value as "queue" | "steer"); setTargetWork(""); }}><option value="queue">Queue</option><option value="steer">Steer</option></select>
        <button className="stop" disabled={!active || composer.stopping || !!composer.pendingStop || work?.state === "stop_requested"} onClick={() => work && void composer.stop(work.work_id)}><Square size={11} fill="currentColor" />{composer.stopping || work?.state === "stop_requested" ? "Stop requested" : "Stop"}</button>
        <button className="primary send" aria-label={mode === "queue" ? "Queue message" : "Steer active work"} disabled={!draft.trim() || !!uploads.length || preparing || composer.busy || !!composer.pending || (mode === "steer" && (!active || targetWork !== work.work_id))} onClick={() => void send()}><ArrowUp size={16} /></button>
      </div><small className="composer-hint">Ctrl+Enter to {mode} · Context is captured on submit</small>
    </div>
  </div>;
}
