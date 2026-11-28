import { ApiError, type ApiClient } from "../../api/client";
import type { RelationshipDirection, RelationshipEdge, RelationshipEndpoint, RelationshipNodeSummary, RelationshipPage, RelationshipRelation, RelationshipRoot, VersionRef } from "../../api/generated/contracts";

export const versionRoot = (ref: VersionRef): RelationshipRoot => ({ kind: "version", ref });
export const rootRef = (root: RelationshipRoot) => root.kind === "version" ? root.ref : root.board;

export function endpointKey(node: RelationshipEndpoint): string {
  switch (node.kind) {
    case "version": return JSON.stringify([node.kind, node.ref.item_id, node.ref.version_id]);
    case "shot": return JSON.stringify([node.kind, node.board.item_id, node.board.version_id, node.shot_id]);
    case "panel": return JSON.stringify([node.kind, node.board.item_id, node.board.version_id, node.shot_id, node.frame_id]);
    case "artifact": return JSON.stringify([node.kind, node.artifact_id]);
  }
}

export function extendPath(path: ReadonlySet<string>, node: RelationshipEndpoint): ReadonlySet<string> | null {
  const key = endpointKey(node);
  return path.has(key) ? null : new Set([...path, key]);
}

const relationGroup = {
  contains_shot: "structure", contains_panel: "structure",
  board_reference: "inputs", bound_scene: "inputs", sequence_input: "inputs", declared_dependency: "inputs",
  derived_from: "provenance",
} as const satisfies Record<RelationshipRelation, string>;

// The generator represents the sequence-input variant as an open object. Keep
// its identity/details, but only display relations that this increment knows.
type KnownEdge = { relation: RelationshipRelation; from: RelationshipEndpoint; to: RelationshipEndpoint; details: Record<string, unknown> };
function displayableEdge(edge: RelationshipEdge): edge is KnownEdge {
  return typeof edge.relation === "string" && Object.hasOwn(relationGroup, edge.relation)
    && Boolean(edge.from && edge.to && edge.details);
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, part]) => [key, canonical(part)]));
  }
  return value;
}

export function groupEdges(items: RelationshipEdge[], direction: RelationshipDirection) {
  const groups = { structure: [], inputs: [], provenance: [] } as Record<typeof relationGroup[RelationshipRelation], { key: string; node: RelationshipEndpoint; edge: KnownEdge }[]>;
  const seen = new Set<string>();
  for (const edge of items) {
    if (!displayableEdge(edge)) continue;
    const key = JSON.stringify([edge.relation, endpointKey(edge.from), endpointKey(edge.to), canonical(edge.details)]);
    if (seen.has(key)) continue;
    seen.add(key);
    groups[relationGroup[edge.relation]].push({ key, node: direction === "uses" ? edge.to : edge.from, edge });
  }
  // The wire order is deterministic across pages and preserves authored ordinals.
  return groups;
}

export function appendPage(previous: RelationshipPage, page: RelationshipPage): RelationshipPage {
  if (previous.relationship_revision !== page.relationship_revision) throw new RelationshipRevisionChanged();
  const nodes = new Map<string, RelationshipNodeSummary>(previous.nodes.map(node => [endpointKey(node), node]));
  for (const node of page.nodes) nodes.set(endpointKey(node), node);
  return { ...page, nodes: [...nodes.values()], items: [...previous.items, ...page.items] };
}

export function coverageMessage(page: RelationshipPage): { state: "partial" | "empty"; text: string } | null {
  if (page.coverage.state === "partial") {
    const reasons = page.coverage.reasons.join(", ");
    return { state: "partial", text: `Relationships are incomplete${reasons ? `: ${reasons}` : ""}. Published relationships may be missing.` };
  }
  if (page.next_cursor !== null || page.items.length) return null;
  return { state: "empty", text: page.direction === "uses" ? "No published dependencies" : page.root.kind === "shot" ? "No published uses of this shot" : "No published uses of this version" };
}

class RelationshipRevisionChanged extends Error {}

export function readFailure(error: unknown): { stale: boolean; text: string } {
  if (error instanceof RelationshipRevisionChanged || (error instanceof ApiError && error.status === 409 && error.envelope.error.code === "relationship_revision_changed")) {
    return { stale: true, text: "Relationships changed while you were browsing. Restart from page 1." };
  }
  const text = error instanceof ApiError ? `Request failed (HTTP ${error.status}).`
    : error instanceof Error && error.name === "TimeoutError" ? "Request timed out."
    : "Request could not be completed.";
  return { stale: false, text };
}

export async function readRelationships(api: ApiClient, projectId: string, root: RelationshipRoot, direction: RelationshipDirection, signal: AbortSignal, cursor?: string) {
  const page = await api.call("getVersionRelationships", {
    path: { project_id: projectId, version_id: rootRef(root).version_id },
    query: { direction, shot_id: root.kind === "shot" ? root.shot_id : undefined, cursor }, signal,
  });
  signal.throwIfAborted();
  if (page.project_id !== projectId || page.direction !== direction || endpointKey(page.root) !== endpointKey(root)) throw new Error("Relationship response scope mismatch");
  return page;
}

export function boundedSignal(scope: AbortSignal) {
  return AbortSignal.any([scope, AbortSignal.timeout(15_000)]);
}

export async function readVersions(api: ApiClient, projectId: string, itemId: string, signal: AbortSignal, exactId?: string) {
  const found = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await api.call("listItemVersions", { path: { project_id: projectId, item_id: itemId }, query: { cursor, limit: 100 }, signal });
    signal.throwIfAborted();
    for (const version of page.items) {
      if (version.project_id !== projectId || version.item_id !== itemId) throw new Error("Version response scope mismatch");
      if (version.version_id === exactId) return [version];
      if (exactId === undefined) found.push(version);
    }
    cursor = page.next_cursor ?? undefined;
    if (cursor && seen.has(cursor)) throw new Error("Version pagination did not advance");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return found;
}
