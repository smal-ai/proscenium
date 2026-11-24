import { useEffect, useRef, useState } from "react";
import { ArrowDown, Paperclip, Square, X, MessageSquare } from "lucide-react";
import type { ApiClient, Artifact, Attachment, Event, ItemRecord, JobRecord, ProjectSnapshot, RequestRecord, ResumeCommand, RetainedArtifactSummary, VersionRef } from "../../api";
import type { ComposerSeedIntent, ContextPreparer, TurnSelection } from "../../api/studio";
import { describeError, durableCommand, id, readPrivate, writePrivate } from "../../app/storage";
import { DecisionCard } from "./DecisionCard";
import { ContextPreview } from "./ContextPreview";
import { Conversation } from "./Conversation";
import type { useRequestComposer } from "./useRequestComposer";
import { awaitResume, heldRequestIds, isIdleAutoReleaseReady, resumableWork, resumeCommand } from "./resume";
import { useChatScroll } from "./useChatScroll";
import { ComposerPanel, JobList, jobsBadgeState } from "./ComposerPanel";
import { AssistantStatus } from "./AssistantStatus";
import { TargetPicker } from "./TargetPicker";
import { cleanTargetContext, contextCount, prepareSubmissionContext, resolveTarget, uniqueVersions, type DraftTarget } from "./target";
import { canNavigateHistory, promptHistory, stepHistory, type HistoryCursor } from "./promptHistory";
import { busyAgent, nativeAgents, type NativeSpecialist } from "./agents";
import { SendControl } from "./SendControl";
import { isRenderPassStreamArtifact, OutputCards } from "./OutputCards";
import { steerWorkId, stopWorkId } from "./controls";
import "./chat.css";

export { AssistantStatus } from "./AssistantStatus";

const pendingExistingError = "Choose a published item for Existing… or switch target mode before sending.";
export const AUTO_RELEASE_VISIBILITY_TIMEOUT_MS = 5_000;

export function shouldShowQueueHold(snapshot: ProjectSnapshot | null, mode: "queue" | "steer", requests: RequestRecord[] = [], events: Event[] = [], autoReleasingRequestId: string | null = null): boolean {
  if (mode !== "queue" || !snapshot || isIdleAutoReleaseReady(snapshot, requests, events, autoReleasingRequestId)) return false;
  const execution = snapshot.execution;
  return (execution ? (Boolean(execution.fenced) || execution.queue_released === false) : snapshot.project.state === "awaiting_resume")
    || requests.some(r => r.submission.type === "stop" && r.receipt.state === "outcome_uncertain")
    || ["fenced", "awaiting_resume", "queue_paused", "work_stopped", "resume_pending", "uncertain_stop", "stop_requested"].includes(execution?.hold_reason_code ?? "")
    || (!execution?.hold_reason_code && Boolean(execution?.hold_reason) && !["running", "awaiting_user"].includes(snapshot.active_work?.state ?? ""));
}

export function shouldShowResumeCard(snapshot: ProjectSnapshot | null, requests: RequestRecord[], resumableCount: number, uncertainStop: boolean, autoReleasingRequestId: string | null = null): boolean {
  if (!snapshot) return false;
  const work = snapshot.active_work;
  const execution = snapshot.execution;
  const reason = execution?.hold_reason_code;
  if (uncertainStop || resumableCount > 0 || (work && ["stopped", "awaiting_resume", "outcome_uncertain", "failed"].includes(work.state))) return true;
  if (["queue_paused", "work_stopped", "resume_pending"].includes(reason ?? "") || (execution?.queue_released === false && !execution.fenced)) return true;
  if (execution?.fenced) {
    if (work && ["running", "awaiting_user"].includes(work.state)) return true;
    if (reason && !["awaiting_resume", "stop_requested", "active_work_running", "awaiting_user"].includes(reason)) return true;
  }
  return Boolean((execution ? Boolean(execution.fenced) : snapshot.project.state === "awaiting_resume")
    && work?.state !== "stop_requested"
    && [...heldRequestIds(snapshot, requests)].some(id => id !== autoReleasingRequestId));
}

