import { useLayoutEffect, useMemo, useState } from "react";
import type { ItemRecord, RelationshipNodeSummary, RelationshipPanelEndpoint, SelectionRecord, VersionRecord } from "../../api/generated/contracts";
import type { OutlinerSurfaceProps } from "../../api/studio";
import { useArtifactObservations, type ArtifactObservation } from "../readyArtifactIndex";
import { allPages } from "../review/records";
import { boundedSignal, endpointKey, readVersions, versionRoot } from "./model";

type OverlayScope = Pick<OutlinerSurfaceProps, "api" | "projectId" | "visible">;
export type NodeOverlays = {
  items: ReadonlyMap<string, ItemRecord>;
  artefacts: ReadonlyMap<string, ArtifactObservation>;
  panels?: ReadonlyMap<string, SelectionRecord>;
};

export function panelSelections(projectId: string, records: SelectionRecord[]) {
  const slots = new Map<string, SelectionRecord>();
  for (const record of records) {
    const { slot } = record;
    if (record.project_id !== projectId || slot.kind !== "storyboard_frame") continue;
    const key = endpointKey({ kind: "panel", board: slot.storyboard_version, shot_id: slot.shot_id, frame_id: slot.frame_id });
    if ((slots.get(key)?.state_revision ?? -1) <= record.state_revision) slots.set(key, record);
  }
  return slots;
}

export function overlayNode(node: RelationshipNodeSummary, overlays: NodeOverlays, readOrder: number, chosenVersion?: VersionRecord): RelationshipNodeSummary {
  if (node.kind === "version") {
    const item = overlays.items.get(node.ref.item_id);
    return item ? { ...node, is_current: item.current_version_id === node.ref.version_id, is_selected: item.selected_version_id === node.ref.version_id } : node;
  }
  if (node.kind !== "panel" && node.kind !== "artifact") return node;
  const observed = node.artifact_id ? overlays.artefacts.get(node.artifact_id) : undefined;
  const liveState = observed && observed.readOrder > readOrder ? observed.artefact.state : undefined;
  if (node.kind === "artifact") return liveState ? { ...node, state: liveState } : node;
  const record = overlays.panels?.get(endpointKey(node));
  const choice = record?.state === "selected" ? record.choice : null;
  // An admitted panel version choice contains exactly one image. Authored panel
  // artefacts are images, so exact membership in that version proves the match.
  const chosen = choice?.kind === "artifact" ? choice.artifact_id === node.artifact_id
    : choice?.kind === "version" && !!node.artifact_id && chosenVersion?.item_id === choice.version.item_id &&
      chosenVersion.version_id === choice.version.version_id && chosenVersion.artifact_ids.includes(node.artifact_id);
  return { ...node, artifact_state: liveState ?? node.artifact_state,
    owner_selected: overlays.panels ? Boolean(chosen) : node.owner_selected };
}

export function useNodeOverlays({ api, projectId, visible, selectionRevision, items, retainedArtifacts, retainedArtifactReadOrder }: OverlayScope & Pick<OutlinerSurfaceProps, "selectionRevision" | "items" | "retainedArtifacts" | "retainedArtifactReadOrder">, reconnect: number) {
  const { observations } = useArtifactObservations(projectId, retainedArtifacts, undefined, retainedArtifactReadOrder);
  const [read, setRead] = useState<{ revision: number; reconnect: number; panels?: NodeOverlays["panels"] }>();
  const readRevision = read?.revision;
  useLayoutEffect(() => {
    if (!visible || selectionRevision === 0 || readRevision === selectionRevision) return;
    const controller = new AbortController();
    const signal = boundedSignal(controller.signal);
    void allPages(cursor => {
      signal.throwIfAborted();
      return api.call("listSelections", { path: { project_id: projectId }, query: { cursor, limit: 100 }, signal });
    }).then(records => {
      signal.throwIfAborted();
      setRead({ revision: selectionRevision, reconnect, panels: panelSelections(projectId, records) });
    }).catch(() => {
      if (!controller.signal.aborted) setRead({ revision: selectionRevision, reconnect });
    });
    return () => controller.abort();
  }, [api, projectId, visible, selectionRevision, readRevision, reconnect]);
  const itemIndex = useMemo(() => new Map(items.filter(item => item.project_id === projectId).map(item => [item.item_id, item])), [items, projectId]);
  return {
    // On reconnect the fresh relationship read already carries server choices.
    overlays: { items: itemIndex, artefacts: observations.ordered, panels: read?.reconnect === reconnect ? read.panels : undefined },
    selectionError: read?.reconnect === reconnect && read.revision === selectionRevision && !read.panels,
  };
}

export function usePanelChoiceVersion({ api, projectId, visible }: OverlayScope, endpoint: RelationshipPanelEndpoint | null, overlays: NodeOverlays) {
  const record = endpoint && overlays.panels?.get(endpointKey(endpoint));
  const choice = record && record.state === "selected" && record.choice?.kind === "version" ? record.choice.version : null;
  const itemId = choice?.item_id;
  const versionId = choice?.version_id;
  const key = choice ? endpointKey(versionRoot(choice)) : null;
  const [read, setRead] = useState<{ key: string; version?: VersionRecord }>();
  useLayoutEffect(() => {
    if (!visible || !itemId || !versionId || !key || read?.key === key) return;
    const controller = new AbortController();
    void readVersions(api, projectId, itemId, boundedSignal(controller.signal), versionId)
      .then(([version]) => { if (!controller.signal.aborted) setRead({ key, version }); })
      .catch(() => { if (!controller.signal.aborted) setRead({ key }); });
    return () => controller.abort();
  }, [api, projectId, visible, itemId, versionId, key, read?.key]);
  return read?.key === key ? read?.version : undefined;
}
