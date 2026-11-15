import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { Event, RequestRecord } from "../../api";

const BOTTOM_THRESHOLD = 48;

export function useChatScroll(projectId: string, requests: RequestRecord[], events: Event[]) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastScrollTop = useRef(0);
  const [isFollowing, setIsFollowing] = useState(true);

  const scrollToLatest = useCallback(() => {
    const viewport = scrollRef.current;
    if (viewport && viewport.clientHeight > 0) {
      viewport.scrollTop = viewport.scrollHeight;
      lastScrollTop.current = viewport.scrollTop;
    }
  }, []);

  const onScroll = useCallback(() => {
    const viewport = scrollRef.current;
    if (!viewport || viewport.clientHeight === 0) return;
    const atBottom = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= BOTTOM_THRESHOLD;
    const movedUp = viewport.scrollTop < lastScrollTop.current - 1;
    lastScrollTop.current = viewport.scrollTop;
    // Layout can increase the bottom gap without the user scrolling away.
    if (following.current && !atBottom && !movedUp) return;
    following.current = atBottom;
    setIsFollowing(atBottom);
  }, []);

  const jumpToLatest = useCallback(() => {
    following.current = true;
    setIsFollowing(true);
    scrollToLatest();
  }, [scrollToLatest]);

  useLayoutEffect(() => {
    jumpToLatest();
  }, [projectId, jumpToLatest]);

  useLayoutEffect(() => {
    if (following.current) scrollToLatest();
  }, [requests, events, scrollToLatest]);

  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;
    // Streaming, expanded tool output, fonts and composer resizing can change height.
    const observer = new ResizeObserver(() => {
      if (following.current) scrollToLatest();
    });
    observer.observe(viewport);
    observer.observe(content);
    return () => observer.disconnect();
  }, [scrollToLatest]);

  return { scrollRef, contentRef, isFollowing, onScroll, jumpToLatest };
}
