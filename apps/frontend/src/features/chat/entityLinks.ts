import type { Artifact, Event, ItemRecord, JobRecord, ProjectSnapshot, RetainedArtifactSummary, ReviewTicket, VersionRef } from "../../api";
import { shortVersionLabel } from "../catalogue/catalogueHelpers";
import { isReadyAudio } from "../retainedAudio";
import { isReadyVideo } from "../retainedVideo";
import { reviewDisplayName } from "../review/reviewDisplayName";

export type ChatEntityLink = { id: string; label: string } & (
  | { kind: "item" | "version"; itemId: string; versionId: string }
  | { kind: "artifact"; artifact: Artifact; baseLabel: string }
  | { kind: "review"; reviewId: string }
);
export type ChatEntityIndex = ReadonlyMap<string, ChatEntityLink>;

interface EntitySources {
  projectId: string;
  items?: readonly ItemRecord[];
  retainedArtifacts?: readonly RetainedArtifactSummary[];
  jobs?: readonly JobRecord[];
  snapshot?: ProjectSnapshot | null;
  events?: readonly Event[];
  reviews?: readonly (ReviewTicket & { project_id?: string })[];
}

const imageMediaTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
export function isReadyChatMedia(artifact: Artifact): boolean {
  return (artifact.kind === "image" && artifact.state === "ready" && imageMediaTypes.has(artifact.media_type))
    || isReadyAudio(artifact) || isReadyVideo(artifact);
}

function artifactLabel({ purpose, kind }: Artifact): string {
  const purposeLabel = purpose.replaceAll("_", " ");
  const phrase = purposeLabel === kind ? kind : `${purposeLabel} ${kind}`;
  return phrase[0].toUpperCase() + phrase.slice(1);
}

/** Index only metadata already loaded for the active project. Later kinds win ID collisions. */
export function buildEntityIndex({ projectId, items = [], retainedArtifacts = [], jobs = [], snapshot, events = [], reviews = [] }: EntitySources): ChatEntityIndex {
  const entities = new Map<string, ChatEntityLink>();
  const names = new Map<string, string>();
  const scopedItems = items.filter(item => item.project_id === projectId);
  const scopedArtifacts = retainedArtifacts.filter(summary => summary.artifact.project_id === projectId);
  for (const ticket of reviews) {
    if (ticket.project_id !== projectId) continue;
    const id = ticket.review_id;
    entities.set(id, { kind: "review", id, reviewId: id, label: reviewDisplayName(ticket) });
  }
  for (const { artifact } of scopedArtifacts) {
    if (!isReadyChatMedia(artifact)) continue;
    const id = artifact.artifact_id;
    const baseLabel = artifactLabel(artifact);
    entities.set(id, { kind: "artifact", id, artifact, baseLabel, label: baseLabel });
  }
  for (const item of scopedItems) {
    names.set(item.item_id, item.name);
    const versionId = item.selected_version_id ?? item.current_version_id;
    if (!versionId) continue;
    const id = item.item_id;
    entities.set(id, { kind: "item", id, itemId: id, versionId, label: item.name });
  }
  function addVersion({ item_id: itemId, version_id: versionId }: VersionRef) {
    const name = names.get(itemId);
    const short = shortVersionLabel(versionId);
    entities.set(versionId, { kind: "version", id: versionId, itemId, versionId, label: name === undefined ? short : `${name} · ${short}` });
  }
  for (const event of events) {
    if (event.project_id !== projectId || event.event_type !== "publication.created") continue;
    const { published_versions, versions, project_id } = event.payload as { published_versions?: unknown; versions?: unknown; project_id?: string };
    if (project_id !== undefined && project_id !== projectId) continue;
    // The publication receipt emits `versions`; work output summaries use `published_versions`.
    const refs = published_versions ?? versions;
    if (Array.isArray(refs)) for (const ref of refs) {
      if (ref && typeof ref.item_id === "string" && typeof ref.version_id === "string") addVersion(ref);
    }
  }
  if (snapshot?.project.project_id === projectId) {
    for (const output of snapshot.execution?.work_outputs ?? []) {
      for (const ref of output.published_versions ?? []) addVersion(ref);
    }
  }
  for (const job of jobs) {
    if (job.project_id === projectId && job.target.version) addVersion(job.target.version);
  }
  for (const summary of scopedArtifacts) {
    for (const ref of summary.published_versions ?? []) addVersion(ref);
  }
  for (const item of scopedItems) {
    for (const versionId of [item.current_version_id, item.selected_version_id]) {
      if (versionId) addVersion({ item_id: item.item_id, version_id: versionId });
    }
  }
  return entities;
}

const idChar = /[A-Za-z0-9._-]/;
const startsId = /[A-Za-z0-9]/;
const maxIdLength = 128;
export const isWord = (text: string) => /[\p{L}\p{M}\p{N}]/u.test(text);

/** Resolve the longest known ID, leaving sentence punctuation and Markdown closing delimiters intact. */
export function matchChatEntity(source: string, start: number, entities: ChatEntityIndex, { afterOpenUnderscore = false, openUnderscoreDepth = 0 } = {}) {
  const previous = source[start - 1];
  if (!startsId.test(source[start] ?? "") || (start > 0 && idChar.test(previous) && !(afterOpenUnderscore && previous === "_"))) return;
  let end = start + 1;
  while (end < source.length && idChar.test(source[end])) end++;
  const raw = source.slice(start, end);
  let length = raw.length;
  let entity = entities.get(raw);
  while (!entity && length > 1 && raw[length - 1] === ".") {
    length--;
    // Contract IDs are bounded. Do not copy or hash unbounded punctuation runs repeatedly.
    if (length <= maxIdLength) entity = entities.get(raw.slice(0, length));
  }
  if (entity) return { entity, end: start + length };
  if (openUnderscoreDepth === 0 || isWord(String.fromCodePoint(source.codePointAt(start + length) ?? 32))) return;
  // The token may have swallowed a closing run. Restore only as much as the
  // open delimiters can close, so indexed trailing underscores win first.
  for (let closing = 1; closing <= openUnderscoreDepth && closing <= 3 && length > 1 && raw[length - 1] === "_"; closing++) {
    length--;
    if (length <= maxIdLength) {
      entity = entities.get(raw.slice(0, length));
      if (entity) return { entity, end: start + length };
    }
  }
}

export function disambiguateArtifactLabels(groups: ReadonlyMap<string, ReadonlySet<string>>) {
  const labels = new Map<string, string>();
  for (const [label, ids] of groups) {
    if (ids.size < 2) continue;
    const bySuffix = new Map<string, string[]>();
    for (const id of ids) {
      const tail = id.split(/[-._]+/).filter(Boolean).at(-1) ?? id;
      const suffix = /^\d+$/.test(tail) ? `#${tail}` : tail.slice(-12);
      const matches = bySuffix.get(suffix);
      if (matches) matches.push(id);
      else bySuffix.set(suffix, [id]);
    }
    for (const [suffix, matchingIds] of bySuffix) {
      if (matchingIds.length === 1) {
        labels.set(matchingIds[0], `${label} · ${suffix}`);
      } else {
        // Different generation batches can both end in artifact-0. Extend their tails until distinct.
        let length = 16;
        while (new Set(matchingIds.map(id => id.slice(-length))).size < matchingIds.length) length += 4;
        for (const id of matchingIds) labels.set(id, `${label} · ${suffix} (${id.slice(-length)})`);
      }
    }
  }
  return labels;
}
