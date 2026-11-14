import { Component, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AnnotationSnapshot, Artifact, Camera, FrameAvailability, FrameMap, FrameRange, FrameReceipt, FrameTarget, PreviewSegment, ProxyMapping } from "../../api/generated/contracts";
import { emptySelection, type ContextPreparer, type ReviewSurfaceProps, type TurnSelection } from "../../api/studio";
import { AnnotationEditor } from "./AnnotationEditor";
import { frameTarget, mediaUrl, sequenceTarget, typingTarget } from "./media";
import { flushDraft, readDrafts, saveDraft, type Draft } from "./outbox";
import { ProgressivePlayer } from "./ProgressivePlayer";
import { SequencePlayer } from "./SequencePlayer";
import { ProxyView } from "./ProxyView";
import { allPages } from "./records";
import { annotationContext } from "./context";
import { useReviewSelection } from "./state";
import "./review.css";

type Mode = "3d_inspection" | "keyframes" | "animation";

class MediaBoundary extends Component<{children: ReactNode}, {error: string}> {
  state = {error: ""};
  static getDerivedStateFromError(error: Error) { return {error: error.message}; }
  render() { return this.state.error ? <p role="alert">media_unavailable: {this.state.error}</p> : this.props.children; }
}

export function ReviewWorkspace(props: ReviewSurfaceProps) {
  // A new explicit version selection resets the surface. Receipt refreshes do not.
  return <Workspace key={`${props.projectId}:${props.version?.version_id ?? "none"}`} {...props}/>;
}

