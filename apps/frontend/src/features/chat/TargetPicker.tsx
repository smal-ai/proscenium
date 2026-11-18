import { useEffect, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import type { ApiClient, ItemRecord, ShotPlan, VersionRecord, VersionRef } from "../../api";
import type { TurnSelection } from "../../api/studio";
import { describeError, id } from "../../app/storage";
import { resolveTarget, targetLabel, uniqueVersions, type DraftTarget } from "./target";

type NewTarget = Extract<DraftTarget, { kind: "new_item" }>;
type Mode = "current_view" | "project" | "existing" | "new";
type Lookups = { api: ApiClient; projectId: string };
type PickerProps = Lookups & {
  items: ItemRecord[]; selection: TurnSelection; target: DraftTarget; references: VersionRef[];
  includeCurrentViewAsReference: boolean; onTarget: (target: DraftTarget) => void; onReferences: (refs: VersionRef[]) => void; onIncludeView: (include: boolean) => void;
  open?: boolean; onOpenChange?: (open: boolean) => void;
  pendingExisting?: boolean; onPendingExistingChange?: (pending: boolean) => void;
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
  return <label>{label}<select aria-label={label} value={itemId} onChange={event => onChange(event.target.value)}>
    <option value="" disabled>Choose published item</option>
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
  const currentShotId = target !== "current_view" && target.kind === "shot" && versionId ? target.shot_id : "";
  const result = useVersions({ api, projectId }, itemId, current);
  const error = result?.error ?? (versionId ? plan?.error : undefined);
  function chooseItem(next: string) {
    const version_id = items.find(item => item.item_id === next)?.current_version_id;
    if (version_id) onTarget({ kind: "item", version: { item_id: next, version_id } });
  }
  return <div className="target-existing-fields">
    <ItemSelect label="Published item" items={items.filter(item => canTarget(item) && (item.current_version_id || item.item_id === itemId))} itemId={itemId} onChange={chooseItem} />
    {itemId && <VersionSelect label="Published version" result={result} versionId={versionId} current={current}
      onChange={version_id => onTarget({ kind: "item", version: { item_id: itemId, version_id } })} />}
    {plan?.plan && pinned && versionId && <label>Specific shot (optional)<select aria-label="Target storyboard shot" value={currentShotId} onChange={event => {
      const shot_id = event.target.value;
      onTarget(shot_id ? { kind: "shot", storyboard_version: pinned, shot_id } : { kind: "item", version: pinned });
    }}>
      <option value="">Entire storyboard version</option>
      {plan.plan.shots.map(shot => <option key={shot.shot_id} value={shot.shot_id}>{shot.intent || shot.shot_id}</option>)}
    </select></label>}
    {pending && <small className="target-pending-hint">Select a published item to enable sending, or choose another target mode.</small>}
    {error && <small role="alert">{error}</small>}
  </div>;
}

function References({ api, projectId, items, target, selection, references, includeCurrentViewAsReference, onReferences, onIncludeView, count }: Lookups & Pick<PickerProps, "items" | "target" | "selection" | "references" | "includeCurrentViewAsReference" | "onReferences" | "onIncludeView"> & { count: number }) {
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
    {references.map(ref => <div className="reference-chip" key={`${ref.item_id}:${ref.version_id}`} title={JSON.stringify(ref)}>
      <span>{items.find(item => item.item_id === ref.item_id)?.name ?? ref.item_id} · {ref.version_id}</span>
      <button className="subtle" aria-label={`Remove reference ${ref.version_id}`} onClick={() => onReferences(references.filter(value => value.item_id !== ref.item_id || value.version_id !== ref.version_id))}><X size={12} /></button>
    </div>)}
    {target !== "current_view" && <small>Explicit targets do not capture hidden Review drafts or viewport images.</small>}
    {result?.error && <small role="alert">{result.error}</small>}
  </details>;
}

function TargetOptions({ props, count, friendly, close, plan, pending, chooseExisting }: { props: PickerProps; count: number; friendly: string; close: () => void; plan: ReturnType<typeof useShotPlan>; pending: boolean; chooseExisting: () => void }) {
  const { target, onTarget, selection } = props;
  const newTarget = target !== "current_view" && target.kind === "new_item" ? target : null;
  const [previousNew, setPreviousNew] = useState<NewTarget | null>(null);
  const mode: Mode = pending || pinnedVersion(target) ? "existing" : target === "current_view" ? "current_view" : newTarget ? "new" : "project";
  function chooseMode(next: Mode) {
    if (newTarget) setPreviousNew(newTarget);
    if (next === "existing" && !pinnedVersion(target)) chooseExisting();
    if (next === "current_view") onTarget("current_view");
    if (next === "project") onTarget({ kind: "project" });
    if (next === "new") onTarget(newTarget ?? previousNew ?? { kind: "new_item", item_kind: "asset", intent_id: id() });
  }
  return <section id="request-target-picker" className="target-options" aria-label="Request target and references">
    <header className="target-options-header"><strong>Request target</strong><button className="subtle" aria-label="Close target picker" onClick={close}>Done</button></header>
    <div className="target-mode-tabs" role="group" aria-label="Target mode">{modes.map(([next, label]) =>
      <button key={next} aria-pressed={mode === next} onClick={() => chooseMode(next)}>{label}</button>
    )}</div>
    {mode === "current_view" && <small className="target-mode-summary">{selection.version_id ? `Captures ${friendly} from Review on submit.` : "Nothing open in Review · resolves to Project on submit."}</small>}
    {mode === "project" && <small className="target-mode-summary">Project-wide planning or discussion. Hidden Review drafts are not captured.</small>}
    {mode === "existing" && <ExistingTarget {...props} plan={plan} pending={pending} />}
    {mode === "new" && newTarget && <>
      <div className="target-kind-tabs" role="group" aria-label="New item kind">{(Object.keys(newKinds) as NewTarget["item_kind"][]).map(kind =>
        <button key={kind} aria-pressed={newTarget.item_kind === kind} onClick={() => onTarget({ ...newTarget, item_kind: kind })}>{newKinds[kind][0]}</button>
      )}</div>
      <label>Optional name<input aria-label="New item name" placeholder={`e.g. ${newKinds[newTarget.item_kind][1]}`} value={newTarget.name ?? ""} onChange={event => {
        const name = event.target.value;
        onTarget({ kind: "new_item", item_kind: newTarget.item_kind, intent_id: newTarget.intent_id, ...(name ? { name } : {}) });
      }} onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); close(); } }} /></label>
    </>}
    <References {...props} count={count} />
  </section>;
}

