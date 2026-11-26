import type { Artifact } from "../api";

const videoMediaTypes = new Set(["video/mp4"]);

export function isReadyVideo(artifact: Artifact | null): boolean {
  return artifact?.kind === "video" && artifact.state === "ready" && videoMediaTypes.has(artifact.media_type);
}

export function isPlayableConceptVideo(artifact: Artifact | null): boolean {
  return artifact?.purpose === "concept" && isReadyVideo(artifact)
    && !artifact.artifact_id.startsWith("attempt-") && !artifact.artifact_id.startsWith("preview-");
}
