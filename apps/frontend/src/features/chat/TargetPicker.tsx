import { useEffect, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import type { ApiClient, Attachment, ItemRecord, ShotPlan, VersionRecord, VersionRef } from "../../api";
import type { TurnSelection } from "../../api/studio";
import { describeError, id } from "../../app/storage";
import { ComposerPanel } from "./ComposerPanel";
import { artifactUrl } from "./ContextPreview";
import { resolveTarget, targetLabel, uniqueVersions, viewedSelectionLabel, type DraftTarget } from "./target";

type NewTarget = Extract<DraftTarget, { kind: "new_item" }>;
type Mode = "current_view" | "project" | "existing" | "new";
type Lookups = { api: ApiClient; projectId: string };
type PickerProps = Lookups & {
  items: ItemRecord[]; selection: TurnSelection; target: DraftTarget; references: VersionRef[];
  includeCurrentViewAsReference: boolean; onTarget: (target: DraftTarget) => void; onReferences: (refs: VersionRef[]) => void; onIncludeView: (include: boolean) => void;
  open: boolean; onToggle: () => void; onClose: () => void;
  pendingExisting: boolean; onPendingExistingChange: (pending: boolean) => void;
  attachments: Attachment[]; onAttachments: (attachments: Attachment[]) => void; uploadingCount: number;
};

const modes: [Mode, string][] = [["current_view", "Current view"], ["project", "Project"], ["existing", "Existing…"], ["new", "New…"]];
const newKinds = { asset: ["Asset", "Broken relay dish"], scene: ["Scene", "Relay interior"], storyboard: ["Storyboard", "Signal Lost"] } as const;
const canTarget = (item: ItemRecord) => item.kind === "asset" || item.kind === "scene" || item.kind === "storyboard";
const pinnedVersion = (target: DraftTarget) => target === "current_view" ? null : target.kind === "item" ? target.version : target.kind === "shot" ? target.storyboard_version : null;

function useVersions({ api, projectId }: Lookups, itemId: string, currentVersionId?: string | null) {
  const [result, setResult] = useState<{ itemId: string; versions: VersionRecord[]; error?: string }>();
  useEffect(() => {
    setResult(undefined);
    if (!itemId) return;
    const controller = new AbortController();
    const { signal } = controller;
    void (async () => {
      try {
        const versions: VersionRecord[] = [];
        let cursor: string | undefined;
        do {
          const page = await api.call("listItemVersions", { path: { project_id: projectId, item_id: itemId }, query: { cursor, limit: 100 }, signal });
          versions.push(...(page.items ?? [])); cursor = page.next_cursor ?? undefined;
        } while (cursor && !signal.aborted);
        if (!signal.aborted) setResult({ itemId, versions });
      } catch (error) {
        if (!signal.aborted) setResult({ itemId, versions: [], error: describeError(error) });
      }
    })();
    return () => controller.abort();
  }, [api, projectId, itemId, currentVersionId]);
  return result?.itemId === itemId ? result : undefined;
}

function useShotPlan({ api, projectId }: Lookups, versionId?: string) {
  const [result, setResult] = useState<{ versionId: string; plan?: ShotPlan; error?: string }>();
  useEffect(() => {
    setResult(undefined);
    if (!versionId) return;
    const controller = new AbortController();
    const { signal } = controller;
    void api.call("getVersionShotPlan", { path: { project_id: projectId, version_id: versionId }, signal })
      .then(plan => { if (!signal.aborted) setResult({ versionId, plan }); })
      .catch(error => { if (!signal.aborted) setResult({ versionId, error: describeError(error) }); });
    return () => controller.abort();
  }, [api, projectId, versionId]);
  return result?.versionId === versionId ? result : undefined;
}

function ItemSelect({ label, items, itemId, onChange }: { label: string; items: ItemRecord[]; itemId: string; onChange: (id: string) => void }) {
  return <label>{label}<select aria-label={label} value={itemId} disabled={!items.length} onChange={event => onChange(event.target.value)}>
    <option value="" disabled>{items.length ? "Choose published item" : "No published items yet"}</option>
    {items.map(item => <option key={item.item_id} value={item.item_id}>{item.kind} · {item.name}</option>)}
  </select></label>;
}

function VersionSelect({ label, result, versionId, current, onChange, hasItem = true }: {
  label: string; result: ReturnType<typeof useVersions>; versionId: string; current?: string | null; onChange: (id: string) => void; hasItem?: boolean;
}) {
  const versions = result?.versions ?? [];
  return <label>{label}<select aria-label={label} value={versionId} disabled={!result || Boolean(result.error)} onChange={event => onChange(event.target.value)}>
    <option value="" disabled>{!hasItem ? "Choose an item first" : result ? "Choose version" : "Loading versions…"}</option>
    {versionId && !versions.some(version => version.version_id === versionId) && <option value={versionId}>{versionId}</option>}
    {versions.map(version => <option key={version.version_id} value={version.version_id}>{version.name} · {version.version_id}{version.version_id === current ? " · current" : ""}</option>)}
  </select></label>;
}

function ExistingTarget({ api, projectId, items, target, onTarget, plan, pending }: Lookups & Pick<PickerProps, "items" | "target" | "onTarget"> & { plan: ReturnType<typeof useShotPlan>; pending: boolean }) {
  const pinned = pinnedVersion(target);
  const itemId = pinned?.item_id ?? "";
  const versionId = pinned?.version_id ?? "";
  const current = items.find(item => item.item_id === itemId)?.current_version_id;
  const eligibleItems = items.filter(item => canTarget(item) && (item.current_version_id || item.item_id === itemId));
  const currentShotId = target !== "current_view" && target.kind === "shot" && versionId ? target.shot_id : "";
  const result = useVersions({ api, projectId }, itemId, current);
  const error = result?.error ?? (versionId ? plan?.error : undefined);
  function chooseItem(next: string) {
    const version_id = items.find(item => item.item_id === next)?.current_version_id;
    if (version_id) onTarget({ kind: "item", version: { item_id: next, version_id } });
  }
  return <div className="target-existing-fields">
    <ItemSelect label="Published item" items={eligibleItems} itemId={itemId} onChange={chooseItem} />
    {itemId && <VersionSelect label="Published version" result={result} versionId={versionId} current={current}
      onChange={version_id => onTarget({ kind: "item", version: { item_id: itemId, version_id } })} />}
    {plan?.plan && pinned && versionId && <label>Specific shot (optional)<select aria-label="Target storyboard shot" value={currentShotId} onChange={event => {
      const shot_id = event.target.value;
      onTarget(shot_id ? { kind: "shot", storyboard_version: pinned, shot_id } : { kind: "item", version: pinned });
    }}>
      <option value="">Entire storyboard version</option>
      {plan.plan.shots.map(shot => <option key={shot.shot_id} value={shot.shot_id}>{shot.intent || shot.shot_id}</option>)}
    </select></label>}
    {pending && <small className="target-pending-hint">{eligibleItems.length ? "Select a published item to enable sending, or choose another target mode." : "No published items exist yet. Choose another target mode to enable sending."}</small>}
    {error && <small role="alert">{error}</small>}
  </div>;
}

function References({ api, projectId, items, selection, references, includeCurrentViewAsReference, onReferences, onIncludeView, count }: Lookups & Pick<PickerProps, "items" | "selection" | "references" | "includeCurrentViewAsReference" | "onReferences" | "onIncludeView"> & { count: number }) {
  const [initiallyOpen] = useState(() => references.length > 0 || includeCurrentViewAsReference);
  const [itemId, setItemId] = useState("");
  const [versionId, setVersionId] = useState("");
  const current = items.find(item => item.item_id === itemId)?.current_version_id;
  const result = useVersions({ api, projectId }, itemId, current);
  const candidate = result?.versions.some(version => version.version_id === versionId) ? { item_id: itemId, version_id: versionId } : null;
  const canIncludeView = Boolean(selection.item_id && selection.version_id);
  return <details className="target-references-disclosure" open={initiallyOpen}>
    <summary>References <span className="target-ref-count">{count}</span></summary>
    <label className="target-checkbox-row"><input type="checkbox" disabled={!canIncludeView} checked={includeCurrentViewAsReference && canIncludeView} onChange={event => onIncludeView(event.target.checked)} />
      <span>{canIncludeView ? "Include current Review version as reference" : "Include current Review version (none open in Review)"}</span>
    </label>
    <div className="target-reference-fields">
      <ItemSelect label="Reference item" items={items.filter(item => item.current_version_id)} itemId={itemId} onChange={next => {
        setItemId(next); setVersionId(items.find(item => item.item_id === next)?.current_version_id ?? "");
      }} />
      <VersionSelect label="Reference version" result={result} versionId={versionId} current={current} onChange={setVersionId} hasItem={Boolean(itemId)} />
      <button className="target-add-reference" disabled={!candidate} onClick={() => { if (candidate) onReferences(uniqueVersions([...references, candidate])); }}>Add reference version</button>
    </div>
    {references.map(ref => <div className="reference-chip" key={`${ref.item_id}:${ref.version_id}`}>
      <span>{items.find(item => item.item_id === ref.item_id)?.name ?? ref.item_id} · {ref.version_id}</span>
      <button className="subtle" aria-label={`Remove reference ${ref.version_id}`} onClick={() => onReferences(references.filter(value => value.item_id !== ref.item_id || value.version_id !== ref.version_id))}><X size={12} /></button>
    </div>)}
    {result?.error && <small role="alert">{result.error}</small>}
  </details>;
}

function TargetOptions({ props, count, friendly, plan, chooseMode, reviewAttachments, extras }: {
  props: PickerProps; count: number; friendly: string; plan: ReturnType<typeof useShotPlan>; chooseMode: (mode: Mode) => void; reviewAttachments: Attachment[]; extras: string[];
}) {
  const { target, onTarget, selection, pendingExisting: pending, onClose, attachments, onAttachments, projectId } = props;
  const newTarget = target !== "current_view" && target.kind === "new_item" ? target : null;
  const mode: Mode = pending || pinnedVersion(target) ? "existing" : target === "current_view" ? "current_view" : newTarget ? "new" : "project";
  return <div className="target-options">
    <div className="target-mode-tabs" role="group" aria-label="Target mode">{modes.map(([next, label]) =>
      <button key={next} aria-pressed={mode === next} onClick={() => chooseMode(next)}>{label}</button>
    )}</div>
    {mode === "current_view" && <small className="target-mode-summary">{selection.version_id ? `Captures ${friendly} from Review on submit.` : selection.attachments.length ? "Captures the inspected image from Review on submit." : "Nothing open in Review · resolves to Project on submit."}</small>}
    {mode === "project" && <small className="target-mode-summary">Project-wide planning or discussion. Hidden Review drafts are not captured.</small>}
    {mode === "existing" && <><ExistingTarget {...props} plan={plan} pending={pending} />{!pending && <small className="target-mode-summary">Uses the pinned version, independent of Review.</small>}</>}
    {mode === "new" && newTarget && <>
      <div className="target-kind-tabs" role="group" aria-label="New item kind">{(Object.keys(newKinds) as NewTarget["item_kind"][]).map(kind =>
        <button key={kind} aria-pressed={newTarget.item_kind === kind} onClick={() => onTarget({ ...newTarget, item_kind: kind })}>{newKinds[kind][0]}</button>
      )}</div>
      <label>Optional name<input aria-label="New item name" placeholder={`e.g. ${newKinds[newTarget.item_kind][1]}`} value={newTarget.name ?? ""} onChange={event => {
        const name = event.target.value;
        onTarget({ kind: "new_item", item_kind: newTarget.item_kind, intent_id: newTarget.intent_id, ...(name ? { name } : {}) });
      }} onKeyDown={event => { if (event.key === "Enter" && !event.ctrlKey && !event.metaKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); onClose(); } }} /></label>
      <small className="target-mode-summary">Creates a new {newTarget.item_kind}.</small>
    </>}
    <References {...props} count={count} />
    {!!(attachments.length || reviewAttachments.length) && <section className="target-attachments" aria-label="Attached images">
      <strong>Attached images</strong>
      <div className="attachment-previews">
        {attachments.map((attachment, i) => <figure key={`${attachment.artifact_id}.${i}`}>
          <img src={artifactUrl(projectId, attachment.artifact_id)} alt={`Uploaded image ${i + 1}`} loading="lazy" />
          <div className="attachment-controls">
            <select aria-label={`Image ${i + 1} role`} value={attachment.role} onChange={event => {
              const role = event.target.value as Attachment["role"];
              onAttachments(attachments.map((value, j) => i === j ? { ...value, role } : value));
            }}>{["reference", "concept", "render", "markup", "mask"].map(role => <option key={role}>{role}</option>)}</select>
            <button className="subtle" aria-label={`Remove image ${i + 1}`} onClick={() => onAttachments(attachments.filter((_, j) => i !== j))}><X size={12} /></button>
          </div>
        </figure>)}
        {reviewAttachments.map((attachment, i) => <figure key={attachment.artifact_id}>
          <img src={artifactUrl(projectId, attachment.artifact_id)} alt={`Review image ${i + 1}`} loading="lazy" />
          <figcaption>Review · {attachment.role}</figcaption>
        </figure>)}
      </div>
    </section>}
    {!!extras.length && <ul className="target-viewport-extras" aria-label="Review context">{extras.map(extra => <li className="target-ref-count" key={extra}>{extra}</li>)}</ul>}
  </div>;
}

