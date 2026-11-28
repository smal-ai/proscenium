import { memo, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { RelationshipDirection, RelationshipEndpoint, RelationshipNodeSummary, RelationshipPage, RelationshipRelation, RelationshipRoot, VersionRecord } from "../../api/generated/contracts";
import type { OutlinerSurfaceProps } from "../../api/studio";
import { nextArtifactReadOrder } from "../readyArtifactIndex";
import { appendPage, boundedSignal, coverageMessage, endpointKey, extendPath, groupEdges, readFailure, readRelationships, readVersions, rootRef, versionRoot } from "./model";
import { overlayNode, useNodeOverlays, usePanelChoiceVersion, type NodeOverlays } from "./overlays";
import "./outliner.css";

type FocusedRoot = { endpoint: RelationshipRoot; summary?: RelationshipNodeSummary; board?: RelationshipNodeSummary };
type NavigationProps = Pick<OutlinerSurfaceProps, "api" | "projectId" | "visible" | "onOpenVersion" | "onOpenShot" | "onInspectArtifact">;
type BranchProps = NavigationProps & {
  direction: RelationshipDirection;
  readKey: string;
  restart: () => void;
  focusRoot: (root: FocusedRoot) => void;
  overlays: NodeOverlays;
};
const names: Record<RelationshipRelation, string> = {
  contains_shot: "Shot", contains_panel: "Panel", board_reference: "Board reference", bound_scene: "Bound scene",
  sequence_input: "Sequence input", declared_dependency: "Declared dependency", derived_from: "Derived from",
};
const groupNames = { structure: "Structure", inputs: "Inputs & bindings", provenance: "Origins / Derivatives" } as const;

function nodeLabel(node: RelationshipEndpoint, summary?: RelationshipNodeSummary) {
  if (summary) return summary.label;
  switch (node.kind) {
    case "version": return node.ref.version_id;
    case "shot": return node.shot_id;
    case "panel": return node.frame_id;
    case "artifact": return node.artifact_id;
  }
}

function nodeAction(node: RelationshipNodeSummary | undefined) {
  if (node?.kind === "version") return "Open version";
  if (node?.kind === "shot") return "Open shot in Shots";
  if (node?.kind === "artifact") return "Inspect artefact";
  if (node?.kind === "panel" && node.authored_state !== "missing" && node.artifact_id) return "Inspect panel";
  return null;
}

function NodeAction({ node, api, visible, projectId, onOpenVersion, onOpenShot, onInspectArtifact }: NavigationProps & { node: RelationshipNodeSummary }) {
  const action = useRef<AbortController>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = endpointKey(node);
  const artefactId = node.kind === "artifact" || node.kind === "panel" ? node.artifact_id : null;
  const label = nodeAction(node);
  const enabled = visible && !!label && (node.kind === "artifact" ? node.state === "ready" : node.kind !== "panel" || node.artifact_state === "ready");

  // The committed row owns its lookup. A collapsed ancestor unmounts descendants;
  // hiding the tree or revoking the artefact also fences any response in flight.
  useLayoutEffect(() => {
    setBusy(false);
    if (enabled) setError("");
    return () => { action.current?.abort(); action.current = null; };
  }, [api, projectId, key, artefactId, enabled]);

  async function openNode() {
    if (!enabled || action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setError("");
    setBusy(true);
    const signal = boundedSignal(controller.signal);
    try {
      if (node.kind === "version" || node.kind === "shot") {
        const pin = rootRef(node);
        const [record] = await readVersions(api, projectId, pin.item_id, signal, pin.version_id);
        if (action.current !== controller) return;
        signal.throwIfAborted();
        if (!record || (node.kind === "shot" && record.kind !== "storyboard")) {
          setError(`Exact publication ${pin.version_id} is unavailable. No other version was opened.`);
        } else if (node.kind === "shot") onOpenShot(record, node.shot_id);
        else onOpenVersion(record);
      } else if (artefactId) {
        const fresh = await api.call("getArtifact", { path: { project_id: projectId, artifact_id: artefactId }, signal });
        if (action.current !== controller) return;
        signal.throwIfAborted();
        if (fresh.project_id !== projectId || fresh.artifact_id !== artefactId || fresh.state !== "ready") setError("The exact artefact is no longer available to inspect.");
        else onInspectArtifact(fresh);
      }
    } catch (cause) {
      if (action.current === controller && !controller.signal.aborted) setError(`Could not open the exact target. ${readFailure(cause).text}`);
    } finally {
      if (action.current === controller) { action.current = null; setBusy(false); }
    }
  }
  return label && <><button type="button" disabled={!enabled} aria-disabled={busy || undefined} onClick={() => void openNode()}>{label}</button>
    {error && <small role="alert">{error}</small>}</>;
}

function NodeMetadata({ endpoint, summary, board, viewing, badges = true }: {
  endpoint: RelationshipEndpoint; summary?: RelationshipNodeSummary; board?: RelationshipNodeSummary; viewing?: boolean; badges?: boolean;
}) {
  const pinned = summary?.kind === "version" ? summary : board?.kind === "version" ? board : undefined;
  return <div className="relationship-meta">
    {endpoint.kind === "version" && <small>{summary?.kind === "version" ? summary.item_kind.replaceAll("_", " ") : "Published version"} · <code>{endpoint.ref.version_id}</code></small>}
    {(endpoint.kind === "shot" || endpoint.kind === "panel") && <small>{endpoint.kind === "shot" ? `Shot ${endpoint.shot_id}` : `Panel ${endpoint.frame_id}`} · {pinned?.label ?? endpoint.board.item_id} · <code>{endpoint.board.version_id}</code></small>}
    {summary?.kind === "shot" && <small>Scene: {summary.binding_status}</small>}
    {summary?.kind === "panel" && <small>{summary.role} · Authored: {summary.authored_state} · Image: {summary.artifact_state}{summary.owner_selected ? " · Owner-selected image" : ""}</small>}
    {summary?.kind === "artifact" && <small>{summary.artifact_kind ?? "Artefact"} · {summary.state}</small>}
    {((pinned && badges) || viewing) && <div className="relationship-badges">
      {pinned && badges && <span>{pinned.is_current ? "Latest" : "Historical"}</span>}
      {pinned?.is_selected && badges && <span>Selected</span>}
      {viewing && <span>Viewing in Review</span>}
    </div>}
  </div>;
}

function navigateTree(event: KeyboardEvent<HTMLElement>, expanded: boolean, toggle?: () => void) {
  const item = event.currentTarget.closest<HTMLElement>("[role=treeitem]");
  const tree = item?.closest("[role=tree]");
  if (!item || !tree || !["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  event.stopPropagation();
  const rows = [...tree.querySelectorAll<HTMLElement>("[role=treeitem]")];
  const index = rows.indexOf(item);
  const parent = item.parentElement?.closest("[role=treeitem]");
  let destination: Element | undefined | null;
  if (event.key === "ArrowDown") destination = rows[index + 1];
  if (event.key === "ArrowUp") destination = rows[index - 1];
  if (event.key === "Home") destination = rows[0];
  if (event.key === "End") destination = rows.at(-1);
  if (event.key === "ArrowLeft") {
    if (expanded) toggle?.();
    else destination = parent;
  }
  if (event.key === "ArrowRight") {
    if (toggle && !expanded) toggle();
    else if (expanded) destination = item.querySelector("[role=treeitem]");
  }
  destination?.querySelector<HTMLElement>(":scope > .relationship-row > [data-tree-focus]")?.focus();
}

function RelationshipRow({ endpoint, summary: recorded, board, path, evidence, nodeReadOrder, ...props }: BranchProps & {
  endpoint: RelationshipEndpoint; summary?: RelationshipNodeSummary; board?: RelationshipNodeSummary;
  path: ReadonlySet<string>; evidence: string; nodeReadOrder: number;
}) {
  const labelId = useId();
  const descriptionId = useId();
  const [expanded, setExpanded] = useState(false);
  const chosenVersion = usePanelChoiceVersion(props, recorded?.kind === "panel" && recorded.artifact_id ? recorded : null, props.overlays);
  const summary = recorded && overlayNode(recorded, props.overlays, nodeReadOrder, chosenVersion);
  const childPath = extendPath(path, endpoint);
  const root = endpoint.kind === "version" || endpoint.kind === "shot" ? endpoint : null;
  const canExpand = Boolean(root && childPath);
  const toggle = canExpand ? () => setExpanded(open => !open) : undefined;
  const label = nodeLabel(endpoint, summary);
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => navigateTree(event, expanded, toggle);
  const action = nodeAction(summary);

  return <div className="relationship-node" role="treeitem" aria-labelledby={labelId} aria-describedby={descriptionId} aria-expanded={canExpand ? expanded : undefined}>
    <div className="relationship-row">
      {canExpand ? <button className="relationship-toggle" data-tree-focus id={labelId} type="button" aria-expanded={expanded} aria-describedby={descriptionId} onClick={toggle} onKeyDown={onKeyDown}>
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>{label}
      </button> : <strong data-tree-focus id={labelId} aria-describedby={descriptionId} tabIndex={0} onKeyDown={onKeyDown}>{label}</strong>}
      <div id={descriptionId}>
        <small>{evidence}</small>
        <NodeMetadata endpoint={endpoint} summary={summary} board={board} />
        {!childPath && <small>Already on this path. Expansion stopped.</small>}
      </div>
      {(action || root) && <div className="relationship-actions">
        {action && summary && <NodeAction {...props} node={summary} />}
        {root && <button type="button" onClick={() => props.focusRoot({ endpoint: root, summary, board })}>Focus in Outliner</button>}
      </div>}
    </div>
    {expanded && root && childPath && <RelationshipBranch {...props} root={root} path={childPath} />}
  </div>;
}

function usePage({ api, projectId, visible, direction, readKey }: BranchProps, root: RelationshipRoot) {
  const [read, setRead] = useState<{ page: RelationshipPage; nodeOrders: ReadonlyMap<string, number> }>();
  const page = read?.page;
  const [failure, setFailure] = useState<ReturnType<typeof readFailure>>();
  const [busy, setBusy] = useState(false);
  const completed = useRef<string>(null);
  const lane = useRef<AbortController>(null);
  const busyRef = useRef(false);
  const { item_id, version_id } = rootRef(root);
  const shot_id = root.kind === "shot" ? root.shot_id : undefined;

  useLayoutEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    lane.current = controller;
    const exact: RelationshipRoot = shot_id === undefined ? versionRoot({ item_id, version_id }) : { kind: "shot", board: { item_id, version_id }, shot_id };
    if (completed.current !== readKey) {
      setBusy(true);
      busyRef.current = true;
      setFailure(undefined);
      const order = nextArtifactReadOrder();
      void readRelationships(api, projectId, exact, direction, boundedSignal(controller.signal))
        .then(value => { if (!controller.signal.aborted) setRead({ page: value, nodeOrders: stampNodes(value, order) }); })
        .catch(error => { if (!controller.signal.aborted) setFailure(readFailure(error)); })
        .finally(() => {
          if (controller.signal.aborted) return;
          completed.current = readKey;
          busyRef.current = false;
          setBusy(false);
        });
    } else setBusy(false);
    return () => {
      controller.abort();
      lane.current = null;
      busyRef.current = false;
    };
  }, [api, projectId, direction, item_id, version_id, shot_id, visible, readKey]);

  async function loadMore() {
    const controller = lane.current;
    if (!visible || !read || !page?.next_cursor || !controller || busyRef.current || failure?.stale || completed.current !== readKey) return;
    busyRef.current = true;
    setBusy(true);
    setFailure(undefined);
    try {
      const order = nextArtifactReadOrder();
      const next = await readRelationships(api, projectId, root, direction, boundedSignal(controller.signal), page.next_cursor);
      if (!controller.signal.aborted) setRead({ page: appendPage(page, next), nodeOrders: stampNodes(next, order, read.nodeOrders) });
    } catch (error) {
      if (!controller.signal.aborted) setFailure(readFailure(error));
    } finally {
      if (!controller.signal.aborted) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }
  return { page, nodeOrders: read?.nodeOrders, failure, busy, loadMore };
}

function stampNodes(page: RelationshipPage, order: number, previous?: ReadonlyMap<string, number>) {
  const result = new Map(previous);
  for (const node of page.nodes) result.set(endpointKey(node), order);
  return result;
}

function RelationshipBranch({ root, path, initial, viewing, top, ...props }: BranchProps & {
  root: RelationshipRoot; path: ReadonlySet<string>; initial?: FocusedRoot; viewing?: boolean; top?: boolean;
}) {
  const { page, nodeOrders, failure, busy, loadMore } = usePage(props, root);
  const groups = useMemo(() => groupEdges(page?.items ?? [], props.direction), [page, props.direction]);
  const nodes = useMemo(() => new Map(page?.nodes.map(node => [endpointKey(node), node])), [page]);
  const overlay = (node?: RelationshipNodeSummary) => node && overlayNode(node, props.overlays, nodeOrders?.get(endpointKey(node)) ?? 0);
  const boardFor = (endpoint: RelationshipEndpoint) => endpoint.kind === "shot" || endpoint.kind === "panel" ? overlay(nodes.get(endpointKey(versionRoot(endpoint.board)))) : undefined;
  const rootSummary = nodes.get(endpointKey(root));
  const summary = overlay(rootSummary ?? initial?.summary);
  const action = nodeAction(summary);
  const coverage = page && coverageMessage(page);
  const rows = (Object.keys(groupNames) as (keyof typeof groupNames)[]).map(group => groups[group].length > 0 && (
    <div key={group} className="relationship-group" role="group" aria-label={groupNames[group]}>
      <p className="relationship-group-label" aria-hidden="true">{groupNames[group]}</p>
      {groups[group].map(({ key, node, edge }) => {
        const range = edge.relation === "bound_scene" ? edge.details.source_range as { start: number; end: number } | null : null;
        const sequenceSlot = edge.relation === "sequence_input" ? [edge.details.role, edge.details.entry_id ?? edge.details.track_id].filter(part => typeof part === "string").join(" · ") : "";
        const evidence = `${props.direction === "used_by" && edge.relation === "derived_from" ? "Derivative" : names[edge.relation]}${range ? ` · frames ${range.start}–${range.end}` : ""}${sequenceSlot ? ` · ${sequenceSlot}` : ""}`;
        return <RelationshipRow key={key} endpoint={node} summary={nodes.get(endpointKey(node))} nodeReadOrder={nodeOrders?.get(endpointKey(node)) ?? 0} board={boardFor(node)} path={path} evidence={evidence} {...props} />;
      })}
    </div>
  ));
  return <div className={top ? "relationship-root" : "relationship-branch"} role={top ? undefined : "group"}>
    {top && <div className="relationship-root-identity">
      <h3 tabIndex={-1} data-root-heading>{nodeLabel(root, summary)}</h3>
      <NodeMetadata endpoint={root} summary={summary} board={boardFor(root) ?? overlay(initial?.board)} viewing={viewing} badges={Boolean(rootSummary)} />
      {action && summary && !viewing && <div className="relationship-actions"><NodeAction {...props} node={summary} /></div>}
    </div>}
    {busy && <p role="status">Reading relationships…</p>}
    {failure && <div role="alert" className="relationship-notice">
      <p>{failure.text}{page && !failure.stale ? " Showing previously read relationships." : ""}</p>
      {failure.stale && <button type="button" onClick={props.restart}>Restart from page 1</button>}
    </div>}
    {coverage && (coverage.state === "partial" || (!busy && !failure)) && <p role="status" className={coverage.state === "partial" ? "relationship-notice" : undefined}>{coverage.text}</p>}
    {top ? <div role="tree" aria-label="Version relationships">{rows}</div> : rows}
    {page?.next_cursor && !failure?.stale && <button type="button" aria-disabled={busy} onClick={() => void loadMore()}>Load more</button>}
  </div>;
}

function RootPicker({ api, projectId, items, hydrated, visible, readKey, onPick }: Pick<OutlinerSurfaceProps, "api" | "projectId" | "items" | "hydrated" | "visible"> & {
  readKey: string; onPick: (version: VersionRecord | null) => void;
}) {
  const [item, setItem] = useState("");
  const [version, setVersion] = useState("");
  const published = items.filter(value => value.current_version_id);
  const itemId = published.some(value => value.item_id === item) ? item : "";
  const key = JSON.stringify([itemId, readKey]);
  const [read, setRead] = useState<{ key: string; versions?: VersionRecord[]; error?: string }>();
  const current = read?.key === key ? read : undefined;
  useLayoutEffect(() => {
    if (!visible || !itemId || read?.key === key) return;
    const controller = new AbortController();
    void readVersions(api, projectId, itemId, boundedSignal(controller.signal))
      .then(versions => { if (!controller.signal.aborted) setRead({ key, versions }); })
      .catch(error => { if (!controller.signal.aborted) setRead({ key, error: readFailure(error).text }); });
    return () => controller.abort();
  }, [api, projectId, itemId, key, read?.key, visible]);

  if (!published.length) return <p>{hydrated ? "No published versions in this project to navigate." : "Loading published items…"}</p>;
  return <div className="relationship-picker">
    <label>Published item<select value={itemId} onChange={event => { setItem(event.target.value); setVersion(""); onPick(null); }}>
      <option value="">Choose an item</option>
      {published.map(value => <option key={value.item_id} value={value.item_id}>{value.name} ({value.kind})</option>)}
    </select></label>
    {itemId && <>
      {!current && <p role="status">Reading exact published versions…</p>}
      {current?.error && <p role="alert">Could not read published versions. {current.error}</p>}
      {current?.versions && <label>Exact published version<select value={version} onChange={event => {
        setVersion(event.target.value);
        onPick(current.versions?.find(candidate => candidate.version_id === event.target.value) ?? null);
      }}>
        <option value="">Choose a version</option>
        {current.versions.map(value => <option key={value.version_id} value={value.version_id}>{value.name} · {value.version_id}</option>)}
      </select></label>}
    </>}
  </div>;
}

function RelationshipTree({ focused, viewing, home, ...props }: BranchProps & {
  focused: FocusedRoot; viewing: boolean; home: boolean;
}) {
  const key = endpointKey(focused.endpoint);
  const path = useMemo(() => new Set([key]), [key]);
  return <>
    {!home && <p>Focused in Outliner. Review is unchanged.</p>}
    <RelationshipBranch {...props} root={focused.endpoint} initial={focused} path={path} top viewing={viewing} />
  </>;
}

export const Outliner = memo(function Outliner(props: OutlinerSurfaceProps) {
  return <ProjectOutliner key={props.projectId} {...props} />;
});

function ProjectOutliner(props: OutlinerSurfaceProps) {
  const { connection, version } = props;
  const [reconnections, setReconnections] = useState(0);
  const previousConnection = useRef(connection);
  const live = useNodeOverlays(props, reconnections);
  useLayoutEffect(() => {
    if (previousConnection.current !== "Connected" && connection === "Connected") setReconnections(count => count + 1);
    previousConnection.current = connection;
  }, [connection]);
  // Changing the viewed version resets traversal, not project-local live overlays.
  return <OutlinerRoot key={JSON.stringify([version?.item_id, version?.version_id])} {...props} {...live} reconnections={reconnections} />;
}

function OutlinerRoot({ overlays, selectionError, reconnections, ...props }: OutlinerSurfaceProps & { overlays: NodeOverlays; selectionError: boolean; reconnections: number }) {
  const { version, topologyRevision, visible } = props;
  const [direction, setDirection] = useState<RelationshipDirection>("uses");
  const [history, setHistory] = useState<FocusedRoot[]>([]);
  const [revision, setRevision] = useState(0);
  const panel = useRef<HTMLElement>(null);
  const focusHeading = useRef(false);
  useLayoutEffect(() => {
    if (!focusHeading.current) return;
    focusHeading.current = false;
    panel.current?.querySelector<HTMLElement>("[data-root-heading]")?.focus();
  }, [history]);

  const focused = history.at(-1) ?? (version ? versionSeed(version) : null);
  const restart = () => setRevision(value => value + 1);
  const readKey = JSON.stringify([topologyRevision, reconnections, revision]);
  const viewing = Boolean(version && focused?.endpoint.kind === "version" && focused.endpoint.ref.version_id === version.version_id);
  return <section className="relationship-outliner" aria-label="Published Outliner" hidden={!visible} ref={panel}>
    <h2>Published relationships</h2>
    {selectionError && <p role="status">Live panel choices could not be read. Showing previously read choices.</p>}
    {!version && <RootPicker {...props} readKey={readKey} onPick={record => setHistory(record ? [versionSeed(record)] : [])} />}
    <div className="relationship-actions">
      {history.length > Number(!version) && <button type="button" onClick={() => { focusHeading.current = true; setHistory(previous => previous.slice(0, -1)); }}>Back</button>}
      <button type="button" onClick={restart}>Refresh relationships</button>
    </div>
    {focused && <>
      <div className="relationship-actions" role="group" aria-label="Relationship direction">
        <button type="button" aria-pressed={direction === "uses"} onClick={() => setDirection("uses")}>Uses</button>
        <button type="button" aria-pressed={direction === "used_by"} onClick={() => setDirection("used_by")}>Used by</button>
      </div>
      <RelationshipTree key={JSON.stringify([endpointKey(focused.endpoint), direction])} {...props} overlays={overlays} focused={focused} viewing={viewing} home={history.length <= Number(!version)} direction={direction} readKey={readKey} restart={restart} focusRoot={next => {
        focusHeading.current = true;
        setHistory(previous => [...previous, next]);
      }} />
    </>}
  </section>;
}

function versionSeed(version: VersionRecord): FocusedRoot {
  const ref = { item_id: version.item_id, version_id: version.version_id };
  return { endpoint: versionRoot(ref), summary: { kind: "version", ref, label: version.name, item_kind: version.kind, is_current: false, is_selected: false } };
}