export function TargetPicker(props: PickerProps) {
  const { api, projectId, items, selection, target, references, includeCurrentViewAsReference } = props;
  const [localOpen, setLocalOpen] = useState(false);
  const [localPendingExisting, setLocalPendingExisting] = useState(false);
  const open = props.open ?? localOpen;
  function changeOpen(next: boolean) {
    if (props.open === undefined) setLocalOpen(next);
    props.onOpenChange?.(next);
  }
  function setPendingExisting(next: boolean) {
    if (props.pendingExisting === undefined) setLocalPendingExisting(next);
    props.onPendingExistingChange?.(next);
  }
  function chooseTarget(next: DraftTarget) {
    setPendingExisting(false);
    props.onTarget(next);
  }
  const resolved = resolveTarget(target, selection);
  const pinned = pinnedVersion(target);
  const pending = (props.pendingExisting ?? localPendingExisting) && !pinned;
  const browsingStoryboard = open && items.some(item => item.item_id === pinned?.item_id && item.kind === "storyboard");
  const plan = useShotPlan({ api, projectId }, resolved.kind === "shot" ? resolved.storyboard_version.version_id : browsingStoryboard ? pinned?.version_id : undefined);
  const friendly = targetLabel(resolved, items);
  const shotName = resolved.kind === "shot" ? plan?.plan?.shots.find(shot => shot.shot_id === resolved.shot_id)?.intent : null;
  const targetName = shotName && resolved.kind === "shot" ? `Shot · ${items.find(item => item.item_id === resolved.storyboard_version.item_id)?.name ?? "Storyboard"} · ${shotName}` : friendly;
  const label = pending ? "Choose published item…" : target === "current_view" && resolved.kind !== "project" ? `Current view · ${targetName}` : targetName;
  const count = uniqueVersions([...references, ...(includeCurrentViewAsReference && selection.item_id && selection.version_id ? [{ item_id: selection.item_id, version_id: selection.version_id }] : [])]).length;
  return <div className="target-picker" onKeyDown={event => {
    if (open && event.key === "Escape" && !event.nativeEvent.isComposing) { event.preventDefault(); event.stopPropagation(); changeOpen(false); }
  }}>
    <button className="target-bar" aria-label="Choose request target and references" aria-expanded={open} aria-controls="request-target-picker" aria-invalid={pending || undefined} onClick={() => changeOpen(!open)} title={pending ? undefined : JSON.stringify(resolved)}>
      <span className="target-bar-label"><span>Target:</span> {label}</span>{count > 0 && <span className="target-ref-count">+{count} ref</span>}<ChevronDown size={13} />
    </button>
    {open && <TargetOptions props={{ ...props, onTarget: chooseTarget }} count={count} friendly={targetName} close={() => changeOpen(false)} plan={plan} pending={pending} chooseExisting={() => setPendingExisting(true)} />}
  </div>;
}
