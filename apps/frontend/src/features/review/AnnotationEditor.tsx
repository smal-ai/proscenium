import { useRef, useState } from "react";
import type { AnnotationSnapshot, Markup } from "../../api/generated/contracts";
import type { ApiClient } from "../../api/client";
import type { TurnSelection } from "../../api/studio";
import { contentRect, imagePoint, mediaUrl, stableJson } from "./media";
import { flushDraft, saveDraft, VECTOR_MASK, type Draft } from "./outbox";
import { greyPng } from "./png";
import { annotationContext } from "./context";
import { allPages } from "./records";

type Tool = Markup["kind"] | "eraser";
const tools: Tool[] = ["stroke", "arrow", "region", "pin", "eraser", "mask"];

function paint(ctx: CanvasRenderingContext2D, markups: Markup[], width: number, height: number, mask: boolean) {
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (const mark of markups) {
    if ((mark.kind === "mask") !== mask) continue;
    const points = mark.points.map(p => [p[0] * width, p[1] * height]);
    ctx.strokeStyle = ctx.fillStyle = mask ? "white" : mark.colour ?? "#ff5555";
    ctx.lineWidth = mark.width_px ?? 4;
    ctx.beginPath(); ctx.moveTo(points[0][0], points[0][1]);
    for (const p of points.slice(1)) ctx.lineTo(p[0], p[1]);
    if (mark.kind === "region") ctx.closePath();
    if (mark.kind === "pin") {
      ctx.arc(points[0][0], points[0][1], Math.max(5, ctx.lineWidth), 0, 2 * Math.PI);
      ctx.fill(); ctx.font = `${Math.max(14, width / 60)}px sans-serif`;
      ctx.fillText(mark.text ?? "", points[0][0] + 10, points[0][1]);
    } else {
      if (points.length === 1) { ctx.lineTo(points[0][0] + 0.01, points[0][1]); }
      ctx.stroke();
      if (mark.kind === "arrow" && points.length > 1) {
        const end = points.at(-1)!; const prev = points.at(-2)!;
        const angle = Math.atan2(end[1] - prev[1], end[0] - prev[0]);
        const size = Math.max(12, ctx.lineWidth * 4);
        ctx.beginPath(); ctx.moveTo(end[0] - size * Math.cos(angle - 0.5), end[1] - size * Math.sin(angle - 0.5));
        ctx.lineTo(end[0], end[1]); ctx.lineTo(end[0] - size * Math.cos(angle + 0.5), end[1] - size * Math.sin(angle + 0.5)); ctx.stroke();
      }
    }
  }
}

const pngBlob = (canvas: HTMLCanvasElement): Promise<Blob> => new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("PNG export failed")), "image/png"));

