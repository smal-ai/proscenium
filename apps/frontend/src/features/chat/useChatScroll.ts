import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { Event, RequestRecord } from "../../api";

const BOTTOM_THRESHOLD = 48;

export function useChatScroll(projectId: string, requests: RequestRecord[], events: Event[]) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastScrollTop = useRef(0);
  const prevFirstSeq = useRef<number | null>(null);
  const prevScrollHeight = useRef(0);
  const firstSeq = events[0]?.sequence ?? null;
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
    prevScrollHeight.current = viewport.scrollHeight;
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
    prevFirstSeq.current = null;
    jumpToLatest();
  }, [projectId, jumpToLatest]);

  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    if (following.current) scrollToLatest();
    else if (viewport && firstSeq !== null && prevFirstSeq.current !== null && firstSeq < prevFirstSeq.current) {
      viewport.scrollTop += Math.max(0, viewport.scrollHeight - prevScrollHeight.current);
      lastScrollTop.current = viewport.scrollTop;
    }
    prevFirstSeq.current = firstSeq;
    if (viewport) prevScrollHeight.current = viewport.scrollHeight;
  }, [requests, events, firstSeq, scrollToLatest]);

  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;
    // Use one anchoring mechanism, not Chromium's adjustment on top of ours.
    const oldAnchor = viewport.style.overflowAnchor;
    viewport.style.overflowAnchor = "none";
    // Streaming, expanded tool output, fonts and composer resizing can change height.
    const observer = new ResizeObserver(() => {
      if (following.current) scrollToLatest();
      prevScrollHeight.current = viewport.scrollHeight;
    });
    observer.observe(viewport);
    observer.observe(content);
    return () => { observer.disconnect(); viewport.style.overflowAnchor = oldAnchor; };
  }, [scrollToLatest]);

  return { scrollRef, contentRef, isFollowing, onScroll, jumpToLatest };
}
