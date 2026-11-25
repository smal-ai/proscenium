import type { Artifact } from "../api";

const audioMediaTypes = new Set(["audio/flac", "audio/wav", "audio/mpeg", "audio/ogg"]);

export function isReadyAudio(artifact: Artifact | null): boolean {
  return artifact?.kind === "audio" && artifact.state === "ready" && audioMediaTypes.has(artifact.media_type);
}