export function TargetPicker(props: PickerProps) {
  const { api, projectId, items, selection, target, references, includeCurrentViewAsReference, open, onToggle, onClose, onTarget, attachments, uploadingCount } = props;
  const [previousNew, setPreviousNew] = useState<NewTarget | null>(null);
  const resolved = resolveTarget(target, selection, items);
  const pinned = pinnedVersion(target);
  const pending = props.pendingExisting;
  function chooseMode(next: Mode) {
    const newTarget = target !== "current_view" && target.kind === "new_item" ? target : null;
    if (newTarget) setPreviousNew(newTarget);
    if (next === "existing" && !pinned) props.onPendingExistingChange(true);
    if (next === "current_view") onTarget("current_view");
    if (next === "project") onTarget({ kind: "project" });
    if (next === "new") onTarget(newTarget ?? previousNew ?? { kind: "new_item", item_kind: "asset", intent_id: id() });
  }
  const browsingStoryboard = open && items.some(item => item.item_id === pinned?.item_id && item.kind === "storyboard");
  const plan = useShotPlan({ api, projectId }, resolved?.kind === "shot" ? resolved.storyboard_version.version_id : browsingStoryboard ? pinned?.version_id : undefined);
  const viewed = viewedSelectionLabel(selection, items);
  const friendly = resolved ? targetLabel(resolved, items) : (viewed ?? "Project");
  const shotName = resolved?.kind === "shot" ? plan?.plan?.shots.find(shot => shot.shot_id === resolved.shot_id)?.intent : null;
  const targetName = shotName && resolved?.kind === "shot" ? `Shot · ${items.find(item => item.item_id === resolved.storyboard_version.item_id)?.name ?? "Storyboard"} · ${shotName}` : friendly;
  const label = pending ? "Choose published item…" : target === "current_view" && (resolved ? resolved.kind !== "project" : viewed) ? `Current view · ${targetName}` : targetName;
  const count = uniqueVersions([...references, ...(includeCurrentViewAsReference && selection.item_id && selection.version_id ? [{ item_id: selection.item_id, version_id: selection.version_id }] : [])]).length;
  const capturesReview = !pending && target === "current_view";
  const imageIds = new Set(attachments.map(attachment => attachment.artifact_id));
  const reviewAttachments = capturesReview ? selection.attachments.filter(attachment => {
    if (imageIds.has(attachment.artifact_id)) return false;
    imageIds.add(attachment.artifact_id);
    return true;
  }) : [];
  const extras: string[] = [];
  if (capturesReview) {
    const { selected_objects: objects, annotations, approved_findings: findings, camera, range } = selection;
    if (objects.length) extras.push(`${objects.length} ${objects.length === 1 ? "object" : "objects"} selected`);
    if (annotations.length) extras.push(`${annotations.length} ${annotations.length === 1 ? "annotation" : "annotations"}`);
    if (findings.length) extras.push(`${findings.length} approved ${findings.length === 1 ? "finding" : "findings"}`);
    if (camera) extras.push("Inspection camera");
    if (range) extras.push(`Frames ${range.start}–${range.end}`);
  }
  const extraCount = imageIds.size + (capturesReview ? selection.selected_objects.length + selection.annotations.length + selection.approved_findings.length : 0);
  return <>
    <button className="target-bar" aria-label="Choose request target and references" aria-expanded={open} aria-controls="request-target-picker" aria-invalid={pending || undefined} onClick={onToggle} title={`Target: ${label}`}>
      <span className="target-bar-label"><span>Target:</span> {label}</span>
      {count > 0 && <span className="target-ref-count">+{count} ref</span>}
      {extraCount > 0 && <span className="target-ref-count">+{extraCount} context</span>}
      {uploadingCount > 0 && <span className="target-ref-count">+{uploadingCount} uploading</span>}
      <ChevronDown size={13} aria-hidden="true" />
    </button>
    {open && <ComposerPanel panelId="request-target-picker" title="Request target" onClose={onClose} closeLabel="Close target picker">
      <TargetOptions props={props} count={count} friendly={targetName} plan={plan} chooseMode={chooseMode} reviewAttachments={reviewAttachments} extras={extras} />
    </ComposerPanel>}
  </>;
}
