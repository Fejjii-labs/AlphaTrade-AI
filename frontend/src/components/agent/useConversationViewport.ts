"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Follow the page's conversation tail, without taking an older reader's position. */
export function useConversationViewport(scope: string, latestMessage: string) {
  const composer = useRef<HTMLFormElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const previousY = useRef(0);
  const frame = useRef<number | null>(null);
  const [away, setAway] = useState(false);

  const returnToLatest = useCallback(() => {
    following.current = true;
    setAway(false);
    composer.current?.scrollIntoView?.({ block: "end", behavior: "instant" });
    previousY.current = window.scrollY;
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
      // A layout change alone is not an instruction to stop following. Moving
      // upward away from the tail is; reaching the composer resumes following.
      if (nearTail) following.current = true;
      else if (window.scrollY < previousY.current - 2) following.current = false;
      previousY.current = window.scrollY;
      setAway(!nearTail);
    };
    const readOlder = (event: WheelEvent) => {
      if (event.deltaY < 0) {
        following.current = false;
        if (frame.current !== null) cancelAnimationFrame(frame.current);
      }
    };
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    window.addEventListener("wheel", readOlder, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (following.current && latestMessage) {
        if (frame.current !== null) cancelAnimationFrame(frame.current);
        frame.current = requestAnimationFrame(() => {
          if (following.current) returnToLatest();
        });
      }
    });
    if (thread.current) observer?.observe(thread.current);
    if (composer.current) observer?.observe(composer.current);
    return () => {
      observer?.disconnect();
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      window.removeEventListener("wheel", readOlder);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [latestMessage, returnToLatest]);

  return { composer, thread, away, returnToLatest };
}
