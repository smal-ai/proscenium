import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { MapPin, PenLine, RotateCcw } from "lucide-react";
import type { AnnotationSave, AnnotationSnapshot, VersionRecord } from "../../api/generated/contracts";
import type { ApiClient } from "../../api/client";
import type { ContextPreparer, TurnSelection } from "../../api/studio";
import { shortVersionLabel } from "../catalogue/catalogueHelpers";
import { handleSubmitEnter } from "../chat/controls";
import { annotationContext } from "./context";
import { mediaUrl, stableJson } from "./media";
import { classifyDraftDiscard, discardDraftAtomically, flushDraft, formatDraftStateBadge, saveDraft, saveDraftEdit, waitForDraftEdits, VECTOR_MASK, type DiscardMode, type Draft } from "./outbox";
import { greyPng } from "./png";
import { allPages } from "./records";
import { paintMarkups, renderCompositeFromBlob, syncSpatialPinMarkup } from "./spatial";
import "./annotationDock.css";

export type DiscardHandler = (draftKey: string, mode: DiscardMode, restored?: Draft) => void | Promise<void>;
export type DiscardBaselineResolver = (draft: Draft) => AnnotationSnapshot | null | Promise<AnnotationSnapshot | null>;
const noop = () => {};
const baselineUnavailable = "Saved annotation baseline is unavailable; reload annotations before discarding edits.";

