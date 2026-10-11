"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Follow the page's conversation tail, without taking an older reader's position. */
export function useConversationViewport(scope: string, latestMessage: string) {
  const composer = useRef<HTMLFormElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const previousY = useRef(0);
  const composerPosition = useRef(0);
  const touchY = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const [away, setAway] = useState(false);

  const returnToLatest = useCallback(() => {
    following.current = true;
    setAway(false);
    composer.current?.scrollIntoView?.({ block: "end", behavior: "instant" });
    previousY.current = window.scrollY;
    composerPosition.current = (composer.current?.getBoundingClientRect().bottom ?? 0) + window.scrollY;
  }, []);

  useLayoutEffect(() => {
    following.current = true;
    setAway(false);
    previousY.current = window.scrollY;
  }, [scope]);

  useLayoutEffect(() => {
    if (!latestMessage || !following.current) return;
    frame.current = requestAnimationFrame(() => {
      if (following.current) returnToLatest();
    });
    return () => { if (frame.current !== null) cancelAnimationFrame(frame.current); };
  }, [scope, latestMessage, returnToLatest]);

  useEffect(() => {
    const update = () => {
      const bottom = composer.current?.getBoundingClientRect().bottom;
      if (bottom === undefined) return;
      const nearTail = bottom <= window.innerHeight + 80 && bottom >= 100;
      const position = bottom + window.scrollY;
      const layoutChanged = Math.abs(position - composerPosition.current) > 2;
      // A layout change alone is not an instruction to stop following. Moving
      // upward away from the tail is; reaching the composer resumes following.
      if (nearTail) following.current = true;
      else if (!layoutChanged && window.scrollY < previousY.current - 2) following.current = false;
      composerPosition.current = position;
      previousY.current = window.scrollY;
      setAway(!nearTail);
    };
    const stopFollowing = () => {
      following.current = false;
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
    const readOlder = (event: WheelEvent) => { if (event.deltaY < 0) stopFollowing(); };
    const touchStart = (event: TouchEvent) => { touchY.current = event.touches[0]?.clientY ?? null; };
    const touchMove = (event: TouchEvent) => {
      const next = event.touches[0]?.clientY;
      if (next !== undefined && touchY.current !== null && next > touchY.current + 2) stopFollowing();
      touchY.current = next ?? null;
    };
    const keyOlder = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement && event.target.closest("input, textarea, [contenteditable=true]")) return;
      if (["ArrowUp", "PageUp", "Home"].includes(event.key)) stopFollowing();
    };
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    window.addEventListener("wheel", readOlder, { passive: true });
    window.addEventListener("touchstart", touchStart, { passive: true });
    window.addEventListener("touchmove", touchMove, { passive: true });
    window.addEventListener("keydown", keyOlder);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (following.current && latestMessage) {
        if (frame.current !== null) cancelAnimationFrame(frame.current);
        frame.current = requestAnimationFrame(() => {
          if (following.current) returnToLatest();
        });
      }
    });
    // Status and recovery blocks sit beside the thread and can move the composer
    // without resizing either node. Observe their common content container too.
    if (thread.current) observer?.observe(thread.current.parentElement ?? thread.current);
    if (composer.current) observer?.observe(composer.current);
    return () => {
      observer?.disconnect();
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      window.removeEventListener("wheel", readOlder);
      window.removeEventListener("touchstart", touchStart);
      window.removeEventListener("touchmove", touchMove);
      window.removeEventListener("keydown", keyOlder);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [latestMessage, returnToLatest]);

  return { composer, thread, away, returnToLatest };
}
