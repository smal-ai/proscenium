import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { MapPin, PenLine, RotateCcw, X } from "lucide-react";
import type { AnnotationSave, AnnotationSnapshot } from "../../api/generated/contracts";
import type { ApiClient } from "../../api/client";
import type { ContextPreparer, TurnSelection } from "../../api/studio";
import { annotationContext } from "./context";
import { mediaUrl, stableJson } from "./media";
import { flushDraft, saveDraft, VECTOR_MASK, type Draft } from "./outbox";
import { greyPng } from "./png";
import { allPages } from "./records";
import { paintMarkups, renderCompositeFromBlob, syncSpatialPinMarkup } from "./spatial";

export interface SpatialPinController {
  busy: boolean;
  saving: boolean;
  queueState: "idle" | "submitting" | "submitted";
  error: string;
  confirmImageOnly: boolean;
  setConfirmImageOnly: (value: boolean) => void;
  serverRevision: AnnotationSnapshot | null;
  editText: (text: string) => void;
  save: () => Promise<void>;
  queue: () => Promise<void>;
  loadServerRevision: () => void;
  rebaseConflict: () => void;
}

export function useSpatialPinController({
  enabled,
  api,
  draft,
  proxyUnavailableReason,
  onChange,
  onQueue,
  onContextPreparer,
}: {
  enabled: boolean;
  api: ApiClient;
  draft: Draft | undefined;
  proxyUnavailableReason: string | null;
  onChange: (draft: Draft) => void;
  onQueue: (text: string, context: TurnSelection) => void | Promise<void>;
  onContextPreparer?: (prepare: ContextPreparer | null) => void;
}): SpatialPinController {
  const [saving, setSaving] = useState(false);
  const [queueState, setQueueState] = useState<"idle" | "submitting" | "submitted">("idle");
  const [error, setError] = useState("");
  const [confirmImageOnly, setConfirmImageOnly] = useState(false);
  const [serverRevision, setServerRevision] = useState<AnnotationSnapshot | null>(null);
  const saveLatch = useRef(false);
  const queueLatch = useRef(false);
  const queueInFlight = useRef(false);
  const mounted = useRef(true);
  const writes = useRef(Promise.resolve());
  const live = useRef(draft);
  live.current = draft;

  const draftKey = draft?.key ?? null;
  useEffect(() => {
    setError("");
    setQueueState("idle");
    setConfirmImageOnly(false);
    setServerRevision(null);
    queueLatch.current = false;
  }, [draftKey]);

  const busy = Boolean(
    !draft
      || saving
      || queueState === "submitting"
      || draft.state === "saving"
      || (!!draft.operation && draft.state !== "saved"),
  );

  function editText(text: string) {
    const current = live.current;
    if (!current || saveLatch.current || queueInFlight.current) return;
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
    writes.current = writes.current
      .then(() => saveDraft(updated))
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
    if (saveLatch.current || queueInFlight.current) throw new Error("Annotation submission is already in progress");
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
    // Ensure clean target + composite markup exist on server even if proxy is offline.
    if (!working.fallbackOperation) {
      working = await renderArtifacts(working);
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
        working = {...working, annotation, targetUploaded: true};
        await saveDraft(working);
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
        working = {...working, annotation};
        await saveDraft(working);
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
  prepareRef.current = async () => {
    if (proxyUnavailableReason) {
      if (!confirmImageOnly) {
        throw new Error("Original 3D proxy is unavailable. Confirm image-only fallback in Review before sending.");
      }
      return structuredClone(annotationContext(await prepareFallbackSnapshot()));
    }
    return structuredClone(annotationContext((await prepareSavedDraft()).annotation));
  };

  useLayoutEffect(() => {
    mounted.current = true;
    if (!enabled) return () => { mounted.current = false; };
    onContextPreparer?.(() => prepareRef.current());
    return () => {
      mounted.current = false;
      onContextPreparer?.(null);
    };
  }, [enabled, onContextPreparer]);

  async function save() {
    try { await prepareSavedDraft(); } catch { /* Retained in outbox and error banner. */ }
  }

  async function queue() {
    if (queueLatch.current || saveLatch.current || !live.current) return;
    if (proxyUnavailableReason && !confirmImageOnly) {
      setError("Confirm image-only fallback before sending without live 3D proxy verification.");
      return;
    }
    queueLatch.current = true;
    setQueueState("submitting");
    queueInFlight.current = true;
    setError("");
    try {
      const snapshot = proxyUnavailableReason
        ? await prepareFallbackSnapshot()
        : structuredClone(live.current.annotation);
      await onQueue(snapshot.text || "Apply this annotated feedback.", annotationContext(snapshot));
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
    busy,
    saving,
    queueState,
    error,
    confirmImageOnly,
    setConfirmImageOnly,
    serverRevision,
    editText,
    save,
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
  onClose,
}: {
  draft: Draft;
  pinMode: boolean;
  proxyUnavailableReason: string | null;
  controller: SpatialPinController;
  onTogglePinMode: () => void;
  onRestoreView: () => void;
  onEdit2D: () => void;
  onClose: () => void;
}) {
  const hit = draft.annotation.spatial_hits[0];
  return (
    <div className="review-bar" aria-label="Spatial pin tools">
      <span className="toolbar-badge accent">3D Pin</span>
      {hit && <span className="toolbar-chip">{hit.object_id} · {hit.instance_id}</span>}
      {!proxyUnavailableReason && (
        <div className="toolbar-group">
          <button className="toolbar-btn" onClick={onRestoreView}><RotateCcw size={13}/>Restore pinned view</button>
          {draft.annotation.saved_revision === null && (
            <button className="toolbar-btn" aria-pressed={pinMode} disabled={controller.busy} onClick={onTogglePinMode}>
              <MapPin size={13}/>Reposition pin
            </button>
          )}
          <button className="toolbar-btn" disabled={controller.busy} onClick={onEdit2D}><PenLine size={13}/>Edit 2D markup</button>
        </div>
      )}
      <div className="toolbar-spacer"/>
      <output className="toolbar-chip" aria-live="polite">{draft.state}{draft.error ? `: ${draft.error}` : ""}</output>
      <button className="toolbar-btn" disabled={controller.saving || controller.queueState === "submitting"} onClick={onClose}>
        <X size={14}/>Close draft
      </button>
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
  const hit = annotation.spatial_hits[0];

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
      <div className="spatial-fallback-banner" role="alert">
        <strong>Original 3D proxy is unavailable ({hit ? `${hit.proxy_artifact_id} / ${hit.mapping_artifact_id}` : "unverified"}): {reason}</strong>
        <span>The marked 2D snapshot remains viewable on {annotation.target.version_id}, and will not be retargeted onto newer geometry.</span>
        <label className="spatial-fallback-confirm">
          <input
            type="checkbox"
            checked={controller.confirmImageOnly}
            onChange={e => controller.setConfirmImageOnly(e.target.checked)}
          />
          <span>Send image-only snapshot without 3D spatial hit</span>
        </label>
      </div>
    </div>
  );
}

export function SpatialPinDock({
  draft,
  proxyUnavailableReason,
  controller,
}: {
  draft: Draft;
  proxyUnavailableReason: string | null;
  controller: SpatialPinController;
}) {
  const annotation = draft.annotation;
  const hit = annotation.spatial_hits[0];
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
            disabled={controller.busy}
            value={annotation.text}
            onChange={e => controller.editText(e.target.value)}
          />
        </div>
        <div className="review-bar review-transport-bar">
          <span className="toolbar-chip">
            Pinned {annotation.target.version_id ?? annotation.target.artifact_id} · frame {hit?.sample_frame ?? annotation.target.source_frame ?? 1}
            {hit ? ` · ${hit.instance_id}` : ""}
          </span>
          <div className="toolbar-spacer"/>
          <div className="toolbar-group">
            {!proxyUnavailableReason && (
              <button
                className="toolbar-btn"
                disabled={controller.saving || controller.queueState === "submitting" || draft.state === "saving" || draft.state === "conflict"}
                onClick={() => void controller.save()}
              >
                {draft.operation && draft.state !== "saved" ? "Retry original save" : "Save annotation"}
              </button>
            )}
            <button
              className="toolbar-btn toolbar-btn-accent"
              disabled={!(canQueueNormal || canQueueFallback)}
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
          </div>
        </div>
      </div>
      {(controller.error || draft.state === "conflict") && (
        <div className="review-status-banner" role="alert">
          {controller.error || "Saved annotation changed elsewhere. This draft and its original operation are retained. Reload the server revision before making a new edit."}
        </div>
      )}
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