export function AnnotationEditor({api, draft, onChange, onQueue, onClose}: {
  api: ApiClient; draft: Draft; onChange: (draft: Draft) => void;
  onQueue: (text: string, context: TurnSelection) => void | Promise<void>; onClose: () => void;
}) {
  const [tool, setTool] = useState<Tool>("stroke");
  const [width, setWidth] = useState(4);
  const [pinText, setPinText] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const saveLatch = useRef(false);
  const [queueState, setQueueState] = useState<"idle" | "submitting" | "submitted">("idle");
  const queueLatch = useRef(false);
  const [serverRevision, setServerRevision] = useState<AnnotationSnapshot | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const active = useRef<string | null>(null);
  const live = useRef(draft); live.current = draft;
  const writes = useRef(Promise.resolve());
  const busy = draft.state === "saving" || (!!draft.operation && draft.state !== "saved");
  const annotation = draft.annotation;
  const {width: iw, height: ih} = annotation.target.dimensions;

  function edit(next: AnnotationSnapshot) {
    const updated: Draft = {...live.current, annotation: next, operation: undefined, state: "browser-local", error: undefined, markup: undefined, mask: undefined, markupUploadId: undefined, maskUploadId: undefined};
    live.current = updated; onChange(updated);
    writes.current = writes.current.then(() => saveDraft(updated)).catch(e => setError(`Local draft could not be saved: ${String(e)}`));
  }

  function point(event: React.PointerEvent) {
    if (!host.current) return null;
    return imagePoint(event.clientX, event.clientY, contentRect(host.current.getBoundingClientRect(), iw, ih));
  }

  function start(event: React.PointerEvent<HTMLDivElement>) {
    if (busy || event.button !== 0) return;
    const p = point(event); if (!p) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === "eraser") {
      const nearest = [...annotation.markups].reverse().find(mark => mark.points.some(q => Math.hypot((q[0]-p[0])*iw, (q[1]-p[1])*ih) < Math.max(16, width)));
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
    if (!active.current || busy) return;
    const p = point(event);
    // End the geometry at the content edge. Never manufacture edge points outside the image.
    if (!p) { active.current = null; return; }
    edit({...live.current.annotation, markups: live.current.annotation.markups.map(m => m.id === active.current && m.kind !== "pin"
      ? {...m, points: m.kind === "arrow" ? [m.points[0], p] : [...m.points, p]} : m)});
  }

  async function save() {
    if (saveLatch.current) return;
    saveLatch.current = true; setSaving(true);
    setError("");
    try {
      await writes.current;
      if (draft.operation) { await flushDraft(api, draft, onChange); return; }
      if (!image.current?.complete || image.current.naturalWidth !== iw || image.current.naturalHeight !== ih) throw new Error("Target image is not decoded at its recorded dimensions");
      const canvas = document.createElement("canvas"); canvas.width = iw; canvas.height = ih;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(image.current, 0, 0); paint(ctx, annotation.markups, iw, ih, false);
      const markup = await pngBlob(canvas);
      let mask: Blob | undefined;
      if (annotation.markups.some(m => m.kind === "mask" && m.text === VECTOR_MASK)) {
        // Imported binary/soft raster masks retain their exact registered bytes.
        // Only our editable binary vector brushes are re-rasterised after an edit.
        ctx.fillStyle = "black"; ctx.fillRect(0, 0, iw, ih); paint(ctx, annotation.markups.filter(m=>m.text===VECTOR_MASK), iw, ih, true);
        const rgba = ctx.getImageData(0, 0, iw, ih).data;
        const pixels = new Uint8Array(iw * ih);
        for (let i = 0; i < pixels.length; i++) pixels[i] = rgba[i*4] >= 128 ? 255 : 0;
        mask = await greyPng(iw, ih, pixels);
      }
      const frozen = {...draft, markup, mask}; await saveDraft(frozen);
      await flushDraft(api, frozen, onChange);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally {saveLatch.current = false; setSaving(false);}
  }

  async function queue() {
    if (queueLatch.current) return;
    queueLatch.current = true; setQueueState("submitting");
    try {await onQueue(annotation.text || "Apply this annotated feedback.", annotationContext(annotation)); setQueueState("submitted");}
    catch (e) {setError(String(e)); queueLatch.current = false; setQueueState("idle");}
  }

  return <section className="review-annotation" aria-label="Pinned annotation">
    <div className="review-toolbar">{tools.map(t => <button key={t} aria-pressed={tool === t} disabled={busy} onClick={() => setTool(t)}>{t}</button>)}
      <label>Width (image px)<input type="number" min="1" max="256" value={width} onChange={e => setWidth(Math.max(1, Number(e.target.value)))} /></label>
      {tool === "pin" && <input aria-label="Pin text" value={pinText} onChange={e => setPinText(e.target.value)} />}
      <button onClick={onClose}>Close draft</button>
    </div>
    <div className="review-canvas" ref={host} onPointerDown={start} onPointerMove={move} onPointerUp={() => {active.current = null;}} onPointerCancel={() => {active.current = null;}}>
      <img ref={image} src={mediaUrl(draft.projectId, annotation.target.artifact_id)} alt="Pinned clean annotation target" draggable={false} onError={() => setError(`media_unavailable: ${annotation.target.artifact_id}`)} />
      <svg viewBox={`0 0 ${iw} ${ih}`} preserveAspectRatio="xMidYMid meet" aria-label="Editable image-space markup">
        <defs><clipPath id="image-bounds"><rect width={iw} height={ih}/></clipPath><marker id="arrow-head" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#ff5555"/></marker></defs>
        <g clipPath="url(#image-bounds)">{annotation.markups.map(m => <g key={m.id}>
          {m.kind === "mask" && m.text !== VECTOR_MASK ? <><defs><mask id={`raster-${m.id}`}><image href={mediaUrl(draft.projectId,m.mask_artifact_id!)} width={iw} height={ih}/></mask></defs><rect width={iw} height={ih} fill="white" opacity=".4" mask={`url(#raster-${m.id})`}/></> : m.kind === "pin" ? <><circle cx={m.points[0][0]*iw} cy={m.points[0][1]*ih} r={Math.max(5, m.width_px ?? 4)} fill={m.colour}/><text x={m.points[0][0]*iw+10} y={m.points[0][1]*ih} fill={m.colour} fontSize={Math.max(14, iw/60)}>{m.text}</text></>
            : <polyline points={m.points.map(p => `${p[0]*iw},${p[1]*ih}`).join(" ") + (m.kind === "region" ? ` ${m.points[0][0]*iw},${m.points[0][1]*ih}` : "")} fill="none" stroke={m.kind === "mask" ? "#ffffff99" : m.colour ?? "#ff5555"} strokeWidth={m.width_px ?? 4} strokeLinecap="round" strokeLinejoin="round" markerEnd={m.kind === "arrow" ? "url(#arrow-head)" : undefined} />}
        </g>)}</g>
      </svg>
    </div>
    <label>Feedback<textarea aria-label="Feedback" disabled={busy} value={annotation.text} onChange={e => edit({...annotation, text: e.target.value})}/></label>
    <div className="review-toolbar">
      <span>Pinned {annotation.target.version_id ?? annotation.target.artifact_id} · frame {annotation.target.sequence_frame ?? annotation.target.source_frame ?? "image"}</span>
      <label>Range start<input disabled={busy} type="number" min="1" value={annotation.range?.start ?? ""} onChange={e => edit({...annotation, range: e.target.value ? {start: Number(e.target.value), end: Math.max(Number(e.target.value), annotation.range?.end ?? Number(e.target.value))} : null})}/></label>
      <label>Range end<input disabled={busy || !annotation.range} type="number" min={annotation.range?.start ?? 1} value={annotation.range?.end ?? ""} onChange={e => annotation.range && edit({...annotation, range: {...annotation.range, end: Math.max(annotation.range.start, Number(e.target.value))}})}/></label>
      <output aria-live="polite">{draft.state}{draft.error ? `: ${draft.error}` : ""}</output>
      <button disabled={saving || draft.state === "saving" || draft.state === "conflict"} onClick={() => void save()}>{draft.operation && draft.state !== "saved" ? "Retry original save" : "Save annotation"}</button>
      <button disabled={draft.state !== "saved" || queueState !== "idle"} onClick={() => void queue()}>{queueState === "submitted" ? "Feedback queued" : queueState === "submitting" ? "Submitting feedback" : "Queue feedback"}</button>
    </div>
    {(error || draft.state === "conflict") && <p role="alert">{error || "Saved annotation changed elsewhere. This draft and its original operation are retained. Reload the server revision before making a new edit."}</p>}
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
