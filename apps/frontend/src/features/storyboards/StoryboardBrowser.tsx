import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { Artifact, FrameReceipt, ItemRecord, SelectionRecord, SelectionSave, ShotPlan, VersionRecord } from "../../api/generated/contracts";
import type { ReviewSurfaceProps, TurnSelection } from "../../api/studio";
import { handleSubmitEnter } from "../chat/controls";
import { dependencyStatus, frameContext, selectionCommand, selectionFor, type PlanFrame, type PlanShot } from "./model";
import "./storyboards.css";

const message = (error: unknown) => error instanceof Error ? error.message : String(error);

async function pages<T>(read: (cursor?: string) => Promise<{ items: T[]; next_cursor: string | null }>): Promise<T[]> {
  const result: T[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await read(cursor);
    result.push(...page.items);
    cursor = page.next_cursor ?? undefined;
    if (cursor && seen.has(cursor)) throw new Error("Catalogue returned a repeated pagination cursor");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return result;
}

export function StoryboardBrowser(props: ReviewSurfaceProps) {
  // Unmounting on a project change fences old requests and pending mutation identities.
  return <ProjectStoryboardBrowser key={props.projectId} {...props} />;
}

function ProjectStoryboardBrowser({ api, projectId, version, selection: external, jobs, refreshRevision, onContextChange, onNavigate, onQueue }: ReviewSurfaceProps) {
  const [items, setItems] = useState<ItemRecord[]>([]);
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [versionId, setVersionId] = useState<string>(version?.kind === "storyboard" ? version.version_id : "");
  const [plan, setPlan] = useState<ShotPlan | null>(null);
  const [records, setRecords] = useState<SelectionRecord[]>([]);
  const [artifacts, setArtifacts] = useState<Record<string, Artifact>>({});
  const [artifactErrors, setArtifactErrors] = useState<Record<string, string>>({});
  const [active, setActive] = useState<{ shotId: string; frameId: string } | null>(null);
  const [candidateId, setCandidateId] = useState("");
  const [candidate, setCandidate] = useState<Artifact | undefined>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [planLoading, setPlanLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [pending, setPending] = useState<SelectionSave | null>(null);
  const [receipts, setReceipts] = useState<FrameReceipt[]>([]);
  const [receiptError, setReceiptError] = useState("");
  const [planningEdit, setPlanningEdit] = useState("");
  const loadEpoch = useRef(0);
  const currentVersion = versions.find(value => value.version_id === versionId);
  const shot = plan?.shots.find(value => value.shot_id === active?.shotId);
  const frame = shot?.frames.find(value => value.frame_id === active?.frameId);
  const record = currentVersion && shot && frame ? selectionFor(records, currentVersion, shot.shot_id, frame.frame_id) : undefined;
  // Resolve receipt context only for artefacts already referenced by this board.
  // A new unrelated render tick must not replay all project frame pages.
  const storyboardArtifacts = new Set([
    ...(plan?.shots.flatMap(shot => shot.frames.flatMap(frame => frame.artifact_id ? [frame.artifact_id] : [])) ?? []),
    ...Object.keys(artifacts),
    ...(candidate ? [candidate.artifact_id] : []),
  ]);
  const receiptJobsKey = JSON.stringify(jobs.filter(job => job.project_id === projectId && job.kind === "render" &&
    (job.target.version?.version_id === versionId || job.artifact_ids.some(id => storyboardArtifacts.has(id))))
    .map(job => [job.job_id, job.artifact_ids.filter(id => storyboardArtifacts.has(id))]));

  useEffect(() => {
    if (version?.kind === "storyboard") setVersionId(version.version_id);
  }, [version?.kind, version?.version_id]);

  useEffect(() => {
    setActive(null);
    setCandidate(undefined);
    setCandidateId("");
    setPlanningEdit("");
  }, [versionId]);

  // The reviewer owns decoding/annotations. Retain the storyboard role when it
  // reports that same target without Attachment.use after decoding the image.
  useEffect(() => {
    if (!external?.target || !currentVersion || !shot || !frame
      || external.version_id !== currentVersion.version_id || external.target.shot_id !== shot.shot_id) return;
    const use = `${shot.shot_id} / ${frame.frame_id} / ${frame.role}: ${frame.composition}`;
    let changed = false;
    const attachments = external.attachments.map(attachment => {
      if (attachment.artifact_id !== external.target!.artifact_id || !["reference", "concept", "render"].includes(attachment.role) || attachment.use === use) return attachment;
      changed = true;
      return { ...attachment, use };
    });
    if (changed) onContextChange({ ...external, attachments });
  }, [external, currentVersion, shot, frame, onContextChange]);

  useEffect(() => {
    const controller = new AbortController();
    setReceiptError("");
    setReceipts([]);
    const receiptJobs = JSON.parse(receiptJobsKey) as [jobId: string, matchedArtifacts: string[]][];
    void Promise.all(receiptJobs.map(([job_id]) =>
      pages(cursor => api.call("listFrameReceipts", { path: { project_id: projectId, job_id }, query: { cursor, limit: 100 }, signal: controller.signal }))
    )).then(result => { if (!controller.signal.aborted) setReceipts(result.flat()); })
      .catch(cause => { if (!controller.signal.aborted) setReceiptError(message(cause)); });
    return () => controller.abort();
  }, [api, projectId, receiptJobsKey, refreshRevision]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void (async () => {
      const path = { project_id: projectId };
      const [catalogue, selections] = await Promise.all([
        pages(cursor => api.call("listItems", { path, query: { cursor, limit: 100 }, signal: controller.signal })),
        pages(cursor => api.call("listSelections", { path, query: { cursor, limit: 100 }, signal: controller.signal })),
      ]);
      const storyboardItems = catalogue.filter(item => item.kind === "storyboard");
      const allVersions = (await Promise.all(storyboardItems.map(item => pages(cursor => api.call("listItemVersions", {
        path: { ...path, item_id: item.item_id }, query: { cursor, limit: 100 }, signal: controller.signal,
      }))))).flat();
      if (controller.signal.aborted) return;
      setItems(catalogue);
      setVersions(allVersions);
      setRecords(selections);
      setVersionId(previous => allVersions.some(value => value.version_id === previous) ? previous
        : storyboardItems.find(item => item.current_version_id)?.current_version_id ?? allVersions[0]?.version_id ?? "");
    })().catch(cause => { if (!controller.signal.aborted) setError(message(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, projectId, refreshRevision, reload]);

  useEffect(() => {
    const controller = new AbortController();
    const epoch = ++loadEpoch.current;
    setPlan(null);
    setArtifacts({});
    setArtifactErrors({});
    if (!versionId) return () => controller.abort();
    setPlanLoading(true);
    void api.call("getVersionShotPlan", { path: { project_id: projectId, version_id: versionId }, signal: controller.signal })
      .then(async next => {
        if (controller.signal.aborted) return;
        setPlan(next);
        const ids = new Set(next.shots.flatMap(value => value.frames.flatMap(value => value.artifact_id ? [value.artifact_id] : [])));
        for (const selection of records) {
          if (selection.slot.kind === "storyboard_frame" && selection.slot.storyboard_version.version_id === versionId && selection.state === "selected" && selection.choice?.kind === "artifact") ids.add(selection.choice.artifact_id);
        }
        await Promise.all([...ids].map(async artifact_id => {
          try {
            const artifact = await api.call("getArtifact", { path: { project_id: projectId, artifact_id }, signal: controller.signal });
            if (!controller.signal.aborted) setArtifacts(previous => ({ ...previous, [artifact_id]: artifact }));
          } catch (cause) {
            if (!controller.signal.aborted) setArtifactErrors(previous => ({ ...previous, [artifact_id]: message(cause) }));
          }
        }));
      }).catch(cause => { if (!controller.signal.aborted) setError(message(cause)); })
      .finally(() => { if (!controller.signal.aborted && epoch === loadEpoch.current) setPlanLoading(false); });
    return () => controller.abort();
  }, [api, projectId, versionId, refreshRevision, records]);

  function displayArtifact(planFrame: PlanFrame, planShot: PlanShot): Artifact | undefined {
    const selected = currentVersion && selectionFor(records, currentVersion, planShot.shot_id, planFrame.frame_id);
    const id = selected?.state === "selected" && selected.choice?.kind === "artifact" ? selected.choice.artifact_id : planFrame.artifact_id;
    return id ? artifacts[id] : undefined;
  }

  function context(planShot: PlanShot, planFrame: PlanFrame, artifact?: Artifact): TurnSelection {
    if (!currentVersion) throw new Error("Storyboard version is not loaded");
    const image = artifact ?? displayArtifact(planFrame, planShot);
    const receipt = receipts.find(value => value.artifact_id === image?.artifact_id);
    const job = receipt && jobs.find(value => value.job_id === receipt.job_id);
    return frameContext(currentVersion, planShot, planFrame, image, receipt && job ? { receipt, job } : undefined);
  }

  function selectFrame(planShot: PlanShot, planFrame: PlanFrame) {
    if (!currentVersion) return;
    setActive({ shotId: planShot.shot_id, frameId: planFrame.frame_id });
    setCandidate(undefined);
    setCandidateId("");
    setPlanningEdit("");
    const selection = context(planShot, planFrame);
    if (onNavigate) onNavigate(currentVersion, selection);
    else onContextChange(selection);
  }

  async function inspectCandidate() {
    const epoch = loadEpoch.current;
    setBusy(true);
    setError("");
    try {
      const result = await api.call("getArtifact", { path: { project_id: projectId, artifact_id: candidateId.trim() } });
      if (epoch !== loadEpoch.current) return;
      if (result.project_id !== projectId || result.state !== "ready" || result.kind !== "image" || !result.dimensions) throw new Error("Choice must be a ready image from the active project with dimensions");
      setCandidate(result);
      setArtifacts(previous => ({ ...previous, [result.artifact_id]: result }));
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }

  async function save(command: SelectionSave) {
    setBusy(true);
    setError("");
    // Keep this exact command after transport uncertainty. Never retry automatically.
    setPending(command);
    try {
      await api.call("saveSelection", { path: { project_id: projectId }, body: command, idempotencyKey: command.operation_id });
      setPending(null);
      setReload(value => value + 1);
    } catch (cause) {
      if (cause instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(cause.status)) {
        setPending(null);
        setReload(value => value + 1);
        setError(`${message(cause)}. Selection rejected. Refresh owner state before choosing again.`);
      } else setError(`${message(cause)}. Refresh to inspect owner state or retry the exact saved operation. No automatic retry.`);
    }
    finally { setBusy(false); }
  }

  async function queue(stage: "planning" | "concepts" | "scene") {
    if (!shot || !frame) return;
    const text = {
      planning: `Revise only storyboard ${plan?.plan_id}, shot ${shot.shot_id}, frame ${frame.frame_id}. Preserve unrelated IDs and report dependency impact. Planning only: no image generation or scene execution.${planningEdit.trim() ? `\n\nOwner planning instructions: ${planningEdit.trim()}` : ""}`,
      concepts: `Generate or revise the concept for storyboard ${plan?.plan_id}, shot ${shot.shot_id}, frame ${frame.frame_id}, using its pinned references and the configured budgeted generation adapter.`,
      scene: `Author the scene and actual camera/timeline bindings for storyboard ${plan?.plan_id}, shot ${shot.shot_id}, preserving pinned dependencies and unrelated work.`,
    }[stage];
    setBusy(true);
    try { await onQueue(text, { ...context(shot, frame), execution_scope: stage === "planning" ? "planning_only" : "default" }); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }

  const chosenArtifact = shot && frame ? candidate ?? displayArtifact(frame, shot) : undefined;
  const currentImage = shot && frame ? displayArtifact(frame, shot) : undefined;
  const catalogueVersionIds = new Set(items.flatMap(item => item.current_version_id ? [item.current_version_id] : []));
  const canSave = Boolean(chosenArtifact?.state === "ready" && chosenArtifact.kind === "image");

  return <section className="sb-browser" aria-label="Storyboard browser">
    <header className="sb-header"><h2>Storyboards</h2><button onClick={() => { setError(""); setReload(value => value + 1); }} disabled={busy}>Refresh</button></header>
    {error && <div role="alert" className="sb-error">{error}</div>}
    {receiptError && <p className="sb-notice">Render receipt context unavailable: {receiptError}</p>}
    {pending && <div className="sb-notice">Selection operation unresolved. <button disabled={busy} onClick={() => void save(pending)}>Retry exact operation</button></div>}
    {loading && <p role="status">Loading catalogue and owner selections…</p>}
    {!loading && !versions.length && !error && <p>No published storyboards. Ask for a plan. Browsing does not build one.</p>}
    <label className="sb-label">Published version
      <select aria-label="Storyboard version" value={versionId} disabled={busy || Boolean(pending)} onChange={event => { setError(""); setVersionId(event.target.value); }}>
        {!versionId && <option value="">Choose a storyboard</option>}
        {versions.map(value => <option key={value.version_id} value={value.version_id}>{value.name} · {value.version_id}</option>)}
      </select>
    </label>
    {currentVersion && <p className="sb-meta">{currentVersion.item_id}<br />Version {currentVersion.version_id}<br />Source {currentVersion.source_commit.slice(0, 12)}</p>}
    {planLoading && <p role="status">Loading plan and image metadata…</p>}
    <div className="sb-scroll">
      {plan && <>
        <h3>Plan {plan.plan_id}</h3>
        {plan.treatment && <p>{plan.treatment}</p>}
        <details><summary>Dependencies ({plan.asset_dependencies.length})</summary>
          {plan.asset_dependencies.map(dep => <p key={`${dep.item_id}/${dep.version_id}`} className="sb-meta">{dep.item_id} / {dep.version_id}
            {items.some(item => item.item_id === dep.item_id && item.current_version_id && item.current_version_id !== dep.version_id) && <strong> Newer catalogue head exists. Pinned version unchanged.</strong>}</p>)}
        </details>
        {plan.unresolved.length > 0 && <details open><summary>Unresolved inputs ({plan.unresolved.length})</summary><ul>{plan.unresolved.map((value, index) => <li key={index}>{value}</li>)}</ul></details>}
        <ol className="sb-shots">{plan.shots.map((planShot, index) => <li key={planShot.shot_id} className="sb-shot">
          <h3>{index + 1}. {planShot.shot_id}</h3><p>{planShot.intent}</p>
          <details open><summary>Camera, motion and timing</summary>
            <p>{planShot.camera_intent || "Camera intent unresolved"}</p>
            <ul>{planShot.motion_beats.map((beat, beatIndex) => <li key={beatIndex}>{beat}</li>)}</ul>
            <p className="sb-meta">Scene: {planShot.scene_version ? `${planShot.scene_version.item_id} / ${planShot.scene_version.version_id}` : "Unbound"}</p>
            <p className="sb-meta">Source range: {planShot.source_range ? `${planShot.source_range.start}–${planShot.source_range.end} inclusive` : "Not authored"}<br />FPS: {planShot.fps ? `${planShot.fps.numerator}/${planShot.fps.denominator}` : "Not authored"}</p>
          </details>
          <ol className="sb-frames">{planShot.frames.map(planFrame => {
            const image = displayArtifact(planFrame, planShot);
            const owner = currentVersion && selectionFor(records, currentVersion, planShot.shot_id, planFrame.frame_id);
            const ownerSelected = owner?.state === "selected" && owner.choice?.kind === "artifact" && owner.choice.artifact_id === planFrame.artifact_id;
            return <li key={planFrame.frame_id}>
              <button className="sb-frame" aria-pressed={active?.frameId === planFrame.frame_id && active.shotId === planShot.shot_id} disabled={busy || Boolean(pending)} onClick={() => selectFrame(planShot, planFrame)}>
                <strong>{planFrame.role} · {planFrame.frame_id}</strong>
                {image?.state === "ready" && image.kind === "image" ? <img alt={`${planFrame.role}: ${planFrame.composition}`} src={`/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(image.artifact_id)}/content`} onError={event => { event.currentTarget.hidden = true; setArtifactErrors(previous => ({ ...previous, [image.artifact_id]: "Image bytes could not be displayed" })); }} /> : <span className="sb-placeholder">{planFrame.state === "proposed" ? "Proposed · image unavailable" : "Image missing / unbuilt"}</span>}
                <span>{planFrame.composition}</span>
                <span className="sb-meta">Authored: {planFrame.state} · Image: {image?.state ?? "missing"}</span>
                <span className="sb-meta">owner_selected: {ownerSelected ? "yes" : "no"}{owner?.state === "withdrawn" ? " (withdrawn)" : ""}</span>
                {owner?.state === "selected" && owner.choice?.kind === "artifact" && owner.choice.artifact_id !== planFrame.artifact_id && <span className="sb-meta">Owner choice: {owner.choice.artifact_id}. Authored image remains {planFrame.artifact_id ?? "missing"}.</span>}
              </button>
              {image && <p className="sb-meta">{dependencyStatus(image, planShot, catalogueVersionIds)}</p>}
              {planFrame.artifact_id && artifactErrors[planFrame.artifact_id] && <p className="sb-error">{artifactErrors[planFrame.artifact_id]}</p>}
              {image && artifactErrors[image.artifact_id] && image.artifact_id !== planFrame.artifact_id && <p className="sb-error">{artifactErrors[image.artifact_id]}</p>}
            </li>;
          })}</ol>
        </li>)}</ol>
      </>}
    </div>
    {shot && frame && currentVersion && <footer className="sb-actions">
      <strong>{shot.shot_id} / {frame.frame_id}</strong>
      {currentImage && !context(shot, frame, currentImage).target && <p className="sb-notice">Exact render-frame context unavailable. No invented source frame will be sent to review.</p>}
      <label className="sb-label">Alternative image artefact ID<input value={candidateId} disabled={busy || Boolean(pending)} onChange={event => { setCandidateId(event.target.value); setCandidate(undefined); }} /></label>
      <button disabled={busy || Boolean(pending) || !candidateId.trim()} onClick={() => void inspectCandidate()}>Inspect candidate</button>
      {candidate && <p className="sb-meta">Ready {candidate.purpose}: {candidate.artifact_id}</p>}
      <div className="sb-buttons"><button disabled={busy || Boolean(pending) || !canSave} onClick={() => void save(selectionCommand(projectId, currentVersion, shot.shot_id, frame.frame_id, chosenArtifact!.artifact_id, record))}>{record?.state === "selected" ? "Supersede selection" : "Select image"}</button>
        <button disabled={busy || Boolean(pending) || record?.state !== "selected"} onClick={() => void save(selectionCommand(projectId, currentVersion, shot.shot_id, frame.frame_id, null, record))}>Withdraw</button></div>
      <label className="sb-label">Planning edit instructions<textarea value={planningEdit} disabled={busy || Boolean(pending)} onChange={event => setPlanningEdit(event.target.value)} placeholder="Describe this shot's planning-only change" onKeyDown={e => handleSubmitEnter(e, () => {
        if (!busy && !pending) void queue("planning");
      })} /></label>
      <div className="sb-buttons"><button disabled={busy || Boolean(pending)} onClick={() => void queue("planning")}>Queue plan edit</button><button disabled={busy || Boolean(pending)} onClick={() => void queue("concepts")}>Queue concept</button><button disabled={busy || Boolean(pending)} onClick={() => void queue("scene")}>Queue scene</button></div>
    </footer>}
  </section>;
}
