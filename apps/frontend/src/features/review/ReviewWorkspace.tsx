import { Component, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Bookmark, Camera as CameraIcon, ChevronLeft, ChevronRight, Download, Info, MapPin, Pause, PenLine, Play, X } from "lucide-react";
import type { AnnotationSnapshot, Artifact, Camera, FrameAvailability, FrameMap, FrameRange, FrameReceipt, FrameTarget, Markup, PreviewSegment, ProxyMapping, SpatialHit, VersionRecord } from "../../api/generated/contracts";
import { emptySelection, type ContextPreparer, type ReviewSurfaceProps, type TurnSelection } from "../../api/studio";
import { versionRequestTarget } from "../chat/target";
import { AnnotationEditor } from "./AnnotationEditor";
import { AnnotationsDrawer, draftFromSnapshot, mergeHydratedDrafts, mergeItemAnnotations, resolveAnnotationEntry, type AnnotationEntry } from "./AnnotationsDrawer";
import { frameTarget, mediaUrl, sequenceTarget, stableJson, typingTarget } from "./media";
import { flushDraft, readDrafts, saveDraft, saveDraftEdit, type Draft } from "./outbox";
import { ProgressivePlayer } from "./ProgressivePlayer";
import { SequencePlayer } from "./SequencePlayer";
import { ProxyView, type FrozenProxyCapture, type ProxyCapture, type ProxyPinPlacement, type SpatialPinMarker } from "./ProxyView";
import { readProxyCapture, writeProxyCapture, type PendingProxyCapture } from "./proxyCaptureStore";
import { allPages } from "./records";
import { formatRenderPresetOption, RENDER_PRESETS, resolveRenderTargetDimensions, type RenderPresetId } from "./renderFraming";
import { annotationContext, cleanTargetRole } from "./context";
import { DecodedReviewImage, decodedImageIdentity } from "./DecodedReviewImage";
import { frameCamera } from "./frameCamera";
import { generationOutputs, imageTarget, targetJobId } from "./jobIdentity";
import { canStepKeyframePass, describeKeyframePassStatus, isFrameRendering, resolveDirectKeyframeJump, sortedReadyFrames, stepKeyframePass } from "./keyframeStepping";
import { pinMarkupLabel, sha256Hex, verifySpatialMapping } from "./spatial";
import { SpatialPinDock, SpatialPinFallbackView, SpatialPinHeader, useSpatialPinController } from "./SpatialPinDock";
import { useReviewSelection } from "./state";
import { RetainedReview, ReviewEmpty, ReviewModeSwitcher } from "./ReviewChrome";
import "./review.css";

type Mode = "3d_inspection" | "keyframes" | "animation";
type StoryboardPanel = { shot_id: string; frame_id: string; role: string; composition: string; artifact_id: string };
type StoryboardTarget = StoryboardPanel & { target: FrameTarget };
type UnmappedCapture = FrozenProxyCapture & { clickUv: [number, number] };

function formatTargetOrigin(origin: FrameTarget["origin"]): string {
  if (origin === "proxy_capture") return "3D capture";
  return origin.replaceAll("_", " ").replace(/^./, c => c.toUpperCase());
}

function mergeSavedSnapshots(previous: AnnotationSnapshot[], incoming: readonly AnnotationSnapshot[]) {
  const newest = new Map(previous.map(a => [a.annotation_id, a]));
  for (const annotation of incoming) {
    if ((annotation.saved_revision ?? 0) >= (newest.get(annotation.annotation_id)?.saved_revision ?? 0)) {
      newest.set(annotation.annotation_id, annotation);
    }
  }
  return [...newest.values()];
}

class MediaBoundary extends Component<{children: ReactNode; onError?: (message: string) => void}, {error: string}> {
  state = {error: ""};
  static getDerivedStateFromError(error: Error) { return {error: error.message}; }
  componentDidCatch(error: Error) { this.props.onError?.(error.message); }
  render() { return this.state.error ? <p role="alert">media_unavailable: {this.state.error}</p> : this.props.children; }
}

export function ReviewWorkspace(props: ReviewSurfaceProps) {
  // A new explicit version selection resets the surface. Receipt refreshes do not.
  return <Workspace key={`${props.projectId}:${props.version?.version_id ?? "none"}`} {...props}/>;
}

