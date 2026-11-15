import { useLayoutEffect, useRef } from "react";
import type { FrameTarget } from "../../api/generated/contracts";
import { mediaUrl } from "./media";

/** Pixel identity deliberately excludes semantic shot/frame/version context. */
export const decodedImageIdentity = (projectId: string, target: FrameTarget | null) => target
  ? JSON.stringify([mediaUrl(projectId, target.artifact_id), target.displayed_sha256, target.dimensions.width, target.dimensions.height])
  : null;

export function DecodedReviewImage({visible = true, projectId, target, onReadiness, onFailure}: {
  visible?: boolean;
  projectId: string; target: FrameTarget; onReadiness: (identity: string | null) => void; onFailure: (message: string) => void;
}) {
  const image = useRef<HTMLImageElement>(null);
  const source = mediaUrl(projectId, target.artifact_id);
  const identity = decodedImageIdentity(projectId, target)!;
  function verify() {
    if (!visible) { onReadiness(null); return false; }
    const element = image.current;
    // currentSrc can still name the previous URL during a pending src change.
    // Revalidate the live DOM for every event, never trust an old event's target.
    const matching = !!element && element.currentSrc === new URL(source, location.href).href
      && element.complete && element.naturalWidth === target.dimensions.width
      && element.naturalHeight === target.dimensions.height && element.naturalWidth > 0;
    onReadiness(matching ? identity : null);
    if (element?.complete && element.currentSrc === new URL(source, location.href).href
      && element.naturalWidth > 0 && !matching) onFailure("Decoded image dimensions do not match target");
    return matching;
  }
  // Cached images may finish before React's load listener/semantic navigation.
  // Same URL, new shot/frame needs no load event: its verified pixel identity
  // stays valid while Workspace supplies the new semantic FrameTarget unchanged.
  useLayoutEffect(() => {
    verify();
    return () => onReadiness(null);
  }, [identity, onReadiness, visible]);

  return <img ref={image} src={source} alt={`${target.origin}, frame ${target.sequence_frame ?? target.source_frame ?? "image"}`}
    onLoad={verify} onError={() => {
      const matching = verify();
      if (!matching && image.current?.complete && image.current.currentSrc === new URL(source, location.href).href) onFailure(`media_unavailable: ${target.artifact_id}`);
    }}/>;
}
