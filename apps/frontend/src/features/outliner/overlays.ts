import { useCallback, useLayoutEffect, useMemo, useState } from "react";
import type { ApiClient } from "../../api/client";
import type { ItemRecord, RelationshipNodeSummary, SelectionRecord, ShotPlan, VersionRef } from "../../api/generated/contracts";
import type { OutlinerSurfaceProps } from "../../api/studio";
import { useArtifactObservations, type ArtifactObservation } from "../readyArtifactIndex";
import { allPages } from "../review/records";
import { boundedSignal, endpointKey } from "./model";

type OverlayScope = Pick<OutlinerSurfaceProps, "api" | "projectId" | "visible">;
export type NodeOverlays = {
  items: ReadonlyMap<string, ItemRecord>;
  artefacts: ReadonlyMap<string, ArtifactObservation>;
  panels?: ReadonlyMap<string, SelectionRecord>;
  shotPlans?: ReadonlyMap<string, ShotPlan>;
  readShotPlan?: (board: VersionRef) => void;
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

export function overlayNode(node: RelationshipNodeSummary, overlays: NodeOverlays, readOrder: number): RelationshipNodeSummary {
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
  // Version views merge render outputs. Only the server's shot plan resolves
  // version choices against the immutable publication's image membership.
  const plan = overlays.shotPlans?.get(node.board.version_id);
  const frame = plan?.shots.find(shot => shot.shot_id === node.shot_id)?.frames.find(frame => frame.frame_id === node.frame_id && frame.artifact_id === node.artifact_id);
  const chosen = choice?.kind === "artifact" ? choice.artifact_id === node.artifact_id
    : choice?.kind === "version" ? (plan ? Boolean(frame?.owner_selected) : node.owner_selected) : false;
  return { ...node, artifact_state: liveState ?? node.artifact_state,
    owner_selected: overlays.panels ? chosen : node.owner_selected };
}

export function panelPlanReads(api: ApiClient, projectId: string) {
  const requests = new Map<string, AbortController>();
  return {
    read(board: VersionRef) {
      if (requests.has(board.version_id)) return;
      const controller = new AbortController();
      requests.set(board.version_id, controller);
      const signal = boundedSignal(controller.signal);
      return api.call("getVersionShotPlan", { path: { project_id: projectId, version_id: board.version_id }, signal }).then(plan => {
        signal.throwIfAborted();
        if (plan.project_id !== projectId) throw new Error("Shot plan does not match the active project");
        return plan;
      });
    },
    abort() {
      for (const controller of requests.values()) controller.abort();
      requests.clear();
    },
  };
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
  const lane = useMemo(() => panelPlanReads(api, projectId), [api, projectId, selectionRevision, reconnect]);
  const [plans, setPlans] = useState<{ lane: typeof lane; boards: Map<string, ShotPlan>; error?: boolean }>();
  useLayoutEffect(() => lane.abort, [lane]);
  const readShotPlan = useCallback((board: VersionRef) => {
    if (!visible) return;
    void lane.read(board)?.then(plan => {
      setPlans(previous => {
        const boards = new Map(previous?.lane === lane ? previous.boards : undefined);
        boards.set(board.version_id, plan);
        return { lane, boards, error: previous?.lane === lane && previous.error };
      });
    }).catch(error => {
      if (error?.name !== "AbortError") setPlans(previous => ({ lane, boards: previous?.lane === lane ? previous.boards : new Map(), error: true }));
    });
  }, [lane, visible]);
  const currentPlans = plans?.lane === lane ? plans : undefined;
  return {
    // On reconnect the fresh relationship read already carries server choices.
    overlays: { items: itemIndex, artefacts: observations.ordered, panels: read?.reconnect === reconnect && read.revision === selectionRevision ? read.panels : undefined,
      shotPlans: currentPlans?.boards, readShotPlan },
    selectionError: Boolean(currentPlans?.error || (read?.reconnect === reconnect && read.revision === selectionRevision && !read.panels)),
  };
}
