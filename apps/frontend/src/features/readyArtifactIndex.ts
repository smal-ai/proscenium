import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Artifact, RetainedArtifactSummary } from "../api";

/** Only retain ready records. Explicit non-ready evidence invalidates existing metadata. */
export function seedReadyArtifacts(
  index: Map<string, Artifact>, projectId: string, summaries: readonly RetainedArtifactSummary[] | undefined,
  wanted?: ReadonlySet<string>,
): void {
  for (const { artifact } of summaries ?? []) {
    if (artifact.project_id !== projectId) continue;
    const key = `${projectId}:${artifact.artifact_id}`;
    if (artifact.state !== "ready") index.delete(key);
    else if (!wanted || wanted.has(artifact.artifact_id)) index.set(key, artifact);
  }
}

export function nonReadyArtifactIndex(projectId: string, summaries?: readonly RetainedArtifactSummary[]): ReadonlyMap<string, Artifact> {
  const index = new Map<string, Artifact>();
  for (const { artifact } of summaries ?? []) {
    if (artifact.project_id !== projectId) continue;
    if (artifact.state === "ready") index.delete(artifact.artifact_id);
    else index.set(artifact.artifact_id, artifact);
  }
  return index;
}

function emptyObservations(projectId: string) {
  return { projectId, observed: new Map<string, Artifact>(), revoked: new Map<string, Artifact>(), lastRevoked: new Map<string, number>(), sequence: 0 };
}

export function useArtifactObservations(projectId: string, summaries?: readonly RetainedArtifactSummary[], inspectedSummary?: RetainedArtifactSummary | null) {
  const [initial] = useState(() => emptyObservations(projectId));
  const current = useRef(initial);
  const previousInspected = useRef<typeof inspectedSummary>(undefined);
  const previousSummaries = useRef<typeof summaries>(undefined);
  const observations = useMemo(() => {
    const sameProject = current.current.projectId === projectId;
    const previous = sameProject ? current.current : emptyObservations(projectId);
    const additional = inspectedSummary !== (sameProject ? previousInspected.current : undefined) ? inspectedSummary?.artifact : undefined;
    const updates = new Map<string, Artifact>();
    if (additional?.project_id === projectId && !summaries?.some(({ artifact }) => artifact.project_id === projectId && artifact.artifact_id === additional.artifact_id)) {
      updates.set(additional.artifact_id, additional);
    }
    if (!sameProject || summaries !== previousSummaries.current) {
      for (const { artifact } of summaries ?? []) if (artifact.project_id === projectId) updates.set(artifact.artifact_id, artifact);
    }
    if (!updates.size) return previous;
    const next = { ...previous, observed: new Map(previous.observed), revoked: new Map(previous.revoked), lastRevoked: new Map(previous.lastRevoked) };
    for (const [id, artifact] of updates) {
      next.observed.set(id, artifact);
      if (artifact.state === "ready") next.revoked.delete(id);
      else {
        next.revoked.set(id, artifact);
        next.lastRevoked.set(id, ++next.sequence);
      }
    }
    return next;
  }, [projectId, summaries, inspectedSummary]);
  // Only committed evidence can fence an asynchronous read.
  useLayoutEffect(() => {
    current.current = observations;
    previousInspected.current = inspectedSummary;
    previousSummaries.current = summaries;
  }, [observations, inspectedSummary, summaries]);
  return { observations, current };
}
