import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Box, Eraser, Layers, MapPin, MoveUpRight, Pencil, Square } from "lucide-react";
import type { AnnotationSnapshot, Markup, VersionRecord } from "../../api/generated/contracts";
import type { ApiClient } from "../../api/client";
import type { ContextPreparer, TurnSelection } from "../../api/studio";
import { handleSubmitEnter } from "../chat/controls";
import { contentRect, imagePoint, mediaUrl, stableJson } from "./media";
import { flushDraft, formatDraftStateBadge, saveDraft, saveDraftEdit, waitForDraftEdits, VECTOR_MASK, type Draft } from "./outbox";
import { greyPng } from "./png";
import { annotationContext } from "./context";
import { allPages } from "./records";
import { canvasPngBlob, paintMarkups, syncSpatialPinMarkup } from "./spatial";
import { AnnotationDockActions, AnnotationTargetChip, useDraftDiscard, type DiscardBaselineResolver, type DiscardHandler } from "./SpatialPinDock";
import "./annotationDock.css";

type Tool = Markup["kind"] | "eraser";
const tools: Tool[] = ["stroke", "arrow", "region", "pin", "eraser", "mask"];
const toolIcons = {stroke: Pencil, arrow: MoveUpRight, region: Square, pin: MapPin, eraser: Eraser, mask: Layers};