function Workspace({visible: surfaceVisible = true, api, projectId, version: suppliedVersion, inspectedArtifact, inspectedArtifactSummary, onClearArtifact, jobs, refreshRevision, selection: external, onNavigate, onContextChange, onContextPreparer, onQueue}: ReviewSurfaceProps) {
  const visible = surfaceVisible && !inspectedArtifact;
  const [refreshedVersion, setRefreshedVersion] = useState<VersionRecord | null>(null);
  const [itemVersions, setItemVersions] = useState<VersionRecord[]>([]);
  const version = refreshedVersion ?? suppliedVersion;
  const [mode, setMode] = useState<Mode>("keyframes");
  const [drawer, setDrawer] = useState<"annotations" | "details" | null>(null);
  const [frames, setFrames] = useState<FrameReceipt[]>([]);
  const [segments, setSegments] = useState<PreviewSegment[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [storyboardTargets, setStoryboardTargets] = useState<StoryboardTarget[]>([]);
  const [storyboardPanelIndex, setStoryboardPanelIndex] = useState(0);
  const [mapping, setMapping] = useState<ProxyMapping | null>(null);
  const [pinnedMapping, setPinnedMapping] = useState<ProxyMapping | null>(null);
  const [pinnedMappingError, setPinnedMappingError] = useState<string | null>(null);
  const [proxyLoadError, setProxyLoadError] = useState<string | null>(null);
  const [pinMode, setPinMode] = useState(false);
  const [editing2D, setEditing2D] = useState(false);
  const [restorePinCamera, setRestorePinCamera] = useState<Camera | null>(null);
  const [restorePinNonce, setRestorePinNonce] = useState(0);
  const [unmappedCapture, setUnmappedCapture] = useState<UnmappedCapture | null>(null);
  const [frameMap, setFrameMap] = useState<{id: string; map: FrameMap} | null>(null);
  const [availabilityByJob, setAvailabilityByJob] = useState<Record<string,FrameAvailability>>({});
  const [sequenceJobId, setSequenceJobId] = useState<string | null>(null);
  const [target, setTarget] = useState<FrameTarget | null>(null);
  const [captureReady, setCaptureReady] = useState(false);
  const [decodedImage, setDecodedImage] = useState<string | null>(null);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [targetCamera, setTargetCamera] = useState<Camera | null>(null);
  const [range, setRange] = useState<FrameRange | null>(null);
  const [requestedFrame, setRequestedFrame] = useState(1);
  const [frameInput, setFrameInput] = useState("1");
  const [frameNotice, setFrameNotice] = useState("");
  const framePointerDown = useRef(false);
  const frameInputEdited = useRef(false);
  const [playing, setPlaying] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draftId, setDraftId] = useState<string | null>(null);
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  const draftIdRef = useRef(draftId);
  draftIdRef.current = draftId;
  const placementTokenRef = useRef(0);
  const [placementCommitting, setPlacementCommitting] = useState(false);
  const invalidatePendingPlacement = useCallback(() => {
    placementTokenRef.current += 1;
    setPlacementCommitting(false);
  }, []);
  useLayoutEffect(() => () => { placementTokenRef.current += 1; }, []);
  useLayoutEffect(() => {
    if (!visible) {
      invalidatePendingPlacement();
      setPlaying(false); setCaptureReady(false); setPinMode(false); setUnmappedCapture(null);
    }
  }, [visible, invalidatePendingPlacement]);
  const [saved, setSaved] = useState<AnnotationSnapshot[]>([]);
  const [error, setError] = useState("");
  const [queueingView,setQueueingView]=useState(false);
  const [pendingCapture, setPendingCapture] = useState<PendingProxyCapture | null>(null);
  const pendingCaptureRef = useRef<PendingProxyCapture | null>(null);
  const captureHandler = useRef<ProxyCapture | null>(null);
  const [proxyReady, setProxyReady] = useState(false);
  const [renderPreset, setRenderPreset] = useState<RenderPresetId>("16:9");
  const [viewportPixels, setViewportPixels] = useState<{width: number; height: number} | null>(null);
  const [captureStoreReady, setCaptureStoreReady] = useState(false);
  const captureLatch = useRef(false);
  const bindCapture = useCallback((capture: ProxyCapture | null) => {captureHandler.current = capture; setProxyReady(!!capture);}, []);
  const captureKey = `${projectId}:${version?.version_id}`;
  const initialised = useRef(false);
  const autoRecoveredInterrupted = useRef(false);
  const emittedPrimary = useRef("");
  const externalPrimary = JSON.stringify({target: external?.target ?? null, annotation: external?.annotations.map(a => [a.annotation_id,a.saved_revision]) ?? []});
  const choice = useReviewSelection();
  const scope = `${projectId}:${version?.version_id}`;
  const selected = choice.scope === scope ? choice.selected : [];
  const versionJobs = jobs.filter(j => j.target.version?.version_id === version?.version_id);
  const jobsKey = versionJobs.map(j => `${j.job_id}:${j.state_revision}:${j.artifact_ids.join(",")}`).join("|");
  const draft = drafts.find(d => d.key === draftId);
  const isSpatialDraft = Boolean(draft && draft.annotation.spatial_hits.length > 0);
  const show2DEditor = Boolean(draft && (!isSpatialDraft || editing2D));
  const pinnedHit = isSpatialDraft ? draft!.annotation.spatial_hits[0] : null;
  const editorPreparer = useRef<ContextPreparer | null>(null);
  const registerEditorPreparer = useCallback((prepare: ContextPreparer | null) => {editorPreparer.current = prepare;}, []);
  const reviewJobId=targetJobId(draft?.annotation.target ?? target, versionJobs, artifacts, sequenceJobId);
  const activeJob = jobs.find(j => j.job_id === reviewJobId);
  const currentFrames=useMemo(()=>frames.filter(f=>!reviewJobId||f.job_id===reviewJobId),[frames,reviewJobId]);
  const currentSegments=useMemo(()=>segments.filter(s=>!reviewJobId||s.job_id===reviewJobId),[segments,reviewJobId]);
  const availability=availabilityByJob[reviewJobId ?? ""] ?? null;
  const readyFrames = useMemo(() => reviewJobId ? sortedReadyFrames(currentFrames) : [], [currentFrames, reviewJobId]);
  const activeStoryboardPanel = storyboardTargets[storyboardPanelIndex];
  const matchesPanel = (panel: StoryboardTarget) => panel.target.artifact_id === target?.artifact_id && panel.shot_id === target?.shot_id;
  const storyboardIndex = activeStoryboardPanel && matchesPanel(activeStoryboardPanel) ? storyboardPanelIndex : storyboardTargets.findIndex(matchesPanel);
  const storyboardPanel = storyboardTargets[storyboardIndex];
  const displayedFrame = mode === "keyframes" ? (target?.source_frame ?? target?.sequence_frame ?? Math.max(1, storyboardIndex + 1)) : requestedFrame;
  const imageOrdinal = mode === "keyframes" ? (target?.source_frame ?? target?.sequence_frame) : (target?.sequence_frame ?? target?.source_frame);
  const passStatus = mode === "keyframes" ? describeKeyframePassStatus({readyFrames, displayedFrame, job: activeJob, availability}) : null;
  const activeFrameOrdinal = mode === "keyframes" ? displayedFrame : requestedFrame;
  useEffect(() => {
    frameInputEdited.current = false;
    setFrameInput(String(activeFrameOrdinal));
  }, [activeFrameOrdinal, mode, draftId]);
  const assembly = version?.kind === "sequence" ? artifacts.find(a => a.kind === "video" && a.state === "ready") : undefined;
  const mappedSequence = mode === "animation" && !!assembly && !!frameMap;
  const deliveryJob = versionJobs.find(job => job.kind === "encode" && job.state === "succeeded" && job.availability === "complete" && job.artifact_ids.includes(assembly?.artifact_id ?? ""));
  const deliveryManifest = deliveryJob ? artifacts.find(a => deliveryJob.artifact_ids.includes(a.artifact_id) && a.kind === "document" && a.purpose === "metadata" && a.state === "ready") : undefined;
  const readyToCapture = visible && (mode === "keyframes" ? !!target && decodedImage === decodedImageIdentity(projectId, target) : captureReady);
  const imageFailure = useCallback((message: string) => setError(message), []);
  const handleProxyError = useCallback((message: string) => setProxyLoadError(message), []);

  const changeDraft = useCallback((next: Draft) => {
    setDrafts(previous => [...previous.filter(d => d.key !== next.key), next]);
    if (next.state === "saved" && next.annotation.saved_revision !== null) {
      // Discard needs the acknowledged snapshot even if no list refresh follows Save.
      setSaved(previous => mergeSavedSnapshots(previous, [structuredClone(next.annotation)]));
    }
  }, []);

  // Resolve exact pinned ProxyMapping by mapping_artifact_id when a spatial annotation is active.
  const matchingCurrentMap = useMemo(() => {
    if (!pinnedHit || !version || !mapping) return null;
    if (mapping.mapping_artifact_id !== pinnedHit.mapping_artifact_id) return null;
    return verifySpatialMapping(mapping, pinnedHit, version.source.digest) === null ? mapping : null;
  }, [mapping, pinnedHit, version]);

  useEffect(() => {
    setPinnedMapping(null);
    setPinnedMappingError(null);
    setProxyLoadError(null);
    if (!pinnedHit || !version) return;
    if (version.version_id !== pinnedHit.version_id) {
      setPinnedMappingError(`Pinned version ${pinnedHit.version_id} differs from ${version.version_id}`);
      return;
    }
    if (matchingCurrentMap) {
      setPinnedMapping(matchingCurrentMap);
      return;
    }
    let active = true;
    api.call("getVersionProxyMapping", {
      path: {project_id: projectId, version_id: pinnedHit.version_id},
      query: {mapping_artifact_id: pinnedHit.mapping_artifact_id},
    }).then(result => {
      if (!active) return;
      const mismatch = verifySpatialMapping(result, pinnedHit, version.source.digest);
      if (mismatch) setPinnedMappingError(mismatch);
      else setPinnedMapping(result);
    }).catch(e => {
      if (active) setPinnedMappingError(e instanceof Error ? e.message : String(e));
    });
    return () => { active = false; };
  }, [
    api,
    matchingCurrentMap,
    pinnedHit?.instance_id,
    pinnedHit?.mapping_artifact_id,
    pinnedHit?.object_id,
    pinnedHit?.proxy_artifact_id,
    pinnedHit?.sample_frame,
    pinnedHit?.version_id,
    projectId,
    version?.source.digest,
    version?.version_id,
  ]);

  const activeMapping = pinnedHit ? (matchingCurrentMap ?? pinnedMapping) : mapping;
  const proxyUnavailableReason = pinnedHit ? (pinnedMappingError ?? proxyLoadError) : null;

  const resolveDiscardBaseline = useCallback(async (current: Draft): Promise<AnnotationSnapshot | null> => {
    const matches = (annotation: AnnotationSnapshot) => annotation.annotation_id === current.annotation.annotation_id
      && annotation.saved_revision !== null && annotation.saved_revision >= (current.annotation.saved_revision ?? 0);
    const cached = saved.find(matches);
    if (cached) return cached;
    try {
      const fetched = await allPages(cursor => api.call("listAnnotations", {path: {project_id: projectId}, query: {cursor, limit: 100, version_id: current.annotation.target.version_id ?? undefined}}));
      setSaved(previous => mergeSavedSnapshots(previous, fetched));
      return fetched.find(matches) ?? null;
    } catch {
      return null;
    }
  }, [api, projectId, saved]);

  const spatialController = useSpatialPinController({
    enabled: Boolean(draft && isSpatialDraft && !editing2D),
    api,
    draft,
    versionKind: version?.kind,
    proxyUnavailableReason,
    onChange: changeDraft,
    onQueue,
    onContextPreparer: registerEditorPreparer,
    placementCommitting,
    resolveDiscardBaseline,
  });

  useEffect(() => {
    let active = true;
    readProxyCapture(captureKey).then(capture => {if (active) {pendingCaptureRef.current = capture; setPendingCapture(capture); setCaptureStoreReady(true);}})
      .catch(e => {if (active) setError(`Capture recovery unavailable: ${String(e)}`);});
    return () => {active = false;};
  }, [captureKey]);

  useEffect(() => {
    if (version?.kind === "sequence" && frameMap && target && target.origin !== "sequence_render" && target.source_frame) {
      try {setTarget(sequenceTarget(target.source_frame,target,frameMap.map,frameMap.id));}
      catch (e) {setCaptureReady(false);setError(String(e));}
    }
  },[version?.kind,frameMap,target]);

  useEffect(() => {
    if (externalPrimary === emittedPrimary.current) return;
    invalidatePendingPlacement();
    setUnmappedCapture(null);
    if (!external?.target) return;
    initialised.current = true; setPlaying(false); setCaptureReady(false); setPinMode(false); setEditing2D(false); setFrameNotice("");
    setSequenceJobId(null);
    const annotation = external.annotations.find(a => a.target.artifact_id === external.target?.artifact_id);
    const spatial = Boolean(annotation && annotation.spatial_hits.length > 0);
    setTarget(structuredClone(external.target));
    setTargetCamera(annotation ? structuredClone(annotation.camera) : external.target.render_job_id ? frameCamera(external.target, jobs) : structuredClone(external.camera));
    setRange(external.range);
    setRequestedFrame(external.target.sequence_frame ?? external.target.source_frame ?? 1);
    setMode(spatial || external.view_mode === "3d_inspection" ? "3d_inspection" : external.view_mode === "animation" ? "animation" : "keyframes");
    choice.select(scope, external.selected_objects);
    if (annotation) {
      if (spatial && annotation.camera) {
        setRestorePinCamera(structuredClone(annotation.camera));
        setRestorePinNonce(n => n + 1);
      }
      const restored = draftFromSnapshot(annotation, projectId);
      // Navigation must never reconstruct an unacknowledged operation from its snapshot.
      setDrafts(previous => mergeHydratedDrafts(previous, [restored]));
      setDraftId(restored.key);
    } else setDraftId(null);
  }, [externalPrimary]);

  useEffect(() => {
    let active = true;
    readDrafts(projectId).then(result => {
      if (!active) return;
      setDrafts(previous => mergeHydratedDrafts(result, previous));
      if (!autoRecoveredInterrupted.current && version) {
        autoRecoveredInterrupted.current = true;
        const interrupted = result.find(d => d.state === "failed" && d.operation && d.annotation.target.version_id === version.version_id);
        if (interrupted) {
          setDraftId(current => {
            if (current) return current;
            if (interrupted.annotation.spatial_hits.length > 0) {
              setMode("3d_inspection");
              setEditing2D(false);
              if (interrupted.annotation.camera) {
                setRestorePinCamera(structuredClone(interrupted.annotation.camera));
                setRestorePinNonce(n => n + 1);
              }
            }
            return interrupted.key;
          });
        }
      }
    }).catch(e => {if (active) setError(`Draft recovery failed: ${String(e)}`);});
    return () => {active = false;};
  }, [projectId, version?.version_id]);

  useEffect(() => {
    let active = true; let flushing = false;
    async function reconnect() {
      if (flushing) return; flushing = true;
      try {
        const pending = (await readDrafts(projectId)).filter(d => d.state === "failed" && (d.operation || d.markup));
        for (const draft of pending) {
          if (!active) break;
          await flushDraft(api,draft,next => {if (active) changeDraft(next);});
        }
      } catch (e) {if (active) setError(`Outbox reconciliation failed: ${String(e)}`);} finally {flushing=false;}
    }
    window.addEventListener("online",reconnect);
    return () => {active=false;window.removeEventListener("online",reconnect);};
  },[api,projectId,changeDraft]);

  useEffect(() => {
    if (!version) return;
    let active = true;
    const errors: string[] = [];
    const captureError = (error: unknown) => { errors.push(error instanceof Error ? error.message : String(error)); };
    async function read() {
      const receipts: FrameReceipt[] = []; const clips: PreviewSegment[] = [];
      const media: Artifact[] = [];
      let storyboardPanels: StoryboardPanel[] = [];
      let pinned = version!;
      try {
        const versions = await allPages(cursor => api.call("listItemVersions", {path: {project_id: projectId, item_id: version!.item_id}, query: {cursor, limit: 100}}));
        if (active) setItemVersions(versions.filter(v => v.project_id === projectId && v.item_id === version!.item_id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.version_id.localeCompare(a.version_id, undefined, {numeric: true})));
        const current = versions.find(v => v.version_id === version!.version_id);
        if (current) {
          if (current.project_id !== projectId || current.item_id !== version!.item_id || current.source.digest !== version!.source.digest || current.source_commit !== version!.source_commit) throw new Error("Pinned source version identity changed");
          pinned = current;
          if (active) setRefreshedVersion(current);
        }
      } catch (e) {captureError(e);}
      await Promise.all(versionJobs.filter(job => job.render_spec !== null).map(async job => {
        await Promise.all([
          allPages(cursor => api.call("listFrameReceipts", {path: {project_id: projectId, job_id: job.job_id}, query: {cursor, limit: 100}})).then(page => receipts.push(...page)).catch(captureError),
          allPages(cursor => api.call("listPreviewSegments", {path: {project_id: projectId, job_id: job.job_id}, query: {cursor, limit: 100}})).then(page => clips.push(...page)).catch(captureError),
          api.call("getFrameAvailability", {path: {project_id: projectId, job_id: job.job_id}}).then(value => {if (active) setAvailabilityByJob(previous=>({...previous,[job.job_id]:value}));}).catch(captureError),
        ]);
      }));
      if (pinned.kind === "storyboard") {
        try {
          const plan = await api.call("getVersionShotPlan", {path: {project_id: projectId, version_id: pinned.version_id}});
          if (plan.project_id !== projectId) throw new Error("Shot plan does not match active project");
          storyboardPanels = plan.shots.flatMap(shot => shot.frames.flatMap(frame => frame.artifact_id ? [{shot_id: shot.shot_id, frame_id: frame.frame_id, role: frame.role, composition: frame.composition, artifact_id: frame.artifact_id}] : []));
        } catch (e) {captureError(e);}
      }
      const artifactIds = [...new Set([...pinned.artifact_ids, ...storyboardPanels.map(panel => panel.artifact_id), ...versionJobs.flatMap(job => job.artifact_ids)])];
      await Promise.all(artifactIds.map(async artifact_id => {
        try { media.push(await api.call("getArtifact", {path: {project_id: projectId, artifact_id}})); } catch (e) {captureError(e);}
      }));
      const completeVideo = media.find(a => a.kind === "video" && a.purpose === "final" && a.state === "ready");
      const encodedParameters = completeVideo?.derived_from.find(t => t.kind === "encode")?.parameters;
      const encodedMapHash = encodedParameters && "frame_map_sha256" in encodedParameters && typeof encodedParameters.frame_map_sha256 === "string" ? encodedParameters.frame_map_sha256 : null;
      const mapArtifact = media.find(a => a.kind === "frame_map" && a.state === "ready" && (!encodedMapHash || a.sha256 === encodedMapHash));
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
        // Keep admission order, not asynchronous HTTP completion order, for initial display.
        const orderedMedia = artifactIds.flatMap(id => media.filter(a => a.artifact_id === id));
        const panels: StoryboardTarget[] = storyboardPanels.flatMap(panel => {
          const artifact = orderedMedia.find(a => a.artifact_id === panel.artifact_id && a.project_id === projectId && a.state === "ready" && a.kind === "image" && a.dimensions && ["image/png", "image/jpeg", "image/webp"].includes(a.media_type));
          if (!artifact?.dimensions) return [];
          return [{...panel, target: {origin: artifact.purpose === "reference" ? "reference" as const : "concept" as const, artifact_id: artifact.artifact_id, displayed_sha256: artifact.sha256, dimensions: artifact.dimensions, item_id: pinned.item_id, version_id: pinned.version_id, shot_id: panel.shot_id, render_job_id: null, source_frame: null, media_artifact_id: null}}];
        });
        setStoryboardTargets(panels);
        receipts.sort((a,b) => versionJobs.findIndex(j => j.job_id === a.job_id) - versionJobs.findIndex(j => j.job_id === b.job_id) || a.source_frame - b.source_frame);
        setFrames(receipts); setSegments(clips); setArtifacts(orderedMedia);
        if (!initialised.current && (receipts.length || media.some(a => a.kind === "image" && a.state === "ready" && a.dimensions))) {
          initialised.current = true;
          if (receipts[0]) {const first=frameTarget(receipts[0],version!);setTarget(first);setTargetCamera(frameCamera(first,jobs));setRequestedFrame(receipts[0].source_frame);}
          else if (panels[0]) {setTarget(panels[0].target);setStoryboardPanelIndex(0);setTargetCamera(null);setMode("keyframes");}
          else {
            const image = orderedMedia.find(a => pinned.artifact_ids.includes(a.artifact_id) && a.kind === "image" && a.state === "ready" && a.dimensions)
              ?? versionJobs.flatMap(job => generationOutputs(job, orderedMedia))[0];
            if (image) setTarget(imageTarget(image, version!, versionJobs));
            else initialised.current = false;
          }
        }
        if (errors.length) setError(`Some review data is unavailable: ${[...new Set(errors)].join(". ")}`);
      }
    }
    void read();
    return () => {active = false;};
    // The fingerprint represents pinned version jobs, not array object identity.
  }, [api, projectId, suppliedVersion?.version_id, suppliedVersion?.artifact_ids.join("|"), jobsKey, refreshRevision]);

  useEffect(() => {
    if (!version) return;
    let active = true;
    api.call("getVersionProxyMapping", {path: {project_id: projectId, version_id: version.version_id}}).then(result => {
      if (result.version_id !== version.version_id || result.project_id !== projectId || result.source_closure_digest !== version.source.digest) throw new Error("Proxy mapping does not match pinned source");
      // The visible static proxy/sample is pinned too. A refresh must not replace
      // its GLB, reset orbit, or erase a working view because a read went offline.
      if (active) setMapping(previous => previous ?? result);
    }).catch(() => { /* Keep an already validated mapping available offline. */ });
    allPages(cursor => api.call("listAnnotations", {path: {project_id: projectId}, query: {cursor, limit: 100}})).then(result => {
      if (active) setSaved(previous => {
        const scoped = result.filter(({target}) => target.item_id === version.item_id || (target.item_id === null && target.version_id === version.version_id));
        return mergeSavedSnapshots(previous, scoped);
      });
    }).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [api, projectId, version, refreshRevision]);

  const present = useCallback((next: FrameTarget) => {
    if (draftId) return;
    if (frameMap && version?.kind === "sequence") {
      try {next = sequenceTarget(next.source_frame!, next, frameMap.map, frameMap.id);} catch {setError("Sequence mapping unavailable for presented frame"); return;}
    }
    setTarget(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    setTargetCamera(frameCamera(next,jobs));
    setCaptureReady(true);
  }, [draftId, frameMap, version?.kind,jobs]);

  const hasServerTarget = !draft?.targetBlob || Boolean(draft.targetUploaded);
  const draftContext = draft ? annotationContext(draft.annotation, version?.kind) : null;
  const context: TurnSelection = inspectedArtifact ? {...emptySelection(), request_target: {kind: "project"}, attachments: [{artifact_id: inspectedArtifact.artifact_id, role: "reference"}]} : draft && draftContext ? {...draftContext, annotations: draft.state === "saved" ? draftContext.annotations : [],
    // A local capture has no stable artefact ID until the outbox uploads it.
    target: hasServerTarget ? draftContext.target : null,
    attachments: draft.state === "saved" ? draftContext.attachments : hasServerTarget ? [{artifact_id: draft.annotation.target.artifact_id, role: cleanTargetRole(draft.annotation.target)}] : []} : {...emptySelection(), item_id: version?.item_id ?? null, version_id: version?.version_id ?? null,
    request_target: version ? versionRequestTarget(version, version.kind, mode === "3d_inspection" ? null : target?.shot_id) : undefined,
    view_mode: mode, target: mode === "3d_inspection" || !readyToCapture ? null : target,
    camera: mode === "3d_inspection" ? camera : targetCamera, selected_objects: selected,
    range: mode === "3d_inspection" && mapping ? {start: mapping.sample_frame, end: mapping.sample_frame} : range,
    attachments: mode !== "3d_inspection" && target ? [{artifact_id: target.artifact_id, role: cleanTargetRole(target)}] : []};
  const contextKey = JSON.stringify(context);
  const prepareState = useRef({context, draftKey: draft?.key});
  prepareState.current = {context, draftKey: inspectedArtifact ? undefined : draft?.key};
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

  function showKeyframe(receipt: FrameReceipt) {
    if (!version) return;
    const frame = frameTarget(receipt, version);
    setTarget(frame);
    setTargetCamera(frameCamera(frame, jobs));
    setRequestedFrame(receipt.source_frame);
    setFrameInput(String(receipt.source_frame));
    setPlaying(false);
    setCaptureReady(false);
    setFrameNotice("");
  }

  function showStoryboardPanel(index: number) {
    const panel = storyboardTargets[index];
    if (!panel) return;
    setTarget(panel.target);
    setStoryboardPanelIndex(index);
    setRequestedFrame(index + 1);
    setFrameInput(String(index + 1));
    setTargetCamera(null);
    setPlaying(false);
    setCaptureReady(false);
    setFrameNotice("");
  }

  function commitFrameInput(raw: string) {
    frameInputEdited.current = false;
    const ordinal = Number(raw);
    if (!raw.trim() || !Number.isFinite(ordinal) || ordinal < 1 || (mode === "keyframes" && !Number.isSafeInteger(ordinal))) {
      setFrameInput(String(activeFrameOrdinal));
      return;
    }
    if (mode === "animation") {
      const next = Math.trunc(ordinal);
      setPlaying(false); setCaptureReady(false); setRequestedFrame(next); setFrameInput(String(next)); setFrameNotice("");
      return;
    }
    if (!readyFrames.length && storyboardPanel && storyboardTargets[ordinal - 1]) {
      showStoryboardPanel(ordinal - 1);
      return;
    }
    const result = resolveDirectKeyframeJump({requestedOrdinal: ordinal, displayedFrame, readyFrames, job: activeJob, availability});
    if (result.kind === "found") showKeyframe(result.frame);
    else if (!readyFrames.length && ordinal === displayedFrame) {
      setFrameInput(String(displayedFrame));
      setFrameNotice("");
    } else {
      setFrameInput(String(result.revertFrame));
      setRequestedFrame(result.revertFrame);
      setFrameNotice(result.message);
    }
  }

  const storyboardCount = storyboardPanel ? storyboardTargets.length : 0;
  const canStepKeyframe = (delta: -1 | 1) => canStepKeyframePass(readyFrames, displayedFrame, delta, storyboardCount);

  function step(delta: -1 | 1, shot = false) {
    if (draftId) return;
    setPlaying(false);
    if (shot) {
      const currentIndex=versionJobs.findIndex(j=>j.job_id===reviewJobId);
      const candidates=delta>0?versionJobs.slice(currentIndex+1):versionJobs.slice(0,currentIndex).reverse();
      const next=candidates.find(j=>j.target.shot_id!==activeJob?.target.shot_id);
      if(next) chooseJob(next.job_id);
      return;
    }
    if (mode === "keyframes") {
      if (!canStepKeyframe(delta)) return;
      const next = stepKeyframePass(readyFrames, displayedFrame, delta);
      if (next) showKeyframe(next);
      else if (storyboardPanel) showStoryboardPanel(storyboardIndex + delta);
      return;
    }
    if (mode !== "animation") return;
    if (mappedSequence && frameMap) {
      setRequestedFrame(Math.min(frameMap.map.frame_count, Math.max(1, requestedFrame + delta)));
      setFrameNotice("");
      return;
    }
    if (!currentFrames.length && storyboardPanel && storyboardTargets[storyboardIndex + delta]) {
      showStoryboardPanel(storyboardIndex + delta);
      setMode("keyframes");
      return;
    }
    if (!currentFrames.length) return;
    const current = target?.sequence_frame ?? target?.source_frame ?? requestedFrame;
    const ordinal = Math.max(1, current + delta);
    setRequestedFrame(ordinal); setCaptureReady(false);
    const pending = !currentFrames.some(f => f.source_frame === ordinal) && isFrameRendering(ordinal, activeJob, availability);
    setFrameNotice(pending ? `Frame ${ordinal} is pending. Last identified image retained.` : "");
  }

  function chooseJob(identity:string) {
    setFrameNotice("");
    const first=frames.find(f=>f.job_id===identity);
    const job=versionJobs.find(j=>j.job_id===identity);
    const image=job ? generationOutputs(job,artifacts)[0] : undefined;
    if (!version || (!first && !image)) {setError(`No ready review output for ${identity}. Current image and job retained.`);return;}
    setSequenceJobId(first && version.kind === "sequence" ? identity : null);
    setPlaying(false);setCaptureReady(false);setRange(null);setTargetCamera(null);setError("");
    if(first) showKeyframe(first);
    else if(image){setMode("keyframes");setTarget(imageTarget(image,version,versionJobs));setRequestedFrame(1);setFrameInput("1");}
  }

  function changeMode(next: Mode) {
    if (mode !== next) {
      invalidatePendingPlacement();
      setCaptureReady(false);
      setPlaying(false);
      setPinMode(false);
      setUnmappedCapture(null);
      setFrameNotice("");
      setMode(next);
    }
  }

  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (!visible) return;
      if (event.key === "Escape" && pinMode) { event.preventDefault(); setPinMode(false); return; }
      if (typingTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey || draftId) return;
      const modes: Record<string, Mode> = {"1": "3d_inspection", "2": "keyframes", "3": "animation"};
      if (modes[event.key]) {event.preventDefault(); changeMode(modes[event.key]);}
      else if (event.code === "Space" && mode === "animation") {event.preventDefault(); setPlaying(p => !p);}
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1, event.shiftKey);}
    }
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  });

  function beginPinPlacement(expected: Draft | undefined) {
    const token = ++placementTokenRef.current;
    const expectedKey = draftIdRef.current;
    const expectedJson = expected ? stableJson(expected.annotation) : null;
    setPlacementCommitting(true);
    return {
      current: () => placementTokenRef.current === token,
      canWrite: () => {
        if (placementTokenRef.current !== token || draftIdRef.current !== expectedKey) return false;
        if (!expectedKey) return true;
        const live = draftsRef.current.find(d => d.key === expectedKey);
        // The controller is busy *because* this placement is committing. Check
        // draft identity and competing operations, not the combined busy flag.
        return Boolean(live && live === expected && live.state === "browser-local" && !live.operation && !live.fallbackOperation
          && !spatialController.discard.discardLatch.current && stableJson(live.annotation) === expectedJson);
      },
      finish: () => { if (placementTokenRef.current === token) setPlacementCommitting(false); },
    };
  }

  async function handlePlacePin(placement: ProxyPinPlacement) {
    if (placementCommitting || !visible || (draft && spatialController.busy)) return;
    setFrameNotice("");
    if (!version || !activeMapping?.mapping_artifact_id) {
      setError("Pinned proxy mapping artefact identity is unavailable.");
      return;
    }
    const existing = draft && isSpatialDraft && draft.annotation.saved_revision === null ? draft : null;
    const commit = beginPinPlacement(draft);
    setPinMode(false);
    setUnmappedCapture(null);
    setError("");
    try {
      const png = await placement.capture.png;
      const displayedSha256 = await sha256Hex(png);
      if (!commit.canWrite()) return;
      const shotId = versionJobs.find(job => job.artifact_ids.includes(activeMapping.proxy_artifact_id))?.target.shot_id ?? null;
      const noteText = existing?.annotation.text ?? "";
      const hit: SpatialHit = {
        proxy_artifact_id: activeMapping.proxy_artifact_id,
        mapping_artifact_id: activeMapping.mapping_artifact_id,
        version_id: activeMapping.version_id,
        sample_frame: activeMapping.sample_frame,
        object_id: placement.node.object_id,
        instance_id: placement.node.instance_id,
        space: "blender_world_m",
        position: placement.position,
        normal: placement.normal,
        camera: placement.capture.camera,
      };
      const pinnedTarget: FrameTarget = {
        origin: "proxy_capture",
        artifact_id: `local-capture-${crypto.randomUUID()}`,
        displayed_sha256: displayedSha256,
        dimensions: placement.capture.dimensions,
        item_id: version.item_id,
        version_id: activeMapping.version_id,
        shot_id: shotId,
        render_job_id: null,
        source_frame: activeMapping.sample_frame,
        media_artifact_id: null,
      };
      const clampedUv: [number, number] = [
        Math.min(1, Math.max(0, placement.pinUv[0])),
        Math.min(1, Math.max(0, placement.pinUv[1])),
      ];
      const markups: Markup[] = [{
        id: crypto.randomUUID(),
        kind: "pin",
        points: [clampedUv],
        width_px: 6,
        colour: "#ff5555",
        text: pinMarkupLabel(noteText, placement.node.instance_id),
      }];
      const annotation: AnnotationSnapshot = {
        annotation_id: existing?.annotation.annotation_id ?? crypto.randomUUID(),
        saved_revision: null,
        target: pinnedTarget,
        range: {start: activeMapping.sample_frame, end: activeMapping.sample_frame},
        camera: placement.capture.camera,
        markups,
        text: noteText,
        spatial_hits: [hit],
        spatial_status: "valid",
      };
      const next: Draft = {
        key: existing?.key ?? annotation.annotation_id,
        projectId,
        annotation,
        state: "browser-local",
        targetBlob: png,
        targetUploaded: false,
      };
      await saveDraftEdit(next);
      if (!commit.canWrite()) return;
      changeDraft(next);
      setDraftId(next.key);
      setEditing2D(false);
      setRestorePinCamera(null);
    } catch (e) {
      if (commit.current()) setError(String(e));
    } finally {
      commit.finish();
    }
  }

  function handlePinMiss(reason: "empty" | "unmapped", fallback?: UnmappedCapture) {
    if (reason === "empty") {
      setError("Clicked background outside proxy geometry. Click a mesh surface to place a 3D pin.");
      return;
    }
    setPinMode(false);
    setUnmappedCapture(fallback ?? null);
    setError("Clicked surface has no unique authored proxy mapping. Create a 2D snapshot note instead.");
  }

  async function startUnmappedSnapshotNote() {
    if (!unmappedCapture || !version || !activeMapping || placementCommitting || !visible) return;
    const captured = unmappedCapture;
    const commit = beginPinPlacement(draft);
    setUnmappedCapture(null);
    setError("");
    try {
      const png = await captured.png;
      const displayedSha256 = await sha256Hex(png);
      if (!commit.canWrite()) return;
      const shotId = versionJobs.find(job => job.artifact_ids.includes(activeMapping.proxy_artifact_id))?.target.shot_id ?? null;
      const pinnedTarget: FrameTarget = {
        origin: "proxy_capture",
        artifact_id: `local-capture-${crypto.randomUUID()}`,
        displayed_sha256: displayedSha256,
        dimensions: captured.dimensions,
        item_id: version.item_id,
        version_id: activeMapping.version_id,
        shot_id: shotId,
        render_job_id: null,
        source_frame: activeMapping.sample_frame,
        media_artifact_id: null,
      };
      const annotation: AnnotationSnapshot = {
        annotation_id: crypto.randomUUID(),
        saved_revision: null,
        target: pinnedTarget,
        range: {start: activeMapping.sample_frame, end: activeMapping.sample_frame},
        camera: captured.camera,
        markups: [{
          id: crypto.randomUUID(),
          kind: "pin",
          points: [[Math.min(1, Math.max(0, captured.clickUv[0])), Math.min(1, Math.max(0, captured.clickUv[1]))]],
          width_px: 6,
          colour: "#ff5555",
          text: "Snapshot note",
        }],
        text: "",
        spatial_hits: [],
        spatial_status: "unavailable",
      };
      const next: Draft = {
        key: annotation.annotation_id,
        projectId,
        annotation,
        state: "browser-local",
        targetBlob: png,
        targetUploaded: false,
      };
      await saveDraftEdit(next);
      if (!commit.canWrite()) return;
      changeDraft(next);
      setDraftId(next.key);
      setEditing2D(true);
    } catch (e) {
      if (commit.current()) setError(String(e));
    } finally {
      commit.finish();
    }
  }

  async function renderThisView() {
    if (!visible || captureLatch.current || !version || !captureStoreReady) return;
    captureLatch.current = true; setQueueingView(true); setError("");
    try {
      let pending = pendingCaptureRef.current;
      if (pending && pending.phase !== "upload") throw new Error("Resolve the retained Copilot request before dismissing this capture. It will not be queued again automatically.");
      if (!pending) {
        const capture = captureHandler.current;
        if (!capture || !mapping) throw new Error("The published inspection proxy is not ready to capture");
        // All source identities, camera, selected pairs and actual pixels freeze
        // before any await. The upload is not a request to render/generate.
        const frozen = capture({targetDimensions: resolveRenderTargetDimensions(renderPreset)});
        const uploadId = crypto.randomUUID();
        const selection = structuredClone({...context, request_target: versionRequestTarget(version, version.kind), target: null, camera: frozen.camera, annotations: [], approved_findings: [], attachments: [], range: {start: mapping.sample_frame, end: mapping.sample_frame}});
        const sampleFrame = mapping.sample_frame;
        const shotId = versionJobs.find(job => job.artifact_ids.includes(mapping.proxy_artifact_id))?.target.shot_id ?? null;
        const png = await frozen.png;
        const sha256 = await sha256Hex(png);
        pending = {key: captureKey, uploadId, png, sha256, dimensions: frozen.dimensions, selection, sampleFrame, shotId, phase: "upload"};
        pendingCaptureRef.current = pending; setPendingCapture(pending);
      }
      await writeProxyCapture(pending.key, pending, pending.uploadId);
      const artifact = await api.call("uploadImage", {path: {project_id: projectId, upload_id: pending.uploadId}, body: pending.png, idempotencyKey: pending.uploadId});
      if (artifact.project_id !== projectId || artifact.state !== "ready" || artifact.kind !== "image" || artifact.media_type !== "image/png" || artifact.sha256 !== pending.sha256 || artifact.bytes !== pending.png.size || artifact.dimensions?.width !== pending.dimensions.width || artifact.dimensions.height !== pending.dimensions.height) throw new Error("Capture upload acknowledgement does not match the frozen PNG hash/dimensions");
      const selection: TurnSelection = {...pending.selection, request_target: versionRequestTarget(version, version.kind), target: {origin: "proxy_capture", artifact_id: artifact.artifact_id, displayed_sha256: pending.sha256, dimensions: pending.dimensions, item_id: pending.selection.item_id, version_id: pending.selection.version_id, source_frame: pending.sampleFrame, shot_id: pending.shotId, render_job_id: null, media_artifact_id: null}, attachments: [{artifact_id: artifact.artifact_id, role: "reference"}]};
      pending = {...pending, selection, phase: "handoff"};
      await writeProxyCapture(pending.key, pending, pending.uploadId);
      pendingCaptureRef.current = pending; setPendingCapture(pending);
      try {await onQueue("Render this view", structuredClone(selection));}
      catch (e) {
        pending = {...pending, phase: "uncertain"}; pendingCaptureRef.current = pending; setPendingCapture(pending);
        await writeProxyCapture(pending.key, pending, pending.uploadId);
        throw e;
      }
      await writeProxyCapture(pending.key, null, pending.uploadId);
      pendingCaptureRef.current = null; setPendingCapture(null);
    } catch (e) {setError(String(e));}
    finally {captureLatch.current = false; setQueueingView(false);}
  }

  async function annotate() {
    setPlaying(false);
    setFrameNotice("");
    if (!visible || !target || !readyToCapture || mode === "3d_inspection") return;
    // The target comes from a decoded image or rVFC-confirmed animation presentation.
    await startDraft(target);
  }

  async function startDraft(pinned: FrameTarget) {
    setFrameNotice("");
    const annotation: AnnotationSnapshot = {annotation_id: crypto.randomUUID(), saved_revision: null,
      target: structuredClone(pinned), range, camera: pinned.render_job_id ? frameCamera(pinned,jobs) : pinned.origin === "sequence_render" ? null : structuredClone(targetCamera), markups: [], text: "", spatial_hits: [], spatial_status: "unavailable"};
    const next: Draft = {key: annotation.annotation_id, projectId, annotation, state: "browser-local"};
    try {await saveDraft(next); changeDraft(next); setEditing2D(false); setDraftId(next.key);} catch (e) {setError(String(e));}
  }

  function applyAnnotationView(annotation: AnnotationSnapshot) {
    const spatial = annotation.spatial_hits.length > 0;
    setTarget(structuredClone(annotation.target));
    setMode(spatial ? "3d_inspection" : annotation.target.media_artifact_id ? "animation" : "keyframes");
    setRequestedFrame(annotation.target.sequence_frame ?? annotation.target.source_frame ?? 1);
    setTargetCamera(structuredClone(annotation.camera));
    setRange(structuredClone(annotation.range));
    choice.select(scope, annotation.spatial_hits.map(h => ({object_id: h.object_id, instance_id: h.instance_id})));
    setRestorePinCamera(spatial ? structuredClone(annotation.camera) : null);
    if (spatial && annotation.camera) setRestorePinNonce(n => n + 1);
  }

  async function restore(entry: AnnotationEntry) {
    invalidatePendingPlacement();
    setFrameNotice("");
    setSequenceJobId(null);
    setPlaying(false);
    setPinMode(false);
    setUnmappedCapture(null);
    setEditing2D(false);
    setDrawer(null);
    const next = resolveAnnotationEntry(entry, drafts, projectId);
    if (onNavigate && next.annotation.target.version_id && next.annotation.target.item_id && version?.version_id !== next.annotation.target.version_id) {
      try {
        const targetVersion = (await allPages(cursor => api.call("listItemVersions", {path: {project_id: projectId, item_id: next.annotation.target.item_id!}, query: {cursor, limit: 100}}))).find(v => v.version_id === next.annotation.target.version_id);
        if (!targetVersion) throw new Error(`Version unavailable: ${next.annotation.target.version_id}`);
        await saveDraft(next);
        onNavigate(targetVersion, annotationContext(next.annotation, targetVersion.kind));
        return;
      } catch (e) {setError(String(e)); return;}
    }
    applyAnnotationView(next.annotation);
    changeDraft(next);
    setDraftId(next.key);
    try {await saveDraft(next);} catch (e) {setError(String(e)); return;}
    if (onNavigate && version && next.annotation.target.version_id === version.version_id) {
      onNavigate(version, annotationContext(next.annotation, version.kind));
    }
  }

  function closeDraft() {
    invalidatePendingPlacement();
    setPinMode(false);
    setUnmappedCapture(null);
    setEditing2D(false);
    setRestorePinCamera(null);
    setDraftId(null);
  }

  function discardActiveDraft(draftKey: string, mode: "delete_new" | "revert_edits", restored?: Draft) {
    invalidatePendingPlacement();
    setPinMode(false);
    setUnmappedCapture(null);
    if (mode === "delete_new") {
      setDrafts(previous => previous.filter(d => d.key !== draftKey));
      if (draftIdRef.current === draftKey) closeDraft();
      return;
    }
    // The discard transaction has already persisted the acknowledged baseline.
    if (!restored || restored.annotation.saved_revision === null) {
      setError("Saved annotation baseline is unavailable; reload annotations before discarding edits.");
      return;
    }
    setError("");
    setDrafts(previous => [...previous.filter(d => d.key !== draftKey && d.key !== restored.key), restored]);
    if (draftIdRef.current !== draftKey) return;
    applyAnnotationView(restored.annotation);
    setDraftId(restored.key);
  }

  const merged = useMemo(() => version ? mergeItemAnnotations({itemId: version.item_id, currentVersionId: version.version_id, drafts, saved, itemVersions}) : null,
    [version, drafts, saved, itemVersions]);

  if (inspectedArtifact) return <RetainedReview {...{projectId, version, inspectedArtifact, inspectedArtifactSummary, onClearArtifact}}/>;
  if (!version || !merged) return <ReviewEmpty mode={mode} onChange={changeMode}/>;
  const activePins: SpatialPinMarker[] = draft && isSpatialDraft
    ? draft.annotation.spatial_hits.map((hit, idx) => ({
      id: `${draft.key}-${idx}`,
      position: hit.position,
      instanceId: hit.instance_id,
      label: pinMarkupLabel(draft.annotation.text, hit.instance_id),
    }))
    : [];
  const effectiveRestoreCamera = draft && isSpatialDraft
    ? restorePinCamera
    : target?.version_id === activeMapping?.version_id && target?.source_frame === activeMapping?.sample_frame
      ? targetCamera
      : null;
  // Compatible with the current dock and the placement-aware dock on integration.
  const placementProps = {placementCommitting};

  return <section className="review-workspace" aria-label="Review workspace">
    {show2DEditor ? null : draft && isSpatialDraft ? (
      <SpatialPinHeader
        {...placementProps}
        draft={draft}
        pinMode={pinMode}
        proxyUnavailableReason={proxyUnavailableReason}
        controller={spatialController}
        onTogglePinMode={() => { if (!placementCommitting) setPinMode(p => !p); }}
        onRestoreView={() => {
          if (draft.annotation.camera) {
            setRestorePinCamera(structuredClone(draft.annotation.camera));
            setRestorePinNonce(n => n + 1);
          }
        }}
        onEdit2D={() => { invalidatePendingPlacement(); setPinMode(false); setUnmappedCapture(null); setEditing2D(true); }}
      />
    ) : (
      <nav className="review-bar" aria-label="Review mode"><ReviewModeSwitcher mode={mode} onChange={changeMode}/>
        <div className="toolbar-spacer"/><div className="toolbar-group review-command-actions">
          {assembly?.purpose === "final" && deliveryJob && deliveryManifest && <>
            <a className="toolbar-btn" title="Final encode · technical completion is not owner selection" href={mediaUrl(projectId,assembly.artifact_id)} download={`${version.version_id}.mp4`}><Download size={13}/>Download final video</a>
            <a className="toolbar-btn" title="Final encode · technical completion is not owner selection" href={mediaUrl(projectId,deliveryManifest.artifact_id)} download={`${version.version_id}-manifest.json`}><Download size={13}/>Download delivery manifest</a>
          </>}
          <button className="toolbar-btn" aria-pressed={drawer === "annotations"} onClick={() => setDrawer(drawer === "annotations" ? null : "annotations")}><Bookmark size={13}/>Annotations{merged.thisVersion.length > 0 && <span className="toolbar-count">{merged.thisVersion.length}</span>}</button>
          <button className="toolbar-btn" aria-pressed={drawer === "details"} onClick={() => setDrawer(drawer === "details" ? null : "details")}><Info size={13}/>Details</button>
          {mode === "3d_inspection" ? <>
            <button
              className="toolbar-btn"
              aria-pressed={pinMode}
              disabled={!visible || !proxyReady || !mapping?.mapping_artifact_id || queueingView || placementCommitting}
              onClick={() => { setUnmappedCapture(null); setPinMode(p => !p); }}
            >
              <MapPin size={14}/>Pin note
            </button>
            <div className="toolbar-group render-capture-group">
              <label className="toolbar-field">
                <span className="sr-only">Render output format</span>
                <select
                  aria-label="Render output format"
                  disabled={!visible || !proxyReady || queueingView || !!pendingCapture}
                  value={pendingCapture ? "retained" : renderPreset}
                  onChange={e => setRenderPreset(e.target.value as RenderPresetId)}
                >
                  {pendingCapture && <option value="retained">{pendingCapture.dimensions.width} × {pendingCapture.dimensions.height} retained</option>}
                  {RENDER_PRESETS.map(preset => (
                    <option key={preset.id} value={preset.id}>{formatRenderPresetOption(preset.id, viewportPixels)}</option>
                  ))}
                </select>
              </label>
              <button className="toolbar-btn toolbar-btn-accent" disabled={!visible || (!pendingCapture && !proxyReady) || !captureStoreReady || queueingView || (!!pendingCapture && pendingCapture.phase !== "upload")} onClick={() => void renderThisView()}><CameraIcon size={14}/>{pendingCapture?.phase === "upload" ? `Retry captured view upload (${pendingCapture.dimensions.width} × ${pendingCapture.dimensions.height})` : "Render this view"}</button>
            </div>
          </> : !mappedSequence && <button className="toolbar-btn toolbar-btn-accent" aria-label="Annotate presented frame" disabled={!visible || !target || !readyToCapture} onClick={() => void annotate()}><PenLine size={14}/>Annotate</button>}
        </div>
      </nav>
    )}
    {!draft && drawer === "annotations" && <AnnotationsDrawer version={version} merged={merged} onClose={() => setDrawer(null)} onRestore={entry => void restore(entry)}/>}
    {!draft && drawer === "details" && <section className="review-drawer" aria-label="Pinned version details">
      <button className="toolbar-btn toolbar-icon-btn drawer-close" aria-label="Close review drawer" onClick={() => setDrawer(null)}><X size={14}/></button>
      <h2>Pinned version details</h2>
      <div className="review-version-identity"><code>Git {version.source_commit.slice(0, 10)}</code><code>{version.version_id}</code></div>
      <p>{version.name} · {version.artifact_ids.length} version artefacts</p>
      <div className="review-drawer-items"><strong>Published image artefacts</strong>{artifacts.filter(a => a.kind === "image").map(a => <span className="toolbar-chip" key={a.artifact_id}>{a.artifact_id}: {a.state} · {a.purpose}</span>)}</div>
    </section>}
    {draft && show2DEditor ? (
      <AnnotationEditor
        key={draft.key}
        api={api}
        draft={draft}
        versionKind={version?.kind}
        onChange={changeDraft}
        onQueue={onQueue}
        onContextPreparer={registerEditorPreparer}
        onReturnTo3D={isSpatialDraft && !proxyUnavailableReason ? () => setEditing2D(false) : undefined}
        onClose={closeDraft}
        onDiscard={discardActiveDraft}
        resolveDiscardBaseline={resolveDiscardBaseline}
      />
    ) : draft && isSpatialDraft && proxyUnavailableReason ? (
      <SpatialPinFallbackView draft={draft} reason={proxyUnavailableReason} controller={spatialController}/>
    ) : (mode === "3d_inspection" || (draft && isSpatialDraft)) ? (
      activeMapping ? (
        <MediaBoundary key={activeMapping.proxy_artifact_id} onError={handleProxyError}>
          <ProxyView
            visible={visible}
            url={mediaUrl(projectId, activeMapping.proxy_artifact_id)}
            mapping={activeMapping}
            pinMode={pinMode}
            pins={activePins}
            onSelect={pairs => choice.select(scope, pairs)}
            onCamera={setCamera}
            onCaptureReady={bindCapture}
            renderPreset={draft && isSpatialDraft ? "viewport" : renderPreset}
            onViewportPixels={setViewportPixels}
            onPlacePin={placement => void handlePlacePin(placement)}
            onPinMiss={handlePinMiss}
            restoreCamera={effectiveRestoreCamera}
            restoreCameraNonce={restorePinNonce}
            suppressFramingGate={Boolean(draft && isSpatialDraft)}
          />
        </MediaBoundary>
      ) : pinnedHit ? (
        <div className="viewer-empty">Loading pinned 3D proxy {pinnedHit.mapping_artifact_id}…</div>
      ) : (
        <div className="viewer-empty">Inspection proxy unavailable for {version.version_id}. Image-space review remains available.</div>
      )
    ) : mode === "animation" ? assembly && frameMap ? <SequencePlayer visible={visible} key={assembly.artifact_id} projectId={projectId} artifact={assembly} mapping={frameMap.map} requestedFrame={requestedFrame} onCapture={async (blob,frame,width,height) => {
          const upload_id=crypto.randomUUID();const capture=await api.call("uploadImage",{path:{project_id:projectId,upload_id},body:blob,idempotencyKey:upload_id});
          const pinned:FrameTarget={origin:"sequence_render",artifact_id:capture.artifact_id,displayed_sha256:capture.sha256,dimensions:{width,height},item_id:version.item_id,version_id:version.version_id,shot_id:null,render_job_id:null,source_frame:null,media_artifact_id:assembly.artifact_id,sequence_version_id:version.version_id,sequence_frame:frame,frame_map_artifact_id:frameMap.id};
          setTarget(pinned);setCaptureReady(true);await startDraft(pinned);
         }}/> : <ProgressivePlayer visible={visible} key={reviewJobId} projectId={projectId} version={version} frames={currentFrames} segments={currentSegments} availability={availability} requestedFrame={requestedFrame} playing={playing} onPlaying={setPlaying} onPresented={present}/>
           : target ? <div className="review-image"><DecodedReviewImage visible={visible} projectId={projectId} target={target} onReadiness={setDecodedImage} onFailure={imageFailure}/></div> : <div className="viewer-empty">No ready review image. Rendering is an explicit Queue request.</div>}
    {show2DEditor ? null : draft && isSpatialDraft ? (
      <SpatialPinDock {...placementProps} draft={draft} proxyUnavailableReason={proxyUnavailableReason} controller={spatialController} onClose={closeDraft} onDiscard={discardActiveDraft} resolveDiscardBaseline={resolveDiscardBaseline}/>
    ) : (
      <div className={`review-bar review-transport-bar${mode === "3d_inspection" ? "" : " review-playback-transport"}`} aria-label="Review transport">
        {mode === "3d_inspection" ? <>
          <span className="toolbar-chip">Sample frame {mapping?.sample_frame ?? 1}</span>
          {selected.length > 0 && <span className="toolbar-chip">{selected.length} object(s) selected</span>}
          <div className="toolbar-spacer"/>
          <span className="toolbar-meta">
            {pinMode ? "Pin note active · click mesh surface to place pin · drag to orbit" : "Static inspection proxy · orbit / click to select"}
          </span>
        </> : <>
          <div className="toolbar-group">
            {mode === "animation" && !mappedSequence && <button className="toolbar-btn toolbar-icon-btn" aria-label={playing ? "Pause" : "Play"} title={playing ? "Pause (Space)" : "Play (Space)"} disabled={!visible} onClick={() => setPlaying(!playing)}>{playing ? <Pause size={14}/> : <Play size={14}/>}</button>}
            <button className="toolbar-btn toolbar-icon-btn" aria-label="Previous frame" title="Previous frame (←)" disabled={mode === "keyframes" && !canStepKeyframe(-1)} onClick={() => step(-1)}><ChevronLeft size={15}/></button>
            <label className="toolbar-field"><span>Frame</span><input aria-label="Frame" type="number" min="1" value={frameInput}
              onPointerDown={() => { framePointerDown.current = true; }}
              onPointerUp={() => { framePointerDown.current = false; }}
              onPointerCancel={() => { framePointerDown.current = false; }}
              onPointerLeave={() => { framePointerDown.current = false; }}
              onBlur={e => {
                framePointerDown.current = false;
                if (frameInputEdited.current) commitFrameInput(e.currentTarget.value);
              }}
              onKeyDown={e => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitFrameInput(e.currentTarget.value);
                }
                else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
                  e.preventDefault();
                  const ordinal = e.currentTarget.valueAsNumber;
                  commitFrameInput(String(Math.max(1, (Number.isFinite(ordinal) ? Math.trunc(ordinal) : activeFrameOrdinal) + (e.key === "ArrowUp" ? 1 : -1))));
                }
              }}
              onChange={e => {
                const {value} = e.currentTarget;
                frameInputEdited.current = true;
                setFrameInput(value);
                if (framePointerDown.current || document.activeElement !== e.currentTarget) commitFrameInput(value);
              }}/></label>
            <button className="toolbar-btn toolbar-icon-btn" aria-label="Next frame" title="Next frame (→)" disabled={mode === "keyframes" && !canStepKeyframe(1)} onClick={() => step(1)}><ChevronRight size={15}/></button>
          </div>
          {passStatus && <span className="toolbar-chip" title={passStatus === "Only rendered frame" ? `Only frame ${readyFrames[0].source_frame} was rendered in this pass` : undefined}>{passStatus}</span>}
          {frameNotice && <span className="toolbar-chip review-frame-notice" role="status">{frameNotice}</span>}
          {!readyFrames.length && storyboardPanel && <span className="toolbar-chip" title={`${storyboardPanel.role} · ${storyboardPanel.composition}`}>{storyboardPanel.shot_id} · {storyboardPanel.frame_id} ({storyboardIndex + 1} of {storyboardTargets.length})</span>}
          {versionJobs.length > 0 && <label className="toolbar-field"><span>Pass</span><select aria-label="Shot / render job" value={reviewJobId ?? ""} onChange={e=>chooseJob(e.target.value)}><option value="" disabled>No associated job</option>{versionJobs.map(job=><option key={job.job_id} value={job.job_id}>{job.target.shot_id ?? job.render_spec?.scene_name ?? "Scene"} · {job.render_spec?.purpose ?? job.kind} · {job.job_id}</option>)}</select></label>}
          <div className="toolbar-spacer"/>
          {target && <span className="toolbar-chip">{target.dimensions.width} × {target.dimensions.height} · {formatTargetOrigin(target.origin)} · {imageOrdinal == null ? "still" : `f${imageOrdinal}`}</span>}
          {activeJob?.render_spec && <span className="toolbar-meta">{activeJob.render_spec.engine} · {activeJob.render_spec.purpose} · {activeJob.render_spec.samples} spp</span>}
        </>}
      </div>
    )}
    {unmappedCapture && (
      <section className="review-status-banner" aria-label="Unmapped proxy surface fallback">
        <span>No unique authored object/instance identity matched that mesh.</span>
        <button className="toolbar-btn toolbar-btn-accent" onClick={() => void startUnmappedSnapshotNote()}>Create 2D snapshot note</button>
        <button className="toolbar-btn" onClick={() => setUnmappedCapture(null)}>Dismiss</button>
      </section>
    )}
    {pendingCapture && <section className="review-status-banner" aria-label="Retained inspection capture"><span>Capture {pendingCapture.uploadId} · {pendingCapture.dimensions.width} × {pendingCapture.dimensions.height} · {pendingCapture.phase}</span>{pendingCapture.phase !== "upload" && <><span>Inspect and resolve the retained request in Copilot. This capture is not automatically queued again.</span><button className="toolbar-btn" disabled={queueingView} onClick={async () => {try {await writeProxyCapture(pendingCapture.key, null, pendingCapture.uploadId); pendingCaptureRef.current = null; setPendingCapture(null);} catch (e) {setError(String(e));}}}>Dismiss capture after resolving Copilot request</button></>}</section>}
    {error && <div className="review-status-banner" role="status">{error}</div>}
  </section>;
}
