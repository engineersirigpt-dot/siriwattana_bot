"use client";

// Keep a chat scrolled to the newest message only while the reader is already
// at the bottom. Scrolling up to read stops the auto-follow (a streaming answer
// no longer yanks the page down every token); `atBottom` drives a "ไปข้อความ
// ล่าสุด" button. Follow-scrolls are instant — smooth scrolling on every token
// was what made the old page shake.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

const NEAR = 80; // px from the bottom that still counts as "at the bottom"

export function useStickyScroll(ref: RefObject<HTMLElement | null>, trigger: unknown) {
  const stick = useRef(true);
  const pinned = useRef<number | null>(null); // scrollTop where the last follow left it
  const [atBottom, setAtBottom] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR;
      // our own smooth scroll still on its way down (see scrollToBottom)
      if (!near && stick.current && pinned.current === null) return;
      stick.current = near;
      if (near) pinned.current = el.scrollTop;
      setAtBottom(near);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [ref]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !stick.current) return;
    // The reader scrolled up but the scroll event (next frame) hasn't arrived
    // yet — a token render can land first. Trust the position, not the flag.
    const max = el.scrollHeight - el.clientHeight;
    if (pinned.current !== null && el.scrollTop < Math.min(pinned.current, max) - NEAR) {
      stick.current = false;
      setAtBottom(false);
      return;
    }
    el.scrollTop = el.scrollHeight;
    pinned.current = el.scrollTop;
  }, [ref, trigger]);

  const scrollToBottom = useCallback(
    (smooth = true) => {
      const el = ref.current;
      stick.current = true;
      setAtBottom(true);
      if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
      // smooth: unknown until it lands — the next render or arrival pins it
      pinned.current = smooth ? null : el.scrollTop;
      if (smooth)
        setTimeout(() => {
          if (pinned.current !== null) return; // the reader cut it short with no render since
          const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR;
          pinned.current = el.scrollTop;
          stick.current = near;
          setAtBottom(near);
        }, 1000);
    },
    [ref],
  );

  return { atBottom, scrollToBottom };
}