export function AnnotationEditor({api, draft, versionKind, onChange, onQueue, onClose, onDiscard, onReturnTo3D, onContextPreparer, resolveDiscardBaseline}: {
  api: ApiClient; draft: Draft; versionKind?: VersionRecord["kind"]; onChange: (draft: Draft) => void;
  onQueue: (text: string, context: TurnSelection) => void | Promise<void>; onClose: () => void;
  onDiscard?: DiscardHandler;
  onReturnTo3D?: () => void;
  onContextPreparer?: (prepare: ContextPreparer | null) => void;
  resolveDiscardBaseline?: DiscardBaselineResolver;
}) {
  const [tool, setTool] = useState<Tool>("stroke");
  const [width, setWidth] = useState(4);
  const [pinText, setPinText] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const saveLatch = useRef(false);
  const [queueState, setQueueState] = useState<"idle" | "submitting" | "submitted">("idle");
  const queueLatch = useRef(false);
  const queueInFlight = useRef(false);
  const mounted = useRef(true);
  const [serverRevision, setServerRevision] = useState<AnnotationSnapshot | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const active = useRef<string | null>(null);
  const live = useRef(draft); live.current = draft;
  const writes = useRef(Promise.resolve());
  const discard = useDraftDiscard(draft, live, writes, () => saveLatch.current || queueInFlight.current,
    setError, {resolveBaseline: resolveDiscardBaseline, api, onChange});
  const busyClose = saving || queueState === "submitting" || discard.discarding;
  const busy = busyClose || draft.state === "saving" || draft.state === "conflict"
    || (!!draft.operation && draft.state !== "saved") || (!!draft.fallbackOperation && !draft.fallbackSaved);
  const annotation = draft.annotation;
  const statusMessage = error || (draft.state === "failed" ? draft.error : "") || (draft.state === "conflict"
    ? "Saved annotation changed elsewhere. This draft and its original operation are retained. Reload the server revision before making a new edit." : "");
  const {width: iw, height: ih} = annotation.target.dimensions;
  const localTargetUrl = useMemo(() => draft.targetBlob && !draft.targetUploaded ? URL.createObjectURL(draft.targetBlob) : null, [draft.targetBlob, draft.targetUploaded]);
  useEffect(() => () => { if (localTargetUrl) URL.revokeObjectURL(localTargetUrl); }, [localTargetUrl]);
  const targetImageSrc = localTargetUrl ?? mediaUrl(draft.projectId, annotation.target.artifact_id);

  function edit(next: AnnotationSnapshot) {
    if (busy || saveLatch.current || queueInFlight.current || discard.discardLatch.current) return;
    discard.cancel();
    queueLatch.current = false;
    setQueueState("idle");
    const hit = next.spatial_hits[0];
    const normalized = hit && next.camera
      ? {...next, markups: syncSpatialPinMarkup(next.markups, next.camera, hit, next.text)}
      : next;
    const updated: Draft = {...live.current, annotation: normalized, operation: undefined, state: "browser-local", error: undefined, markup: undefined, mask: undefined, markupUploadId: undefined, maskUploadId: undefined, fallbackOperation: undefined, fallbackSaved: undefined};
    live.current = updated; onChange(updated);
    writes.current = saveDraftEdit(updated).catch(e => setError(`Local draft could not be saved: ${String(e)}`));
  }

  function point(event: React.PointerEvent) {
    if (!host.current) return null;
    return imagePoint(event.clientX, event.clientY, contentRect(host.current.getBoundingClientRect(), iw, ih));
  }

  function start(event: React.PointerEvent<HTMLDivElement>) {
    if (busy || saveLatch.current || queueInFlight.current || discard.discardLatch.current || event.button !== 0) return;
    const p = point(event); if (!p) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === "eraser") {
      const pinnedId = annotation.spatial_hits.length > 0 ? annotation.markups.find(m => m.kind === "pin")?.id : undefined;
      const nearest = [...annotation.markups].reverse().find(mark => mark.id !== pinnedId && mark.points.some(q => Math.hypot((q[0]-p[0])*iw, (q[1]-p[1])*ih) < Math.max(16, width)));
      if (nearest) edit({...annotation, markups: annotation.markups.filter(m => m.id !== nearest.id)});
      return;
    }
    if (tool === "pin" && !pinText.trim()) { setError("Enter pin text before placing it."); return; }
    const id = crypto.randomUUID(); active.current = id;
    const mark: Markup = {id, kind: tool, points: [p], width_px: width, colour: "#ff5555",
      ...(tool === "pin" ? {text: pinText} : {}),
      ...(tool === "mask" ? {text:VECTOR_MASK, mask_artifact_id: "browser-local-mask", mask_encoding: {channel: "luma" as const, bit_depth: 8 as const, selected_value: 255 as const, unselected_value: 0 as const, semantics: "binary" as const}} : {})};
    edit({...annotation, markups: [...annotation.markups, mark]});
  }

  function move(event: React.PointerEvent<HTMLDivElement>) {
    if (!active.current || busy || saveLatch.current || queueInFlight.current) return;
    const p = point(event);
    // End the geometry at the content edge. Never manufacture edge points outside the image.
    if (!p) { active.current = null; return; }
    edit({...live.current.annotation, markups: live.current.annotation.markups.map(m => m.id === active.current && m.kind !== "pin"
      ? {...m, points: m.kind === "arrow" ? [m.points[0], p] : [...m.points, p]} : m)});
  }

  async function prepareSavedDraft(): Promise<Draft> {
    if (saveLatch.current || queueInFlight.current || discard.discardLatch.current) throw new Error("Annotation operation is already in progress");
    discard.cancel();
    // Freeze both the editable record and intrinsic canvas pixels before any await.
    // A later navigation/unmount cannot replace this target with the new viewport.
    let frozen = structuredClone(live.current);
    if (frozen.state === "conflict") throw new Error(frozen.error || "Resolve the annotation revision conflict before sending");
    if (frozen.state === "saved" && (frozen.annotation.markup_artifact_id || !frozen.annotation.markups.length)) return frozen;
    saveLatch.current = true; setSaving(true);
    active.current = null;
    setError("");
    try {
      let markupCanvas: HTMLCanvasElement | undefined;
      let maskPixels: Uint8Array | undefined;
      const {width, height} = frozen.annotation.target.dimensions;
      if (!frozen.operation && !frozen.markup) {
        const clean = image.current;
        const expectedSource = localTargetUrl ?? new URL(mediaUrl(frozen.projectId, frozen.annotation.target.artifact_id), location.href).href;
        if (!clean?.complete || clean.currentSrc !== expectedSource || clean.naturalWidth !== width || clean.naturalHeight !== height) throw new Error("Target image is not decoded at its recorded identity and dimensions");
        markupCanvas = document.createElement("canvas"); markupCanvas.width = width; markupCanvas.height = height;
        const ctx = markupCanvas.getContext("2d")!;
        ctx.drawImage(clean, 0, 0); paintMarkups(ctx, frozen.annotation.markups, width, height, false);
        if (frozen.annotation.markups.some(m => m.kind === "mask" && m.text === VECTOR_MASK)) {
          const maskCanvas = document.createElement("canvas"); maskCanvas.width = width; maskCanvas.height = height;
          const maskContext = maskCanvas.getContext("2d")!;
          maskContext.fillStyle = "black"; maskContext.fillRect(0, 0, width, height);
          paintMarkups(maskContext, frozen.annotation.markups.filter(m => m.text === VECTOR_MASK), width, height, true);
          const rgba = maskContext.getImageData(0, 0, width, height).data;
          maskPixels = new Uint8Array(width * height);
          for (let i = 0; i < maskPixels.length; i++) maskPixels[i] = rgba[i * 4] >= 128 ? 255 : 0;
        }
      }
      await writes.current;
      await waitForDraftEdits(frozen);
      if (markupCanvas) {
        frozen = {...frozen, operation: undefined, markup: await canvasPngBlob(markupCanvas),
          mask: maskPixels ? await greyPng(width, height, maskPixels) : undefined};
        await saveDraft(frozen);
      }
      const saved = await flushDraft(api, frozen, next => {
        live.current = next;
        if (mounted.current) onChange(next);
      });
      if (saved.state !== "saved") throw new Error(saved.error || `Annotation save ${saved.state}`);
      return saved;
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
      throw e;
    } finally {saveLatch.current = false; if (mounted.current) setSaving(false);}
  }

  const prepare = async () => structuredClone(annotationContext((await prepareSavedDraft()).annotation, versionKind));
  const prepareRef = useRef<ContextPreparer>(prepare);
  prepareRef.current = prepare;
  useLayoutEffect(() => {
    mounted.current = true;
    onContextPreparer?.(() => prepareRef.current());
    return () => {mounted.current = false; onContextPreparer?.(null);};
  }, [onContextPreparer]);

  async function save() {try {await prepareSavedDraft();} catch { /* The failed outbox and visible error are retained. */ }}

  async function queue() {
    if (queueLatch.current || saveLatch.current || discard.discardLatch.current) return;
    discard.cancel();
    const snapshot = structuredClone(live.current.annotation);
    queueLatch.current = true; setQueueState("submitting");
    queueInFlight.current = true;
    try {await onQueue(snapshot.text || "Apply this annotated feedback.", annotationContext(snapshot, versionKind)); setQueueState("submitted");}
    catch (e) {setError(String(e)); queueLatch.current = false; setQueueState("idle");}
    finally {queueInFlight.current = false;}
  }

  return <section className="review-annotation" aria-label="Pinned annotation">
    <div className="review-bar" aria-label="Annotation drawing tools">
      <div className="segmented-control">{tools.map(t => {const Icon = toolIcons[t]; return <button key={t} aria-pressed={tool === t} disabled={busy} onClick={() => setTool(t)}><Icon size={14}/>{t[0].toUpperCase() + t.slice(1)}</button>;})}</div>
      <div className="toolbar-divider"/>
      <label className="toolbar-field"><span>Size</span><input aria-label="Width (image px)" disabled={busy} type="number" min="1" max="256" value={width} onChange={e => setWidth(Math.min(256, Math.max(1, Number(e.target.value))))} /><span>px</span></label>
      {tool === "pin" && <input className="toolbar-inline-input" aria-label="Pin text" placeholder="Pin label…" disabled={busy} value={pinText} onChange={e => { discard.cancel(); setPinText(e.target.value); }} />}
      <div className="toolbar-spacer"/>
      {onReturnTo3D && <button className="toolbar-btn" disabled={busyClose} onClick={onReturnTo3D}><Box size={14}/>Back to 3D view</button>}
      <output className="toolbar-chip" aria-live="polite">{formatDraftStateBadge(draft)}</output>
    </div>
    <div className="review-canvas" ref={host} onPointerDown={start} onPointerMove={move} onPointerUp={() => {active.current = null;}} onPointerCancel={() => {active.current = null;}}>
      <img ref={image} src={targetImageSrc} alt="Pinned clean annotation target" draggable={false} onError={() => setError(`media_unavailable: ${annotation.target.artifact_id}`)} />
      <svg viewBox={`0 0 ${iw} ${ih}`} preserveAspectRatio="xMidYMid meet" aria-label="Editable image-space markup">
        <defs><clipPath id="image-bounds"><rect width={iw} height={ih}/></clipPath><marker id="arrow-head" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#ff5555"/></marker></defs>
        <g clipPath="url(#image-bounds)">{annotation.markups.map(m => <g key={m.id}>
          {m.kind === "mask" && m.text !== VECTOR_MASK ? <><defs><mask id={`raster-${m.id}`}><image href={mediaUrl(draft.projectId,m.mask_artifact_id!)} width={iw} height={ih}/></mask></defs><rect width={iw} height={ih} fill="white" opacity=".4" mask={`url(#raster-${m.id})`}/></> : m.kind === "pin" ? <><circle cx={m.points[0][0]*iw} cy={m.points[0][1]*ih} r={Math.max(5, m.width_px ?? 4)} fill={m.colour}/><text x={m.points[0][0]*iw+10} y={m.points[0][1]*ih} fill={m.colour} fontSize={Math.max(14, iw/60)}>{m.text}</text></>
            : <polyline points={m.points.map(p => `${p[0]*iw},${p[1]*ih}`).join(" ") + (m.kind === "region" ? ` ${m.points[0][0]*iw},${m.points[0][1]*ih}` : "")} fill="none" stroke={m.kind === "mask" ? "#ffffff99" : m.colour ?? "#ff5555"} strokeWidth={m.width_px ?? 4} strokeLinecap="round" strokeLinejoin="round" markerEnd={m.kind === "arrow" ? "url(#arrow-head)" : undefined} />}
        </g>)}</g>
      </svg>
    </div>
    <div className="annotation-dock">
      <div className="annotation-feedback"><label className="sr-only" htmlFor="annotation-feedback">Feedback</label><textarea id="annotation-feedback" aria-label="Feedback" placeholder="Describe the change for Copilot on this pinned frame…" disabled={busy} value={annotation.text} onChange={e => edit({...annotation, text: e.target.value})} onKeyDown={e => handleSubmitEnter(e, () => {
        if (busyClose) return;
        if (draft.state === "saved" && queueState === "idle") void queue();
        else if (draft.state !== "saving" && draft.state !== "conflict") void save();
      })}/></div>
      <div className="review-bar review-transport-bar">
        <div className="toolbar-group annotation-dock-target">
          <button className="toolbar-btn" disabled={busyClose} onClick={onClose}>{draft.state === "saved" ? "Close" : "Keep draft & close"}</button>
          <AnnotationTargetChip annotation={annotation}/>
          {annotation.spatial_hits.length === 0 && <div className="toolbar-group">
            <label className="toolbar-field"><span>Range</span><input aria-label="Range start" placeholder="Start" disabled={busy} type="number" min="1" value={annotation.range?.start ?? ""} onChange={e => edit({...annotation, range: e.target.value ? {start: Number(e.target.value), end: Math.max(Number(e.target.value), annotation.range?.end ?? Number(e.target.value))} : null})}/></label><span>–</span>
            <label className="toolbar-field"><span className="sr-only">Range end</span><input aria-label="Range end" placeholder="End" disabled={busy || !annotation.range} type="number" min={annotation.range?.start ?? 1} value={annotation.range?.end ?? ""} onChange={e => annotation.range && edit({...annotation, range: {...annotation.range, end: Math.max(annotation.range.start, Number(e.target.value))}})}/></label>
          </div>}
        </div>
        <AnnotationDockActions discard={discard} onDiscard={onDiscard} onClose={onClose} busy={busy}>
          <button className="toolbar-btn" disabled={busyClose || draft.state === "saving" || draft.state === "conflict"} onClick={() => void save()}>{draft.operation && draft.state !== "saved" ? "Retry original save" : "Save annotation"}</button>
          <button className="toolbar-btn toolbar-btn-accent" disabled={busyClose || draft.state !== "saved" || queueState !== "idle"} onClick={() => void queue()}>{queueState === "submitted" ? "Feedback queued" : queueState === "submitting" ? "Submitting feedback" : "Queue feedback"}</button>
        </AnnotationDockActions>
      </div>
    </div>
    {statusMessage && <div className="review-status-banner annotation-status-banner" role="alert">{statusMessage}</div>}
    {draft.state === "conflict" && <button onClick={() => {
      void allPages(cursor => api.call("listAnnotations",{path:{project_id:draft.projectId},query:{cursor,limit:100,version_id:annotation.target.version_id ?? undefined}}))
        .then(items => {const current=items.find(a=>a.annotation_id===annotation.annotation_id);if (!current) throw new Error("Current annotation unavailable");setServerRevision(current);}).catch(e=>setError(String(e)));
    }}>Load server revision for comparison</button>}
    {draft.state === "conflict" && serverRevision && <div><p>Server revision {serverRevision.saved_revision}: {serverRevision.text}</p><button onClick={() => {
      if (stableJson(serverRevision.target)!==stableJson(annotation.target)) {setError("Server target differs. Retargeting requires a new annotation.");return;}
      const rebased:Draft={...draft,annotation:{...annotation,saved_revision:serverRevision.saved_revision},operation:undefined,state:"browser-local",error:undefined,
        supersededOperations:[...(draft.supersededOperations ?? []),...(draft.operation?[draft.operation]:[])]};
      void saveDraft(rebased).then(()=>{onChange(rebased);setServerRevision(null);}).catch(e=>setError(String(e)));
    }}>Rebase local edits onto revision {serverRevision.saved_revision}</button></div>}
  </section>;
}