export function useDraftDiscard(
  draft: Draft | undefined,
  live: RefObject<Draft | undefined>,
  writes: RefObject<Promise<void>>,
  isSubmitting: () => boolean,
  setError: (message: string) => void,
  {resolveBaseline, api, onChange}: {resolveBaseline?: DiscardBaselineResolver; api?: ApiClient; onChange?: (draft: Draft) => void} = {},
) {
  const [confirmation, setConfirmation] = useState<{draft: Draft; baseline: AnnotationSnapshot | null} | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const discardLatch = useRef(false);
  const mounted = useRef(true);
  const submitting = useRef(isSubmitting); submitting.current = isSubmitting;
  const currentConfirmation = confirmation?.draft === draft ? confirmation : null;
  const policy = draft ? classifyDraftDiscard(draft, currentConfirmation?.baseline?.saved_revision) : {allowed: false} as const;
  const confirmingDiscard = currentConfirmation !== null;
  const cancel = () => setConfirmation(null);
  useEffect(() => { setConfirmation(null); }, [draft]);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function discard(onDiscard: DiscardHandler | undefined, onClose: () => void, confirmed = false, resolver = resolveBaseline) {
    const current = live.current;
    if (!current || current !== draft || discardLatch.current || submitting.current()) return;
    const policy = classifyDraftDiscard(current);
    if (!policy.allowed || (confirmed && !confirmingDiscard)) return;
    if (policy.mode === "delete_new" && policy.requiresConfirmation && !confirmed) {
      setConfirmation({draft: current, baseline: null});
      return;
    }
    discardLatch.current = true;
    setDiscarding(true);
    setError("");
    const isCurrent = () => mounted.current && live.current === current;
    try {
      let baseline = currentConfirmation?.baseline ?? null;
      if (policy.mode === "revert_edits" && !confirmed) {
        const isAcknowledged = (candidate: AnnotationSnapshot | null): candidate is AnnotationSnapshot =>
          !!candidate && candidate.annotation_id === current.annotation.annotation_id && candidate.saved_revision !== null
          && candidate.saved_revision >= (current.annotation.saved_revision ?? 0);
        if (resolver) {
          try { baseline = await resolver(current); }
          catch { baseline = null; }
        }
        if (!isCurrent() || submitting.current()) return;
        // A supplied resolver owns both cache and server lookup. Don't issue
        // a second read if that resolver could not verify the baseline.
        if (!resolver && api) {
          try {
            const annotations = await allPages(cursor => api.call("listAnnotations", {
              path: {project_id: current.projectId},
              query: {cursor, limit: 100, version_id: current.annotation.target.version_id ?? undefined},
            }));
            baseline = annotations.find(isAcknowledged) ?? null;
          } catch { baseline = null; }
        }
        if (!isCurrent() || submitting.current()) return;
        if (!isAcknowledged(baseline)) {
          setError(baselineUnavailable);
          return;
        }
        setConfirmation({draft: current, baseline: structuredClone(baseline)});
        return;
      }

      await writes.current;
      await waitForDraftEdits(current);
      if (!isCurrent() || submitting.current()) return;
      const outcome = await discardDraftAtomically({
        projectId: current.projectId, key: current.key, expectedSnapshotJson: stableJson(current.annotation),
        expectedState: current.state, mode: policy.mode, cleanBaseline: baseline,
      });
      if (!isCurrent()) return;
      cancel();
      if (outcome.status === "deleted") {
        if (onDiscard) await onDiscard(current.key, "delete_new");
        else onClose();
      } else if (outcome.status === "reverted") {
        onChange?.(outcome.restored);
        await onDiscard?.(current.key, "revert_edits", outcome.restored);
      } else {
        // A key collision across projects is not permission to load the other project's draft.
        if (outcome.stored?.projectId === current.projectId) onChange?.(outcome.stored);
        setError(outcome.status === "protected"
          ? "This draft now has a save operation in progress or pending retry and cannot be discarded."
          : "This draft changed in another tab; refreshed the latest draft instead of discarding.");
      }
    } catch (e) {
      if (mounted.current) setError(`Could not discard draft: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      discardLatch.current = false;
      if (mounted.current) setDiscarding(false);
    }
  }

  return {policy, confirmingDiscard, discarding, discardLatch, cancel, discard};
}

export function AnnotationDockActions({discard, onDiscard, onClose, busy, resolveDiscardBaseline, children}: {
  discard: ReturnType<typeof useDraftDiscard>;
  onDiscard?: DiscardHandler;
  onClose: () => void;
  busy: boolean;
  resolveDiscardBaseline?: DiscardBaselineResolver;
  children: ReactNode;
}) {
  const {policy, confirmingDiscard, discarding} = discard;
  return <div className="toolbar-group annotation-dock-actions">
    {confirmingDiscard && policy.allowed ? <>
      <span className="discard-confirm-prompt" role="status">{policy.confirmPrompt}</span>
      <button className="toolbar-btn toolbar-btn-danger" disabled={busy || discarding} onClick={() => void discard.discard(onDiscard, onClose, true)}>Confirm discard</button>
      <button className="toolbar-btn" disabled={busy || discarding} onClick={discard.cancel}>Keep editing</button>
    </> : <>
      {policy.allowed && <button className="toolbar-btn toolbar-btn-subtle-danger" disabled={busy || discarding} onClick={() => void discard.discard(onDiscard, onClose, false, resolveDiscardBaseline)}>{policy.buttonLabel}</button>}
      {children}
    </>}
  </div>;
}

export function AnnotationTargetChip({annotation}: {annotation: AnnotationSnapshot}) {
  const {target, spatial_hits: [hit]} = annotation;
  const version = target.version_id ?? target.artifact_id;
  const label = shortVersionLabel(version);
  const shortVersion = label.length > 18 ? `…${label.slice(-15)}` : label;
  const frame = hit?.sample_frame ?? target.sequence_frame ?? target.source_frame ?? null;
  const suffix = frame === null ? " · image" : ` · frame ${frame}`;
  return <span className="toolbar-chip" title={`Pinned ${version}${suffix}`}>Pinned {shortVersion}{suffix}</span>;
}

export interface SpatialPinController {
  discard: ReturnType<typeof useDraftDiscard>;
  busy: boolean;
  placementCommitting?: boolean;
  saving: boolean;
  queueState: "idle" | "submitting" | "submitted";
  error: string;
  confirmImageOnly: boolean;
  setConfirmImageOnly: (value: boolean) => void;
  serverRevision: AnnotationSnapshot | null;
  editText: (text: string) => void;
  save: () => Promise<void>;
  prepareImageOnly: ContextPreparer;
  queue: () => Promise<void>;
  loadServerRevision: () => void;
  rebaseConflict: () => void;
}

export function useSpatialPinController({
  enabled,
  api,
  draft,
  versionKind,
  proxyUnavailableReason,
  onChange,
  onQueue,
  onContextPreparer,
  resolveDiscardBaseline,
  placementCommitting,
}: {
  enabled: boolean;
  api: ApiClient;
  draft: Draft | undefined;
  versionKind?: VersionRecord["kind"];
  proxyUnavailableReason: string | null;
  onChange: (draft: Draft) => void;
  onQueue: (text: string, context: TurnSelection) => void | Promise<void>;
  onContextPreparer?: (prepare: ContextPreparer | null) => void;
  resolveDiscardBaseline?: DiscardBaselineResolver;
  placementCommitting?: boolean;
}): SpatialPinController {
  const [saving, setSaving] = useState(false);
  const [queueState, setQueueState] = useState<"idle" | "submitting" | "submitted">("idle");
  const [error, setError] = useState("");
  const [confirmImageOnly, setConfirmImageOnly] = useState(false);
  const [serverRevision, setServerRevision] = useState<AnnotationSnapshot | null>(null);
  const saveLatch = useRef(false);
  const queueLatch = useRef(false);
  const queueInFlight = useRef(false);
  const submittedAnnotation = useRef<AnnotationSnapshot | null>(null);
  const mounted = useRef(true);
  const writes = useRef(Promise.resolve());
  const live = useRef(draft);
  live.current = draft;
  const discard = useDraftDiscard(draft, live, writes, () => Boolean(placementCommitting) || saveLatch.current || queueInFlight.current,
    setError, {resolveBaseline: resolveDiscardBaseline, api, onChange});

  const draftKey = draft?.key ?? null;
  useEffect(() => {
    setError("");
    setQueueState("idle");
    setConfirmImageOnly(false);
    setServerRevision(null);
    queueLatch.current = false;
    submittedAnnotation.current = null;
  }, [draftKey]);

  // The 2D markup editor can change this same draft without calling editText.
  useEffect(() => {
    if (queueState === "submitted" && draft?.state === "browser-local" && draft.annotation !== submittedAnnotation.current) {
      queueLatch.current = false;
      setQueueState("idle");
    }
  }, [draft, queueState]);

  const busy = Boolean(
    !draft
      || placementCommitting
      || saving
      || discard.discarding
      || queueState === "submitting"
      || draft.state === "saving"
      || draft.state === "conflict"
      || (!!draft.operation && draft.state !== "saved")
      || (!!draft.fallbackOperation && !draft.fallbackSaved),
  );

  function editText(text: string) {
    const current = live.current;
    if (!current || busy || saveLatch.current || queueInFlight.current || discard.discardLatch.current) return;
    discard.cancel();
    queueLatch.current = false;
    setQueueState("idle");
    const annotation = current.annotation;
    const hit = annotation.spatial_hits[0];
    const markups = hit && annotation.camera
      ? syncSpatialPinMarkup(annotation.markups, annotation.camera, hit, text)
      : annotation.markups;
    const updated: Draft = {
      ...current,
      annotation: {...annotation, text, markups},
      operation: undefined,
      state: "browser-local",
      error: undefined,
      markup: undefined,
      mask: undefined,
      markupUploadId: undefined,
      maskUploadId: undefined,
      fallbackOperation: undefined,
      fallbackSaved: undefined,
    };
    live.current = updated;
    onChange(updated);
    writes.current = saveDraftEdit(updated)
      .catch(e => setError(`Local draft could not be saved: ${String(e)}`));
  }

  async function renderArtifacts(frozen: Draft): Promise<Draft> {
    if (frozen.operation || frozen.markup) return frozen;
    const {width, height} = frozen.annotation.target.dimensions;
    let cleanBlob = frozen.targetBlob;
    if (!cleanBlob) {
      const bytes = await api.call("getArtifactContent", {
        path: {project_id: frozen.projectId, artifact_id: frozen.annotation.target.artifact_id},
      });
      cleanBlob = new Blob([bytes], {type: "image/png"});
    }
    const markupBlob = await renderCompositeFromBlob(cleanBlob, frozen.annotation.markups, width, height);
    let maskBlob: Blob | undefined;
    if (frozen.annotation.markups.some(m => m.kind === "mask" && m.text === VECTOR_MASK)) {
      const maskCanvas = document.createElement("canvas");
      maskCanvas.width = width;
      maskCanvas.height = height;
      const maskContext = maskCanvas.getContext("2d")!;
      maskContext.fillStyle = "black";
      maskContext.fillRect(0, 0, width, height);
      paintMarkups(maskContext, frozen.annotation.markups.filter(m => m.text === VECTOR_MASK), width, height, true);
      const rgba = maskContext.getImageData(0, 0, width, height).data;
      const maskPixels = new Uint8Array(width * height);
      for (let i = 0; i < maskPixels.length; i++) maskPixels[i] = rgba[i * 4] >= 128 ? 255 : 0;
      maskBlob = await greyPng(width, height, maskPixels);
    }
    const staged: Draft = {...frozen, operation: undefined, markup: markupBlob, mask: maskBlob};
    await saveDraft(staged);
    live.current = staged;
    if (mounted.current) onChange(staged);
    return staged;
  }

  async function prepareSavedDraft(): Promise<Draft> {
    if (placementCommitting || saveLatch.current || queueInFlight.current || discard.discardLatch.current) throw new Error("Annotation operation is already in progress");
    discard.cancel();
    const current = live.current;
    if (!current) throw new Error("No active spatial annotation draft");
    let frozen = structuredClone(current);
    if (frozen.state === "conflict") throw new Error(frozen.error || "Resolve the annotation revision conflict before sending");
    if (frozen.state === "saved" && (frozen.annotation.markup_artifact_id || !frozen.annotation.markups.length)) return frozen;
    saveLatch.current = true;
    setSaving(true);
    setError("");
    try {
      await writes.current;
      await waitForDraftEdits(frozen);
      frozen = await renderArtifacts(frozen);
      const saved = await flushDraft(api, frozen, next => {
        live.current = next;
        if (mounted.current) onChange(next);
      });
      if (saved.state !== "saved") throw new Error(saved.error || `Annotation save ${saved.state}`);
      return saved;
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
      throw e;
    } finally {
      saveLatch.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  async function prepareFallbackSnapshot(): Promise<AnnotationSnapshot> {
    const current = live.current;
    if (!current) throw new Error("No active spatial annotation draft");
    if (current.fallbackSaved) return current.fallbackSaved;
    let working = structuredClone(current);
    await writes.current;
    await waitForDraftEdits(working);
    // Ensure clean target + composite markup exist on server even if proxy is offline.
    if (!working.fallbackOperation) {
      if (working.state !== "saved" || (!working.annotation.markup_artifact_id && working.annotation.markups.length)) {
        working = await renderArtifacts(working);
      }
      // Uploads belong to the derivative. The original annotation in the outbox
      // must still match its own saved revision, even if an upload is retried.
      const annotation = structuredClone(working.annotation);
      if (working.targetBlob && !working.targetUploaded) {
        if (!working.targetUploadId) {
          working = {...working, targetUploadId: crypto.randomUUID()};
          await saveDraft(working);
          live.current = working;
          if (mounted.current) onChange(working);
        }
        const targetArtifact = await api.call("uploadImage", {
          path: {project_id: working.projectId, upload_id: working.targetUploadId!},
          body: working.targetBlob,
          idempotencyKey: working.targetUploadId!,
        });
        annotation.target.artifact_id = targetArtifact.artifact_id;
      }
      if (!annotation.markup_artifact_id && working.markup) {
        if (!working.markupUploadId) {
          working = {...working, markupUploadId: crypto.randomUUID()};
          await saveDraft(working);
        }
        const markupArtifact = await api.call("uploadImage", {
          path: {project_id: working.projectId, upload_id: working.markupUploadId!},
          body: working.markup,
          idempotencyKey: working.markupUploadId!,
        });
        annotation.markup_artifact_id = markupArtifact.artifact_id;
      }
      const derived: AnnotationSnapshot = {
        ...annotation,
        annotation_id: crypto.randomUUID(),
        saved_revision: null,
        spatial_hits: [],
        spatial_status: "unavailable",
        ...(annotation.saved_revision ? {derived_from: annotation.annotation_id} : {}),
      };
      const fallbackOperation: AnnotationSave = {
        schema_version: "1.0",
        project_id: working.projectId,
        operation_id: crypto.randomUUID(),
        expected_revision: 0,
        annotation: derived,
      };
      working = {...working, fallbackOperation};
      await saveDraft(working);
      live.current = working;
      if (mounted.current) onChange(working);
    }
    const ack = await api.call("saveAnnotation", {
      path: {project_id: working.projectId},
      body: working.fallbackOperation!,
      idempotencyKey: working.fallbackOperation!.operation_id,
    });
    const fallbackSaved: AnnotationSnapshot = {
      ...working.fallbackOperation!.annotation,
      saved_revision: ack.state_revision,
    };
    working = {...working, fallbackSaved};
    await saveDraft(working);
    live.current = working;
    if (mounted.current) onChange(working);
    return fallbackSaved;
  }

  const prepareRef = useRef<ContextPreparer>(async () => {
    throw new Error("Spatial annotation preparation is not ready");
  });
  async function prepareImageOnly(): Promise<TurnSelection> {
    if (!confirmImageOnly) {
      throw new Error("Original 3D proxy is unavailable. Confirm image-only fallback in Review before sending.");
    }
    if (placementCommitting || saveLatch.current || queueInFlight.current || discard.discardLatch.current) throw new Error("Annotation operation is already in progress");
    discard.cancel();
    saveLatch.current = true;
    setSaving(true);
    try {
      return annotationContext(await prepareFallbackSnapshot(), versionKind);
    } finally {
      saveLatch.current = false;
      if (mounted.current) setSaving(false);
    }
  }
  prepareRef.current = async () => proxyUnavailableReason ? prepareImageOnly()
    : structuredClone(annotationContext((await prepareSavedDraft()).annotation, versionKind));

  // The controller remains alive for the 2D editor's image-only outbox. Only
  // its authority to register the default context preparer follows the 3D dock.
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useLayoutEffect(() => {
    if (!enabled) return;
    onContextPreparer?.(() => prepareRef.current());
    return () => onContextPreparer?.(null);
  }, [enabled, onContextPreparer]);

  async function save() {
    try { await prepareSavedDraft(); } catch { /* Retained in outbox and error banner. */ }
  }

  async function queue() {
    if (placementCommitting || queueLatch.current || saveLatch.current || discard.discardLatch.current || !live.current) return;
    if (proxyUnavailableReason && !confirmImageOnly) {
      setError("Confirm image-only fallback before sending without live 3D proxy verification.");
      return;
    }
    discard.cancel();
    queueLatch.current = true;
    setQueueState("submitting");
    queueInFlight.current = true;
    setError("");
    try {
      const snapshot = proxyUnavailableReason
        ? await prepareFallbackSnapshot()
        : structuredClone(live.current.annotation);
      await onQueue(snapshot.text || "Apply this annotated feedback.", annotationContext(snapshot, versionKind));
      submittedAnnotation.current = live.current?.annotation ?? null;
      setQueueState("submitted");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      queueLatch.current = false;
      setQueueState("idle");
    } finally {
      queueInFlight.current = false;
    }
  }

  function loadServerRevision() {
    const current = live.current;
    if (!current) return;
    void allPages(cursor => api.call("listAnnotations", {
      path: {project_id: current.projectId},
      query: {cursor, limit: 100, version_id: current.annotation.target.version_id ?? undefined},
    }))
      .then(items => {
        const found = items.find(a => a.annotation_id === current.annotation.annotation_id);
        if (!found) throw new Error("Current annotation unavailable");
        setServerRevision(found);
      })
      .catch(e => setError(String(e)));
  }

  function rebaseConflict() {
    const current = live.current;
    if (!current || !serverRevision) return;
    if (stableJson(serverRevision.target) !== stableJson(current.annotation.target)) {
      setError("Server target differs. Retargeting requires a new annotation.");
      return;
    }
    const rebased: Draft = {
      ...current,
      annotation: {...current.annotation, saved_revision: serverRevision.saved_revision},
      operation: undefined,
      state: "browser-local",
      error: undefined,
      supersededOperations: [...(current.supersededOperations ?? []), ...(current.operation ? [current.operation] : [])],
    };
    void saveDraft(rebased)
      .then(() => {
        live.current = rebased;
        onChange(rebased);
        setServerRevision(null);
      })
      .catch(e => setError(String(e)));
  }

  return {
    discard,
    busy,
    placementCommitting,
    saving,
    queueState,
    error,
    confirmImageOnly,
    setConfirmImageOnly,
    serverRevision,
    editText,
    save,
    prepareImageOnly,
    queue,
    loadServerRevision,
    rebaseConflict,
  };
}

export function SpatialPinHeader({
  draft,
  pinMode,
  proxyUnavailableReason,
  controller,
  onTogglePinMode,
  onRestoreView,
  onEdit2D,
  placementCommitting,
}: {
  draft: Draft;
  pinMode: boolean;
  proxyUnavailableReason: string | null;
  controller: SpatialPinController;
  onTogglePinMode: () => void;
  onRestoreView: () => void;
  onEdit2D: () => void;
  onClose?: () => void;
  placementCommitting?: boolean;
}) {
  const hit = draft.annotation.spatial_hits[0];
  const busy = controller.busy || Boolean(placementCommitting);
  return (
    <div className="review-bar" aria-label="Spatial pin tools">
      {hit && <span className="toolbar-chip">{hit.object_id} · {hit.instance_id}</span>}
      {!proxyUnavailableReason && (
        <div className="toolbar-group">
          <button className="toolbar-btn" disabled={Boolean(placementCommitting || controller.placementCommitting)} onClick={onRestoreView}><RotateCcw size={13}/>Restore pinned view</button>
          {draft.annotation.saved_revision === null && (
            <button className="toolbar-btn" aria-pressed={pinMode} disabled={busy} onClick={() => { controller.discard.cancel(); onTogglePinMode(); }}>
              <MapPin size={13}/>Reposition pin
            </button>
          )}
          <button className="toolbar-btn" disabled={busy} onClick={onEdit2D}><PenLine size={13}/>Edit 2D markup</button>
        </div>
      )}
      <div className="toolbar-spacer"/>
      <output className="toolbar-chip" aria-live="polite">{formatDraftStateBadge(draft)}</output>
    </div>
  );
}

export function SpatialPinFallbackView({
  draft,
  reason,
  controller,
}: {
  draft: Draft;
  reason: string;
  controller: SpatialPinController;
}) {
  const annotation = draft.annotation;
  const {width: iw, height: ih} = annotation.target.dimensions;
  const localBlob = draft.markup ?? (!draft.targetUploaded ? draft.targetBlob : undefined);
  const localUrl = useMemo(() => localBlob ? URL.createObjectURL(localBlob) : null, [localBlob]);
  useEffect(() => () => { if (localUrl) URL.revokeObjectURL(localUrl); }, [localUrl]);
  const snapshotArtifact = annotation.markup_artifact_id ?? annotation.target.artifact_id;
  const imageSrc = localUrl ?? mediaUrl(draft.projectId, snapshotArtifact);
  const showSvgOverlay = !draft.markup && !annotation.markup_artifact_id;

  return (
    <div className="review-canvas spatial-fallback-view" aria-label="Unavailable proxy marked snapshot">
      <img src={imageSrc} alt="Marked spatial capture snapshot" draggable={false}/>
      {showSvgOverlay && (
        <svg viewBox={`0 0 ${iw} ${ih}`} preserveAspectRatio="xMidYMid meet" aria-label="Recorded 2D pin projection">
          {annotation.markups.filter(m => m.kind === "pin").map(m => (
            <g key={m.id}>
              <circle cx={m.points[0][0] * iw} cy={m.points[0][1] * ih} r={Math.max(5, m.width_px ?? 6)} fill={m.colour ?? "#ff5555"}/>
              <text x={m.points[0][0] * iw + 10} y={m.points[0][1] * ih} fill={m.colour ?? "#ff5555"} fontSize={Math.max(14, iw / 60)}>{m.text}</text>
            </g>
          ))}
        </svg>
      )}
      <SpatialFallbackNotice annotation={annotation} reason={reason} controller={controller}/>
    </div>
  );
}

export function SpatialFallbackNotice({ annotation, reason, controller, inline, disabled }: {
  annotation: AnnotationSnapshot;
  reason: string;
  controller: Pick<SpatialPinController, "confirmImageOnly" | "setConfirmImageOnly">;
  inline?: boolean;
  disabled?: boolean;
}) {
  const hit = annotation.spatial_hits[0];
  return <div className={`spatial-fallback-banner${inline ? " spatial-fallback-inline" : ""}`} role="alert">
    <strong>Original 3D proxy is unavailable ({hit ? `${hit.proxy_artifact_id} / ${hit.mapping_artifact_id}` : "unverified"}): {reason}</strong>
    <span>The marked 2D snapshot remains viewable on {annotation.target.version_id}, and will not be retargeted onto newer geometry.</span>
    <label className="spatial-fallback-confirm">
      <input type="checkbox" checked={controller.confirmImageOnly} disabled={disabled} onChange={e => controller.setConfirmImageOnly(e.target.checked)}/>
      <span>Send image-only snapshot without 3D spatial hit</span>
    </label>
  </div>;
}

export function SpatialPinDock({
  draft,
  proxyUnavailableReason,
  controller,
  onClose = noop,
  onDiscard,
  resolveDiscardBaseline,
  placementCommitting,
}: {
  draft: Draft;
  proxyUnavailableReason: string | null;
  controller: SpatialPinController;
  onClose?: () => void;
  onDiscard?: DiscardHandler;
  resolveDiscardBaseline?: DiscardBaselineResolver;
  placementCommitting?: boolean;
}) {
  const annotation = draft.annotation;
  const committingPlacement = Boolean(placementCommitting || controller.placementCommitting);
  const busy = controller.busy || committingPlacement;
  const busyClose = committingPlacement || controller.saving || controller.queueState === "submitting" || controller.discard.discarding;
  const statusMessage = controller.error || (draft.state === "failed" ? draft.error : "") || (draft.state === "conflict"
    ? "Saved annotation changed elsewhere. This draft and its original operation are retained. Reload the server revision before making a new edit." : "");
  const canQueueNormal = !proxyUnavailableReason && !controller.saving && draft.state === "saved" && controller.queueState === "idle";
  const canQueueFallback = Boolean(
    proxyUnavailableReason
      && !controller.saving
      && controller.confirmImageOnly
      && controller.queueState === "idle"
      && (draft.state === "saved" || annotation.saved_revision === null),
  );

  return (
    <>
      <div className="annotation-dock">
        <div className="annotation-feedback">
          <label className="sr-only" htmlFor="spatial-pin-feedback">Feedback</label>
          <textarea
            id="spatial-pin-feedback"
            aria-label="Feedback"
            placeholder="Describe the change for Copilot at this 3D surface pin…"
            disabled={busy}
            value={annotation.text}
            onChange={e => controller.editText(e.target.value)}
            onKeyDown={e => handleSubmitEnter(e, () => {
              if (busyClose) return;
              if (canQueueNormal || canQueueFallback) void controller.queue();
              else if (!proxyUnavailableReason && draft.state !== "saving" && draft.state !== "conflict") void controller.save();
            })}
          />
        </div>
        <div className="review-bar review-transport-bar">
          <div className="toolbar-group annotation-dock-target">
            <button className="toolbar-btn" disabled={busyClose} onClick={onClose}>{draft.state === "saved" ? "Close" : "Keep draft & close"}</button>
            <AnnotationTargetChip annotation={annotation}/>
          </div>
          <AnnotationDockActions discard={controller.discard} onDiscard={onDiscard} onClose={onClose} busy={busy} resolveDiscardBaseline={resolveDiscardBaseline}>
            {!proxyUnavailableReason && (
              <button
                className="toolbar-btn"
                disabled={busyClose || draft.state === "saving" || draft.state === "conflict"}
                onClick={() => void controller.save()}
              >
                {draft.operation && draft.state !== "saved" ? "Retry original save" : "Save annotation"}
              </button>
            )}
            <button
              className="toolbar-btn toolbar-btn-accent"
              disabled={busyClose || !(canQueueNormal || canQueueFallback)}
              onClick={() => void controller.queue()}
            >
              {controller.queueState === "submitted"
                ? "Feedback queued"
                : controller.queueState === "submitting"
                  ? "Submitting feedback"
                  : proxyUnavailableReason
                    ? "Queue image-only feedback"
                    : "Queue feedback"}
            </button>
          </AnnotationDockActions>
        </div>
      </div>
      {statusMessage && <div className="review-status-banner annotation-status-banner" role="alert">{statusMessage}</div>}
      {draft.state === "conflict" && (
        <button onClick={controller.loadServerRevision}>Load server revision for comparison</button>
      )}
      {draft.state === "conflict" && controller.serverRevision && (
        <div>
          <p>Server revision {controller.serverRevision.saved_revision}: {controller.serverRevision.text}</p>
          <button onClick={controller.rebaseConflict}>
            Rebase local edits onto revision {controller.serverRevision.saved_revision}
          </button>
        </div>
      )}
    </>
  );
}