function Workspace({api, projectId, version, jobs, refreshRevision, selection: external, onNavigate, onContextChange, onContextPreparer, onQueue}: ReviewSurfaceProps) {
  const [mode, setMode] = useState<Mode>("keyframes");
  const [frames, setFrames] = useState<FrameReceipt[]>([]);
  const [segments, setSegments] = useState<PreviewSegment[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [mapping, setMapping] = useState<ProxyMapping | null>(null);
  const [frameMap, setFrameMap] = useState<{id: string; map: FrameMap} | null>(null);
  const [availabilityByJob, setAvailabilityByJob] = useState<Record<string,FrameAvailability>>({});
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [target, setTarget] = useState<FrameTarget | null>(null);
  const [captureReady, setCaptureReady] = useState(false);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [targetCamera, setTargetCamera] = useState<Camera | null>(null);
  const [range, setRange] = useState<FrameRange | null>(null);
  const [requestedFrame, setRequestedFrame] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [saved, setSaved] = useState<AnnotationSnapshot[]>([]);
  const [error, setError] = useState("");
  const [queueingView,setQueueingView]=useState(false);
  const initialised = useRef(false);
  const emittedPrimary = useRef("");
  const externalPrimary = JSON.stringify({target: external?.target ?? null, annotation: external?.annotations.map(a => [a.annotation_id,a.saved_revision]) ?? []});
  const choice = useReviewSelection();
  const scope = `${projectId}:${version?.version_id}`;
  const selected = choice.scope === scope ? choice.selected : [];
  const versionJobs = jobs.filter(j => j.target.version?.version_id === version?.version_id);
  const jobsKey = versionJobs.map(j => `${j.job_id}:${j.state_revision}`).join("|");
  const draft = drafts.find(d => d.key === draftId);
  const editorPreparer = useRef<ContextPreparer | null>(null);
  const registerEditorPreparer = useCallback((prepare: ContextPreparer | null) => {editorPreparer.current = prepare;}, []);
  const reviewJobId=selectedJobId ?? target?.render_job_id ?? versionJobs[0]?.job_id;
  const activeJob = jobs.find(j => j.job_id === reviewJobId);
  const currentFrames=useMemo(()=>frames.filter(f=>!reviewJobId||f.job_id===reviewJobId),[frames,reviewJobId]);
  const currentSegments=useMemo(()=>segments.filter(s=>!reviewJobId||s.job_id===reviewJobId),[segments,reviewJobId]);
  const availability=availabilityByJob[reviewJobId ?? ""] ?? null;
  const assembly = version?.kind === "sequence" ? artifacts.find(a => a.kind === "video" && a.state === "ready") : undefined;

  useEffect(() => {
    if (version?.kind === "sequence" && frameMap && target && target.origin !== "sequence_render" && target.source_frame) {
      try {setTarget(sequenceTarget(target.source_frame,target,frameMap.map,frameMap.id));}
      catch (e) {setCaptureReady(false);setError(String(e));}
    }
  },[version?.kind,frameMap,target]);

  useEffect(() => {
    if (!external?.target || externalPrimary === emittedPrimary.current) return;
    initialised.current = true; setPlaying(false); setCaptureReady(false);
    setTarget(structuredClone(external.target)); setTargetCamera(external.camera); setRange(external.range);
    setRequestedFrame(external.target.sequence_frame ?? external.target.source_frame ?? 1);
    setMode(external.view_mode === "animation" ? "animation" : "keyframes");
    choice.select(scope, external.selected_objects);
    const annotation = external.annotations.find(a => a.target.artifact_id === external.target?.artifact_id);
    if (annotation) {
      const restored: Draft = {key: annotation.annotation_id, projectId, annotation: structuredClone(annotation), state: annotation.saved_revision ? "saved" : "browser-local"};
      setDrafts(previous => {
        const retained = previous.find(d => d.key === restored.key);
        // Navigation must never reconstruct an unacknowledged operation from its snapshot.
        const chosen = retained && (retained.state !== "saved" || retained.annotation.saved_revision === restored.annotation.saved_revision) ? retained : restored;
        return [...previous.filter(d => d.key !== restored.key), chosen];
      }); setDraftId(restored.key);
    } else setDraftId(null);
  }, [externalPrimary]);

  useEffect(() => {
    let active = true;
    readDrafts(projectId).then(result => {if (active) setDrafts(result);}).catch(e => {if (active) setError(`Draft recovery failed: ${String(e)}`);});
    return () => {active = false;};
  }, [projectId]);

  useEffect(() => {
    let active = true; let flushing = false;
    async function reconnect() {
      if (flushing) return; flushing = true;
      try {
        const pending = (await readDrafts(projectId)).filter(d => d.state === "failed" && (d.operation || d.markup));
        for (const draft of pending) {
          if (!active) break;
          await flushDraft(api,draft,next => {if (active) setDrafts(previous => [...previous.filter(d => d.key !== next.key),next]);});
        }
      } catch (e) {if (active) setError(`Outbox reconciliation failed: ${String(e)}`);} finally {flushing=false;}
    }
    window.addEventListener("online",reconnect);
    return () => {active=false;window.removeEventListener("online",reconnect);};
  },[api,projectId]);

  useEffect(() => {
    if (!version) return;
    let active = true;
    const errors: string[] = [];
    const captureError = (error: unknown) => { errors.push(error instanceof Error ? error.message : String(error)); };
    async function read() {
      const receipts: FrameReceipt[] = []; const clips: PreviewSegment[] = [];
      const media: Artifact[] = [];
      await Promise.all(versionJobs.map(async job => {
        await Promise.all([
          allPages(cursor => api.call("listFrameReceipts", {path: {project_id: projectId, job_id: job.job_id}, query: {cursor, limit: 100}})).then(page => receipts.push(...page)).catch(captureError),
          allPages(cursor => api.call("listPreviewSegments", {path: {project_id: projectId, job_id: job.job_id}, query: {cursor, limit: 100}})).then(page => clips.push(...page)).catch(captureError),
          api.call("getFrameAvailability", {path: {project_id: projectId, job_id: job.job_id}}).then(value => {if (active) setAvailabilityByJob(previous=>({...previous,[job.job_id]:value}));}).catch(captureError),
        ]);
      }));
      await Promise.all(version!.artifact_ids.map(async artifact_id => {
        try { media.push(await api.call("getArtifact", {path: {project_id: projectId, artifact_id}})); } catch (e) {captureError(e);}
      }));
      const mapArtifact = media.find(a => a.kind === "frame_map" && a.state === "ready");
      if (mapArtifact) {
        try {
          const bytes = await api.call("getArtifactContent", {path: {project_id: projectId, artifact_id: mapArtifact.artifact_id}});
          const map = JSON.parse(new TextDecoder().decode(bytes)) as FrameMap;
          if (map.project_id !== projectId || map.sequence_version_id !== version!.version_id || map.frames.length !== map.frame_count) throw new Error("Frame map target mismatch");
          if (active) setFrameMap({id: mapArtifact.artifact_id, map});
        } catch (e) {captureError(e);}
      }
      if (active) {
        receipts.sort((a,b) => a.source_frame - b.source_frame);
        setFrames(receipts); setSegments(clips); setArtifacts(media);
        if (!initialised.current && (receipts.length || media.some(a => a.kind === "image"))) {
          initialised.current = true;
          if (receipts[0]) {setTarget(frameTarget(receipts[0], version!)); setRequestedFrame(receipts[0].source_frame);}
          else {
            const image = media.find(a => a.kind === "image" && a.dimensions)!;
            setTarget({origin: image.purpose === "concept" ? "concept" : "reference", artifact_id: image.artifact_id,
              displayed_sha256: image.sha256, dimensions: image.dimensions!, item_id: version!.item_id, version_id: version!.version_id,
              shot_id: null, render_job_id: null, source_frame: null, media_artifact_id: null});
          }
        }
        if (errors.length) setError(`Some review data is unavailable: ${[...new Set(errors)].join(". ")}`);
      }
    }
    void read();
    return () => {active = false;};
    // The fingerprint represents pinned version jobs, not array object identity.
  }, [api, projectId, version?.version_id, jobsKey, refreshRevision]);

  useEffect(() => {
    if (!version) return;
    let active = true;
    api.call("getVersionProxyMapping", {path: {project_id: projectId, version_id: version.version_id}}).then(result => {
      if (result.version_id !== version.version_id || result.project_id !== projectId || result.source_closure_digest !== version.source.digest) throw new Error("Proxy mapping does not match pinned source");
      if (active) setMapping(result);
    }).catch(() => {if (active) setMapping(null);});
    allPages(cursor => api.call("listAnnotations", {path: {project_id: projectId}, query: {cursor, limit: 100, version_id: version.version_id}})).then(result => {if (active) setSaved(result);}).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [api, projectId, version, refreshRevision]);

  const present = useCallback((next: FrameTarget) => {
    if (draftId) return;
    if (frameMap && version?.kind === "sequence") {
      try {next = sequenceTarget(next.source_frame!, next, frameMap.map, frameMap.id);} catch {setError("Sequence mapping unavailable for presented frame"); return;}
    }
    setTarget(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    setCaptureReady(true);
  }, [draftId, frameMap, version?.kind]);

  const context: TurnSelection = draft ? {...annotationContext(draft.annotation), annotations: draft.state === "saved" ? [draft.annotation] : [],
    attachments: draft.state === "saved" ? annotationContext(draft.annotation).attachments : [{artifact_id: draft.annotation.target.artifact_id, role: "render"}]} : {...emptySelection(), item_id: version?.item_id ?? null, version_id: version?.version_id ?? null,
    view_mode: mode, target: mode === "3d_inspection" || !captureReady ? null : target,
    camera: mode === "3d_inspection" ? camera : targetCamera, selected_objects: selected,
    range: mode === "3d_inspection" && mapping ? {start: mapping.sample_frame, end: mapping.sample_frame} : range,
    attachments: mode !== "3d_inspection" && target ? [{artifact_id: target.artifact_id, role: target.origin === "concept" ? "concept" : target.origin === "reference" ? "reference" : "render"}] : []};
  const contextKey = JSON.stringify(context);
  const prepareState = useRef({context, draftKey: draft?.key});
  prepareState.current = {context, draftKey: draft?.key};
  // Workspace alone owns the shell registration. The editor delegates internally,
  // so its cleanup can never clear a newer workspace's shell handler.
  useLayoutEffect(() => {
    onContextPreparer?.(async () => {
      if (prepareState.current.draftKey) {
        const prepare = editorPreparer.current;
        if (!prepare) throw new Error("Annotation preparation is not ready");
        return prepare();
      }
      return structuredClone(prepareState.current.context);
    });
    return () => onContextPreparer?.(null);
  }, [onContextPreparer]);
  useEffect(() => {const selection = JSON.parse(contextKey) as TurnSelection; emittedPrimary.current = JSON.stringify({target: selection.target, annotation: selection.annotations.map(a => [a.annotation_id,a.saved_revision])}); onContextChange(selection);}, [contextKey, onContextChange]);

  function step(delta: number, shot = false) {
    setPlaying(false);
    if (shot) {
      const currentIndex=versionJobs.findIndex(j=>j.job_id===reviewJobId);
      const candidates=delta>0?versionJobs.slice(currentIndex+1):versionJobs.slice(0,currentIndex).reverse();
      const next=candidates.find(j=>j.target.shot_id!==activeJob?.target.shot_id);
      if(next) chooseJob(next.job_id);
      return;
    }
    if (!currentFrames.length) return;
    const current = target?.sequence_frame ?? target?.source_frame ?? requestedFrame;
    const candidates = currentFrames.filter(f => delta > 0 ? f.source_frame > current : f.source_frame < current).filter(f => !shot || f.shot_id !== target?.shot_id);
    const ordinal = shot ? (delta > 0 ? candidates[0] : candidates.at(-1))?.source_frame : Math.max(1,current+delta);
    if (!ordinal) return;
    setRequestedFrame(ordinal); setCaptureReady(false);
    const next = currentFrames.find(f => f.source_frame === ordinal);
    if (mode === "keyframes" && version && next) setTarget(frameTarget(next, version));
    else if (!next) setError(`Frame ${ordinal} is pending. Last identified image retained.`);
  }

  function chooseJob(identity:string) {
    setSelectedJobId(identity);setPlaying(false);setCaptureReady(false);setRange(null);setTargetCamera(null);
    const first=frames.find(f=>f.job_id===identity);
    if(first && version){setTarget(frameTarget(first,version));setRequestedFrame(first.source_frame);}
    else setError(`No ready frames for ${identity}`);
  }

  function changeMode(next: Mode) {if (mode !== next) {setCaptureReady(false); setPlaying(false); setMode(next);}}

  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (typingTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey || draftId) return;
      const modes: Record<string, Mode> = {"1": "3d_inspection", "2": "keyframes", "3": "animation"};
      if (modes[event.key]) {event.preventDefault(); changeMode(modes[event.key]);}
      else if (event.code === "Space" && mode === "animation") {event.preventDefault(); setPlaying(p => !p);}
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1, event.shiftKey);}
    }
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  });

  function changeDraft(next: Draft) { setDrafts(previous => [...previous.filter(d => d.key !== next.key), next]); }

  async function annotate() {
    setPlaying(false);
    if (!target || !captureReady || mode === "3d_inspection") return;
    // The target comes from a decoded image or rVFC-confirmed animation presentation.
    await startDraft(target);
  }

  async function startDraft(pinned: FrameTarget) {
    const annotation: AnnotationSnapshot = {annotation_id: crypto.randomUUID(), saved_revision: null,
      target: structuredClone(pinned), range, camera: targetCamera, markups: [], text: "", spatial_hits: [], spatial_status: "unavailable"};
    const next: Draft = {key: annotation.annotation_id, projectId, annotation, state: "browser-local"};
    try {await saveDraft(next); changeDraft(next); setDraftId(next.key);} catch (e) {setError(String(e));}
  }

  async function restore(annotation: AnnotationSnapshot) {
    setPlaying(false); setTarget(annotation.target); setMode(annotation.target.media_artifact_id ? "animation" : "keyframes");
    setRequestedFrame(annotation.target.sequence_frame ?? annotation.target.source_frame ?? 1);
    const local = drafts.find(d => d.key === annotation.annotation_id);
    const next: Draft = local ?? {key: annotation.annotation_id, projectId, annotation, state: "saved"};
    changeDraft(next); setDraftId(next.key);
    try {await saveDraft(next);} catch (e) {setError(String(e)); return;}
    if (onNavigate && annotation.target.version_id && annotation.target.item_id) {
      try {
        const targetVersion = version?.version_id === annotation.target.version_id ? version :
          (await allPages(cursor => api.call("listItemVersions", {path: {project_id: projectId, item_id: annotation.target.item_id!}, query: {cursor, limit: 100}}))).find(v => v.version_id === annotation.target.version_id);
        if (!targetVersion) throw new Error(`Version unavailable: ${annotation.target.version_id}`);
        onNavigate(targetVersion, annotationContext(annotation));
      } catch (e) {setError(String(e));}
    }
  }

  if (!version) return <section className="review-workspace"><p>Select a published version to review.</p></section>;
  return <section className="review-workspace" aria-label="Review workspace">
    <nav className="review-toolbar" aria-label="Review mode">{([['3d_inspection','3D Inspection'],['keyframes','Keyframes'],['animation','Animation']] as const).map(([id,label], index) => <button key={id} disabled={!!draft} aria-pressed={mode === id} onClick={() => changeMode(id)}>{index+1} {label}</button>)}
      <button disabled={!target || !captureReady || !!draft || mode === "3d_inspection"} onClick={() => void annotate()}>Annotate presented frame</button>
      {mode === "3d_inspection" && <button disabled={!camera || queueingView} onClick={async () => {setQueueingView(true);try {await onQueue("Render this view",structuredClone(context));}catch(e){setError(String(e));}finally{setQueueingView(false);}}}>Render this view</button>}
    </nav>
    {draft ? <AnnotationEditor key={draft.key} api={api} draft={draft} onChange={changeDraft} onQueue={onQueue} onContextPreparer={registerEditorPreparer} onClose={() => setDraftId(null)}/>
      : mode === "3d_inspection" ? mapping ? <MediaBoundary key={mapping.proxy_artifact_id}><ProxyView url={mediaUrl(projectId, mapping.proxy_artifact_id)} mapping={mapping} onSelect={pairs => choice.select(scope, pairs)} onCamera={setCamera}/></MediaBoundary> : <p>Inspection proxy unavailable for {version.version_id}. Image-space review remains available.</p>
        : mode === "animation" ? assembly && frameMap ? <SequencePlayer projectId={projectId} artifact={assembly} mapping={frameMap.map} onCapture={async (blob,frame,width,height) => {
          const upload_id=crypto.randomUUID();const capture=await api.call("uploadImage",{path:{project_id:projectId,upload_id},body:blob,idempotencyKey:upload_id});
          const pinned:FrameTarget={origin:"sequence_render",artifact_id:capture.artifact_id,displayed_sha256:capture.sha256,dimensions:{width,height},item_id:version.item_id,version_id:version.version_id,shot_id:null,render_job_id:null,source_frame:null,media_artifact_id:assembly.artifact_id,sequence_version_id:version.version_id,sequence_frame:frame,frame_map_artifact_id:frameMap.id};
          setTarget(pinned);setCaptureReady(true);await startDraft(pinned);
        }}/> : <ProgressivePlayer key={reviewJobId} projectId={projectId} version={version} frames={currentFrames} segments={currentSegments} availability={availability} requestedFrame={requestedFrame} playing={playing} onPlaying={setPlaying} onPresented={present}/>
          : target ? <div className="review-image"><img src={mediaUrl(projectId, target.artifact_id)} alt={`${target.origin}, frame ${target.sequence_frame ?? target.source_frame ?? "image"}`} onLoad={event => {if (event.currentTarget.naturalWidth === target.dimensions.width && event.currentTarget.naturalHeight === target.dimensions.height) setCaptureReady(true); else {setCaptureReady(false); setError("Decoded image dimensions do not match target");}}} onError={() => {setCaptureReady(false); setError(`media_unavailable: ${target.artifact_id}`);}}/></div> : <p>No ready review image. Rendering is an explicit Queue request.</p>}
    <div className="review-toolbar"><button onClick={() => step(-1)}>Previous frame</button><button onClick={() => step(1)}>Next frame</button>
      <label>Shot / render job<select aria-label="Shot / render job" disabled={!!draft} value={reviewJobId ?? ""} onChange={e=>chooseJob(e.target.value)}>{versionJobs.map(job=><option key={job.job_id} value={job.job_id}>{job.target.shot_id ?? "Scene"} · {job.render_spec?.purpose ?? job.kind} · {job.job_id}</option>)}</select></label>
      <label>Frame<input type="number" min="1" value={requestedFrame} disabled={!!draft} onChange={e => {setPlaying(false); setCaptureReady(false); setRequestedFrame(Math.max(1, Number(e.target.value))); if (mode === "keyframes") {const frame = currentFrames.find(f => f.source_frame === Number(e.target.value)); if (frame) setTarget(frameTarget(frame, version)); else setError(`Frame ${e.target.value} is pending. Last identified image retained.`);}}}/></label>
      <span>{target ? `${target.dimensions.width} × ${target.dimensions.height} · ${target.origin} · frame ${target.sequence_frame ?? target.source_frame ?? "image"}` : "No presented target"}</span>
      <span>{activeJob?.render_spec ? `${activeJob.render_spec.engine} · ${activeJob.render_spec.purpose} · ${activeJob.render_spec.samples} samples` : "No render profile reported"}</span>
    </div>
    <details><summary>Saved annotations and local drafts</summary>
      {drafts.map(d => <button key={d.key} onClick={() => restore(d.annotation)}>{d.annotation.text || d.annotation.annotation_id} · {d.state} · {d.annotation.target.version_id}</button>)}
      {saved.filter(a => !drafts.some(d => d.key === a.annotation_id)).map(a => <button key={a.annotation_id} onClick={() => restore(a)}>{a.text || a.annotation_id} · revision {a.saved_revision}</button>)}
    </details>
    <details><summary>Published image artefacts</summary>{artifacts.filter(a => a.kind === "image").map(a => <span key={a.artifact_id}>{a.artifact_id}: {a.state} · {a.purpose} </span>)}</details>
    {error && <p role="status">{error}</p>}
  </section>;
}
