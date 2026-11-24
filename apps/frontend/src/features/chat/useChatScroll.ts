import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { Event, RequestRecord } from "../../api";

const BOTTOM_THRESHOLD = 48;

export function useChatScroll(projectId: string, requests: RequestRecord[], events: Event[]) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastScrollTop = useRef(0);
  const anchorRef = useRef<Array<{ key: string; memberKeys: string[]; offsetTop: number }>>([]);
  const [isFollowing, setIsFollowing] = useState(true);

  const scrollToLatest = useCallback(() => {
    const viewport = scrollRef.current;
    if (viewport && viewport.clientHeight > 0) {
      viewport.scrollTop = viewport.scrollHeight;
      lastScrollTop.current = viewport.scrollTop;
    }
  }, []);

  const captureAnchor = useCallback(() => {
    const viewport = scrollRef.current;
    const entries = contentRef.current?.querySelectorAll<HTMLElement>("[data-entry-key]");
    anchorRef.current = [];
    if (following.current || !viewport || !entries) return;
    const bounds = viewport.getBoundingClientRect();
    for (const el of entries) {
      const rect = el.getBoundingClientRect();
      if (rect.bottom > bounds.top && rect.top < bounds.bottom) {
        anchorRef.current.push({ key: el.dataset.entryKey!, memberKeys: el.dataset.memberKeys?.split("\n") ?? [], offsetTop: rect.top - bounds.top });
      } else if (rect.top >= bounds.bottom) {
        break;
      }
    }
  }, []);

  const restoreAnchor = useCallback(() => {
    if (following.current) {
      scrollToLatest();
      anchorRef.current = [];
      return;
    }
    const viewport = scrollRef.current;
    const entries = contentRef.current?.querySelectorAll<HTMLElement>("[data-entry-key]");
    if (viewport && entries && anchorRef.current.length) {
      const byKey = new Map<string, HTMLElement>();
      for (const el of entries) {
        byKey.set(el.dataset.entryKey!, el);
        for (const key of el.dataset.memberKeys?.split("\n") ?? []) byKey.set(key, el);
      }
      for (const anchor of anchorRef.current) {
        const entry = byKey.get(anchor.key) ?? anchor.memberKeys.map(key => byKey.get(key)).find(Boolean);
        if (!entry) continue;
        const delta = entry.getBoundingClientRect().top - viewport.getBoundingClientRect().top - anchor.offsetTop;
        if (Math.abs(delta) > 0.5) {
          viewport.scrollTop += delta;
          lastScrollTop.current = viewport.scrollTop;
        }
        break;
      }
    }
    captureAnchor();
  }, [captureAnchor, scrollToLatest]);

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
    captureAnchor();
  }, [captureAnchor]);

  const jumpToLatest = useCallback(() => {
    following.current = true;
    anchorRef.current = [];
    setIsFollowing(true);
    scrollToLatest();
  }, [scrollToLatest]);

  useLayoutEffect(() => {
    jumpToLatest();
  }, [projectId, jumpToLatest]);

  useLayoutEffect(() => {
    restoreAnchor();
  }, [requests, events, restoreAnchor]);

  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;
    // Use one anchoring mechanism, not Chromium's adjustment on top of ours.
    const oldAnchor = viewport.style.overflowAnchor;
    viewport.style.overflowAnchor = "none";
    // Streaming, expanded tool output, fonts and composer resizing can change height.
    const observer = new ResizeObserver(restoreAnchor);
    observer.observe(viewport);
    observer.observe(content);
    return () => { observer.disconnect(); viewport.style.overflowAnchor = oldAnchor; };
  }, [restoreAnchor]);

  return { scrollRef, contentRef, isFollowing, onScroll, jumpToLatest };
}