interface Upload { uploadId: string; name: string; file: File; artifact: Artifact | null; busy: boolean; error: string }
export interface ChatPanelProps {
  api: ApiClient; projectId: string; snapshot: ProjectSnapshot | null; requests: RequestRecord[]; events: Event[]; jobs: JobRecord[]; selection: TurnSelection;
  composer: ReturnType<typeof useRequestComposer>; onRefresh: () => Promise<void>;
  prepareContext?: ContextPreparer;
  items?: ItemRecord[]; retainedArtifacts?: RetainedArtifactSummary[]; seedIntent?: ComposerSeedIntent | null;
  hydrated?: boolean; connection?: string; showHeaderStatus?: boolean; onInspectArtifact?: (artifact: Artifact) => void;
}
export function ChatPanel({ api, projectId, snapshot, requests, events, jobs, selection, prepareContext, composer, onRefresh, items, retainedArtifacts = [], seedIntent, hydrated = true, connection = "Connected", showHeaderStatus = true, onInspectArtifact }: ChatPanelProps) {
  const [draft, setDraft] = useState(() => readPrivate<string>(`draft.${projectId}`) ?? "");
  const [attachments, setAttachments] = useState<Attachment[]>(() => readPrivate<Attachment[]>(`attachments.${projectId}`) ?? []);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [mode, setMode] = useState<"queue" | "steer">("queue");
  const [draftTarget, setDraftTarget] = useState<DraftTarget>(() => readPrivate<DraftTarget>(`target.${projectId}`) ?? "current_view");
  const [pendingExisting, setPendingExisting] = useState(false);
  const [references, setReferences] = useState<VersionRef[]>(() => readPrivate<VersionRef[]>(`ref_versions.${projectId}`) ?? []);
  const [includeCurrentViewAsReference, setIncludeCurrentViewAsReference] = useState(() => readPrivate<boolean>(`include_current_view_ref.${projectId}`) ?? false);
  const [targetWork, setTargetWork] = useState("");
  const [resumeWorkId, setResumeWorkId] = useState("");
  const [pendingResume, setPendingResume] = useState<ResumeCommand | null>(() => readPrivate(`command.resume.${projectId}`));
  const [autoRelease, setAutoRelease] = useState<{ phase: "submitting" } | { phase: "held"; requestId: string } | null>(null);
  const [error, setError] = useState("");
  const [resuming, setResuming] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const preparingLatch = useRef(false);
  const messageRef = useRef<HTMLTextAreaElement>(null);
  const [composerPanel, setComposerPanel] = useState<"message" | "jobs" | "context" | "agents">("message");
  const [targetOpen, setTargetOpen] = useState(false);
  const caret = useRef({ start: 0, end: 0 });
  const historyCursor = useRef<HistoryCursor | null>(null);
  const seenSeeds = useRef(new Set<string>());
  const snapshotRef = useRef(snapshot); snapshotRef.current = snapshot;
  const agentsRef = useRef<NativeSpecialist[]>([]);
  const { scrollRef, contentRef, isFollowing, onScroll, jumpToLatest } = useChatScroll(projectId, requests, events);
  const work = snapshot?.active_work;
  const canSteerWorkId = steerWorkId(snapshot);
  const canStopWorkId = stopWorkId(snapshot);
  const uncertainStop = requests.some(record => record.submission.type === "stop" && record.receipt.state === "outcome_uncertain") || snapshot?.execution?.hold_reason_code === "uncertain_stop";
  const stale = connection !== "Connected" || !hydrated;
  const jobsBadge = jobsBadgeState(jobs, stale);
  const agents = nativeAgents(snapshot, events, agentsRef.current);
  agentsRef.current = agents;
  const history = promptHistory(projectId, requests);
  const catalogue = items ?? snapshot?.items ?? [];
  const summaryById = new Map(retainedArtifacts.map(summary => [summary.artifact.artifact_id, summary]));
  const renderedOutputIds = new Set(
    events
      .filter(event => event.event_type.startsWith("tool.") || event.event_type.startsWith("subagent."))
      .flatMap(event => (event.payload as unknown as { artifact_ids: string[] }).artifact_ids ?? [])
      .filter(artifactId => {
        const summary = summaryById.get(artifactId);
        return !summary || !isRenderPassStreamArtifact(summary);
      }),
  );
  for (const summary of retainedArtifacts) {
    if (!isRenderPassStreamArtifact(summary) && (events.some(event => (event.event_type.startsWith("tool.") || event.event_type.startsWith("subagent.")) && (event.payload as unknown as { invocation_id: string }).invocation_id === summary.invocation_id) || (!summary.invocation_id && summary.published_versions.length === 0 && requests.some(record => record.request_id === summary.request_id || (record.receipt.work_id && record.receipt.work_id === summary.work_id))))) renderedOutputIds.add(summary.artifact.artifact_id);
  }
  const resumable = resumableWork(snapshot, events);
  const suppressingAutoRelease = autoRelease !== null;
  const autoReleasingRequestId = autoRelease?.phase === "held" ? autoRelease.requestId
    : autoRelease?.phase === "submitting" ? composer.pending?.submission.request_id ?? null : null;
  const statusProps = { snapshot, requests, events, jobs, hydrated, connection, submitting: composer.busy || preparing, stopping: composer.stopping, autoReleasingRequestId };
  useEffect(() => {
    if (!suppressingAutoRelease) return;
    // Absence alone is not settlement: submit() can return before the list refresh arrives.
    if (snapshot?.project.project_id !== projectId || snapshot.project.state === "inactive" || !snapshot.execution?.fenced
      || (snapshot.execution.hold_reason_code ?? "awaiting_resume") !== "awaiting_resume"
      || (autoReleasingRequestId !== null && (
        [...heldRequestIds(snapshot, requests)].some(id => id !== autoReleasingRequestId)
        || requests.some(request => request.request_id === autoReleasingRequestId && request.receipt.state !== "held")
      ))) {
      setAutoRelease(null);
    }
  }, [suppressingAutoRelease, autoReleasingRequestId, projectId, snapshot, requests]);
  useEffect(() => {
    if (!suppressingAutoRelease) return;
    // The deadline covers both the submission and the held receipt, even if the ACK never arrives.
    const timeout = window.setTimeout(() => setAutoRelease(null), AUTO_RELEASE_VISIBILITY_TIMEOUT_MS);
    return () => window.clearTimeout(timeout);
  }, [suppressingAutoRelease]);
  const chosenResumeWork = resumable.length === 1 ? resumable[0] : resumable.find(record => record.work_id === (resumeWorkId || work?.work_id));
  const canReleaseQueue = !uncertainStop && (!work || ["completed", "failed", "stopped"].includes(work.state));
  const [resumeMode, setResumeMode] = useState<ResumeCommand["mode"]>(() => uncertainStop ? "reconcile_only" : work?.state === "awaiting_resume" && chosenResumeWork ? "continue_work" : canReleaseQueue ? "release_held_queue" : "reconcile_only");
  const recoveryStateKey = `${projectId}:${uncertainStop}:${work?.work_id ?? ""}:${work?.state ?? ""}:${Boolean(snapshot?.execution?.fenced)}:${snapshot?.execution?.hold_reason_code ?? ""}`;
  useEffect(() => {
    if (uncertainStop) setResumeMode("reconcile_only");
    else if (work?.state === "awaiting_resume" && chosenResumeWork) setResumeMode("continue_work");
    else if (canReleaseQueue) setResumeMode("release_held_queue");
  }, [recoveryStateKey, Boolean(chosenResumeWork)]);
  const resolvedTarget = resolveTarget(draftTarget, selection, catalogue);
  const effectiveReferences = uniqueVersions([...references, ...(includeCurrentViewAsReference && selection.item_id && selection.version_id ? [{ item_id: selection.item_id, version_id: selection.version_id }] : [])]);
  const currentSelection: TurnSelection = pendingExisting
    ? { ...cleanTargetContext({ kind: "project" }, effectiveReferences, attachments), request_target: undefined }
    : draftTarget === "current_view" && (selection.version_id || selection.annotations.length || selection.attachments.length)
      ? { ...selection, request_target: resolvedTarget, reference_versions: effectiveReferences, attachments: [...selection.attachments, ...attachments] }
      : cleanTargetContext(resolvedTarget, effectiveReferences, attachments);
  function changeDraft(value: string) { setDraft(value); writePrivate(`draft.${projectId}`, value); }
  function changeTarget(target: DraftTarget) {
    setPendingExisting(false);
    setError(previous => previous === pendingExistingError ? "" : previous);
    setDraftTarget(target);
    writePrivate(`target.${projectId}`, target);
  }
  function changeReferences(versions: VersionRef[]) { setReferences(versions); writePrivate(`ref_versions.${projectId}`, versions); }
  function changeIncludeView(include: boolean) { setIncludeCurrentViewAsReference(include); writePrivate(`include_current_view_ref.${projectId}`, include); }
  function restoreMessageCaret(focus: boolean) {
    requestAnimationFrame(() => {
      const input = messageRef.current;
      if (!input) return;
      if (focus) input.focus();
      input.setSelectionRange(caret.current.start, caret.current.end);
    });
  }
  function focusMessage() { restoreMessageCaret(true); }
  function rememberCaret() {
    if (messageRef.current) caret.current = { start: messageRef.current.selectionStart, end: messageRef.current.selectionEnd };
  }
  function openTargetPicker() {
    const remountingMessage = !messageRef.current;
    rememberCaret();
    setComposerPanel("message");
    setTargetOpen(true);
    if (remountingMessage) restoreMessageCaret(false);
  }
  function closeTargetPicker() {
    setTargetOpen(false);
    focusMessage();
  }
  function starter(kind: "asset" | "scene" | "storyboard") {
    changeTarget({ kind: "new_item", item_kind: kind, intent_id: id() });
    if (!draft.trim()) {
      const text = kind === "storyboard" ? "Plan a storyboard for " : kind === "asset" ? "Create an asset: " : "Build a scene: ";
      changeDraft(text); caret.current = { start: text.length, end: text.length };
    }
    setTargetOpen(false); setComposerPanel("message"); focusMessage();
  }
  useEffect(() => {
    if (!seedIntent || seenSeeds.current.has(seedIntent.nonce)) return;
    seenSeeds.current.add(seedIntent.nonce);
    changeTarget(structuredClone(seedIntent.target));
    if (!draft.trim() && seedIntent.templateText) { changeDraft(seedIntent.templateText); caret.current = { start: seedIntent.templateText.length, end: seedIntent.templateText.length }; }
    setTargetOpen(false); setComposerPanel("message"); focusMessage();
  }, [seedIntent, projectId]);
  function changeAttachments(value: Attachment[]) { setAttachments(value); writePrivate(`attachments.${projectId}`, value); }
  function closePanel() {
    setComposerPanel("message");
    focusMessage();
  }
  function togglePanel(panel: "jobs" | "context" | "agents") {
    rememberCaret();
    setTargetOpen(false);
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
    if (pendingExisting) { setError(pendingExistingError); return; }
    if (preparingLatch.current || composer.busy || composer.pending || uploads.length || !draft.trim()) return;
    if (mode === "steer" && (!canSteerWorkId || targetWork !== canSteerWorkId)) { setError("Steer is unavailable for this work. Switch to Queue deliberately to send a new request."); return; }
    preparingLatch.current = true; setPreparing(true); setError("");
    const text = draft;
    const submittedMode = mode;
    const autoReleasing = submittedMode === "queue" && isIdleAutoReleaseReady(snapshot, requests, events);
    const submittedWorkId = targetWork;
    const submittedTargetMode = draftTarget;
    const capturedSelection = structuredClone(selection);
    const capturedReferences = structuredClone(effectiveReferences);
    const profileId = snapshot?.settings.default_profile_id ?? null;
    const capturedAttachments = structuredClone(attachments);
    const submittedIds = new Set(capturedAttachments.map(a => a.artifact_id));
    try {
      const context = await prepareSubmissionContext({ target: submittedTargetMode, selection: capturedSelection, references: capturedReferences, attachments: capturedAttachments, items: catalogue }, prepareContext);
      if (submittedMode === "steer" && steerWorkId(snapshotRef.current) !== submittedWorkId) throw new Error("Work is no longer steerable. Your Steer mode and draft are retained. Switch to Queue deliberately.");
      if (autoReleasing) setAutoRelease({ phase: "submitting" });
      const submitted = await composer.submit(text, context, submittedMode, submittedMode === "steer" ? submittedWorkId : null, profileId);
      setAutoRelease(current => current?.phase !== "submitting" ? current
        : submitted?.receipt.state === "held" ? { phase: "held", requestId: submitted.request_id } : null);
      if (submitted) {
        if (readPrivate<string>(`draft.${projectId}`) === text) changeDraft("");
        setAttachments(old => { const next = old.filter(a => !submittedIds.has(a.artifact_id)); writePrivate(`attachments.${projectId}`, next); return next; });
      }
    } catch (e) { setAutoRelease(current => current?.phase === "submitting" ? null : current); setError(describeError(e)); }
    finally { preparingLatch.current = false; setPreparing(false); }
  }
  async function resume(original?: ResumeCommand) {
    if (!snapshot || resuming) return;
    setResuming(true); setError("");
    try {
      const body = original ?? resumeCommand(snapshot, events, resumeMode, chosenResumeWork?.work_id ?? "", id());
      setPendingResume(body);
      const result = await durableCommand(`resume.${projectId}`, () => body, async command => {
        await api.call("resumeProjectWork", { path: { project_id: projectId }, body: command, idempotencyKey: command.operation_id });
        return awaitResume(api, projectId, command.operation_id);
      });
      if (result?.state === "outcome_uncertain") {
        const detail = result.detail_code ? ` · ${result.detail_code}` : "";
        setError(body.mode === "reconcile_only" ? `Reconciliation could not clear recovery hold${detail}.` : `Resume needs recovery${detail}. Use Reconcile only before continuing original work.`);
      }
      setPendingResume(null);
      await onRefresh();
    } catch (e) { setError(describeError(e)); await onRefresh(); }
    finally { setPendingResume(readPrivate(`command.resume.${projectId}`)); setResuming(false); }
  }
  return <div className="chat-panel"><div className="chat-scroll" ref={scrollRef} onScroll={onScroll}><div className="chat-scroll-content" ref={contentRef}>
    <div className="copilot-identity"><span className="copilot-mark"><MessageSquare size={16} /></span><div><strong>Project Copilot</strong><small title={work?.work_id}>{work ? work.state.replaceAll("_", " ") : "No active work"}</small></div>{showHeaderStatus && <AssistantStatus {...statusProps} />}</div>
    {snapshot?.execution?.work_outputs.filter(output => ["publication_blocked", "retained_unpublished"].includes(output.status)).map(output => {
      const unrenderedIds = output.artifact_ids.filter(id => {
        const summary = summaryById.get(id);
        return !renderedOutputIds.has(id) && (!summary || !isRenderPassStreamArtifact(summary));
      });
      const cards = <OutputCards api={api} projectId={projectId} artifactIds={unrenderedIds} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} />;
      if (output.status === "publication_blocked") return <section className="publication-status" key={output.work_id} role="status"><strong>Finished · publication blocked</strong><p>{output.summary || output.reason_code || "Retained outputs remain accessible. No catalogue publication is confirmed."}</p>{cards}</section>;
      return unrenderedIds.length > 0 ? <div className="retained-turn-outputs" key={output.work_id}>{cards}</div> : null;
    })}
    {work?.state === "awaiting_resume" && requests.some(r => r.receipt.work_id === work.work_id && r.receipt.detail_code === "native_turn_aborted") && <p className="run-error" role="status">Copilot interrupted this turn. Review the latest failure, then use Resume to continue the original work.</p>}
    <Conversation api={api} projectId={projectId} requests={requests} events={events} onRefresh={onRefresh} onError={e => setError(describeError(e))} onStarter={starter} items={catalogue} retainedArtifacts={retainedArtifacts} onInspectArtifact={onInspectArtifact} />
  </div></div>
    {!isFollowing && <button className="chat-jump subtle" onClick={jumpToLatest}><ArrowDown size={13} />Jump to latest</button>}
    <div className="chat-recovery">
      {snapshot?.pending_decisions.map(decision => <DecisionCard key={`${decision.decision_id}.${decision.state_revision}`} api={api} decision={decision} onRefresh={onRefresh} />)}
      {shouldShowResumeCard(snapshot, requests, resumable.length, uncertainStop, autoReleasingRequestId) && <section className="resume-card" aria-label="Resume Copilot">
        <h3>Resume Copilot</h3>
        <p>Choose how to continue. Reconcile only checks known outcomes without sending a prompt.</p>
        <div className="resume-controls"><select aria-label="Resume mode" value={resumeMode} disabled={resuming || !!pendingResume} onChange={e => setResumeMode(e.target.value as ResumeCommand["mode"])}><option value="reconcile_only">Reconcile only</option><option value="continue_work" disabled={!resumable.length || uncertainStop}>Continue original work</option><option value="release_held_queue" disabled={!canReleaseQueue}>Release held queue</option></select><button disabled={resuming || !!pendingResume || (resumeMode === "continue_work" && !chosenResumeWork) || (resumeMode === "release_held_queue" && !canReleaseQueue)} onClick={() => void resume()}>Resume</button></div>
        {resumeMode === "continue_work" && resumable.length > 1 && <label>Original work<select aria-label="Original work to continue" value={chosenResumeWork?.work_id ?? ""} disabled={resuming || !!pendingResume} onChange={event => setResumeWorkId(event.target.value)}><option value="">Select original work</option>{resumable.map(record => <option key={record.work_id} value={record.work_id}>{record.work_id} · {record.state}</option>)}</select></label>}
      </section>}
      {pendingResume && <section className="retry-card" aria-label="Resume awaiting confirmation"><strong>Resume awaiting confirmation</strong><p>{pendingResume.mode} · {pendingResume.work_id ?? "project"}. The original command and target revision are retained.</p><button disabled={resuming} onClick={() => void resume(pendingResume)}>Reconcile original Resume</button></section>}
    </div>
    <div className="composer" onKeyDown={event => {
      if (targetOpen && event.key === "Escape" && !event.nativeEvent.isComposing && !event.defaultPrevented) closeTargetPicker();
    }}>
      {(composer.error || error) && <p role="alert">{composer.error || error}</p>}
      {composer.pending && <div className="retry-card"><strong>Request awaiting confirmation</strong><code>{composer.pending.submission.request_id}</code><p>Its original text and context are frozen. Your draft is retained.</p><button disabled={composer.busy} onClick={() => void composer.retry(false)}>Reconcile original request / retry same ID</button></div>}
      {composer.pendingStop && <div className="retry-card"><strong>Stop awaiting confirmation</strong>{uncertainStop ? <p>The original Stop needs reconciliation through Resume → Reconcile only. Do not send another Stop.</p> : <button disabled={composer.stopping} onClick={() => void composer.retry(true)}>Reconcile original Stop receipt</button>}</div>}
      {uncertainStop && <p className="publication-status" role="status">The original Stop has an uncertain outcome. Use Resume → Reconcile only before continuing. No duplicate Stop is available.</p>}
      {shouldShowQueueHold(snapshot, mode, requests, events, autoReleasingRequestId) && <p className="queue-hold" role="status">Queue will be held until resumed or released. {snapshot?.execution?.hold_reason || snapshot?.execution?.fence_reason || "Execution awaits explicit Resume."}</p>}
      <TargetPicker api={api} projectId={projectId} items={catalogue} selection={selection} target={draftTarget} references={references} includeCurrentViewAsReference={includeCurrentViewAsReference} onTarget={changeTarget} onReferences={changeReferences} onIncludeView={changeIncludeView} pendingExisting={pendingExisting} onPendingExistingChange={setPendingExisting} open={targetOpen} onOpenChange={next => next ? openTargetPicker() : closeTargetPicker()} />
      {uploads.map(u => <div className="upload-state" key={u.uploadId}><span>{u.name} · {u.busy ? "Uploading and validating…" : u.error}</span>{!u.busy && <><button onClick={() => void upload(u)}>Retry same upload</button><button onClick={() => setUploads(old => old.filter(x => x.uploadId !== u.uploadId))}>Remove</button></>}</div>)}
      {composerPanel === "message" && <><label className="sr-only" htmlFor="message-draft">Message Copilot</label><textarea ref={messageRef} id="message-draft" aria-label="Message Copilot" value={draft} onChange={e => { historyCursor.current = null; changeDraft(e.target.value); }} onCompositionStart={() => { historyCursor.current = null; }} onClick={() => { historyCursor.current = null; }} onSelect={e => { caret.current = { start: e.currentTarget.selectionStart, end: e.currentTarget.selectionEnd }; }} placeholder="Describe the next iteration…" onKeyDown={e => {
        if (e.nativeEvent.isComposing) return;
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key) || ((e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) && ["ArrowUp", "ArrowDown"].includes(e.key))) historyCursor.current = null;
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void send(); return; }
        if (canNavigateHistory({ key: e.key, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: e.altKey, shiftKey: e.shiftKey, isComposing: e.nativeEvent.isComposing }, draft, e.currentTarget.selectionStart, e.currentTarget.selectionEnd, historyCursor.current)) {
          const next = stepHistory(history, historyCursor.current, draft, e.key === "ArrowUp" ? "older" : "newer");
          if (next.text !== draft || next.cursor) { e.preventDefault(); historyCursor.current = next.cursor; changeDraft(next.text); caret.current = { start: next.text.length, end: next.text.length }; focusMessage(); }
        }
      }} /></>}
      {composerPanel === "jobs" && <ComposerPanel panelId="composer-jobs" title="Independent jobs" onClose={closePanel}><JobList api={api} projectId={projectId} jobs={jobs} items={catalogue} onRefresh={onRefresh} onError={e => setError(describeError(e))} /></ComposerPanel>}
      {composerPanel === "context" && <ComposerPanel panelId="composer-context" title="Next request context" onClose={closePanel}>
        <ContextPreview projectId={projectId} selection={currentSelection} items={catalogue} />
        {references.map(ref => <div className="reference-version" key={`${ref.item_id}:${ref.version_id}`}><code>{ref.item_id} · {ref.version_id}</code><button aria-label={`Remove reference version ${ref.version_id}`} onClick={() => changeReferences(references.filter(value => value.item_id !== ref.item_id || value.version_id !== ref.version_id))}>Remove reference</button></div>)}
        {attachments.map((a, i) => <div className="attachment-controls" key={`${a.artifact_id}.${i}`}><select aria-label={`Image ${i + 1} role`} value={a.role} onChange={e => changeAttachments(attachments.map((x, j) => i === j ? { ...x, role: e.target.value as Attachment["role"] } : x))}>{["reference", "concept", "render", "markup", "mask"].map(role => <option key={role}>{role}</option>)}</select><button aria-label={`Remove image ${i + 1}`} onClick={() => changeAttachments(attachments.filter((_, j) => i !== j))}><X size={13} /></button></div>)}
      </ComposerPanel>}
      {composerPanel === "agents" && <ComposerPanel panelId="composer-agents" title="Native specialists" onClose={closePanel}>{agents.map(agent => <div className="native-agent-row" key={agent.activity_key}><strong>{agent.display_name || agent.name}</strong><small>{agent.state.replaceAll("_", " ")} · {agent.state === "outcome_uncertain" ? "uncertain" : stale ? "last known" : agent.freshness.replaceAll("_", " ")}</small><small>Model: {agent.model_id ?? "not reported"}</small></div>)}{!agents.length && <small>{stale ? "Native specialist activity is not confirmed while reconnecting." : "No native specialists reported."}</small>}</ComposerPanel>}
      <nav className="composer-links" aria-label="Composer panels">
        <button className={`${jobsBadge.tone}-jobs`} aria-label={jobsBadge.ariaLabel} aria-controls="composer-jobs" aria-expanded={composerPanel === "jobs"} onClick={() => togglePanel("jobs")}>Jobs{["active", "attention"].includes(jobsBadge.tone) && <span className="jobs-indicator" aria-hidden="true" />}<span className={`composer-count${jobsBadge.tone === "empty" ? " empty" : ""}`}>{jobsBadge.text}</span></button>
        {([{ panel: "context", label: "Context", count: contextCount(currentSelection), uncertain: preparing }, { panel: "agents", label: "Agents", count: agents.filter(busyAgent).length, uncertain: agents.some(agent => busyAgent(agent) && agent.freshness !== "live") }] as const).map(link => <button key={link.panel} aria-controls={`composer-${link.panel}`} aria-expanded={composerPanel === link.panel} onClick={() => togglePanel(link.panel)}>{link.label}<span className={`composer-count${link.count === 0 ? " empty" : ""}`} aria-label={`${link.count}${stale || link.uncertain ? " last known or uncertain" : ""}`}>{link.count}{stale || link.uncertain ? "?" : ""}</span>{link.panel === "context" && uploads.some(upload => upload.busy) && <small>+{uploads.filter(upload => upload.busy).length} uploading</small>}</button>)}
      </nav>
      {mode === "steer" && <label className="steer-target">Active work target<select aria-label="Steer active work target" value={targetWork} onChange={e => setTargetWork(e.target.value)}><option value="">Select active work explicitly</option>{canSteerWorkId && <option value={canSteerWorkId}>{canSteerWorkId} · {work?.state}</option>}{targetWork && targetWork !== canSteerWorkId && <option value={targetWork}>{targetWork} · unavailable</option>}</select><small>Steer corrects active work. It does not undo completed actions.</small></label>}
      {mode === "steer" && (!canSteerWorkId || targetWork !== canSteerWorkId) && <div className="steer-unavailable" role="status"><p>{!canSteerWorkId ? `Steer is unavailable because work is ${work?.state.replaceAll("_", " ") ?? "not active"}${snapshot?.execution?.fenced ? " or execution is paused" : ""}. Your draft and mode are retained.` : "Select the eligible work explicitly before sending Steer."}</p><button onClick={() => setMode("queue")}>Switch to Queue</button></div>}
      <div className="composer-actions"><label className="attach-button" title="Attach PNG, JPEG or WebP"><Paperclip size={15} /><span className="sr-only">Attach images</span><input aria-label="Attach images" type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={e => { const next = [...(e.target.files ?? [])].map(file => ({ uploadId: id(), name: file.name, file, artifact: null, busy: true, error: "" })); setUploads(old => [...old, ...next]); next.forEach(u => void upload(u)); e.target.value = ""; }} /></label>
        <button className="stop" disabled={!canStopWorkId || uncertainStop || composer.stopping || !!composer.pendingStop || work?.state === "stop_requested"} onClick={() => { if (canStopWorkId && stopWorkId(snapshotRef.current) === canStopWorkId) void composer.stop(canStopWorkId); }}><Square size={11} fill="currentColor" />{composer.stopping || work?.state === "stop_requested" ? "Stop requested" : "Stop"}</button>
        <SendControl mode={mode} onMode={setMode} disabled={composerPanel !== "message" || !draft.trim() || !!uploads.length || preparing || composer.busy || !!composer.pending || pendingExisting || (mode === "steer" && (!canSteerWorkId || targetWork !== canSteerWorkId))} onSend={() => void send()} />
      </div><div className="composer-footer"><small className="composer-hint">Ctrl+Enter to {mode} · Context is captured on submit</small><AssistantStatus {...statusProps} /></div>
    </div>
  </div>;
}
