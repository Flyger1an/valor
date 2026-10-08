"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export function StudyRefresh() {
  const router = useRouter();
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [router]);
  return <p className="muted">Refreshes every 10 seconds while this tab is visible. Snapshot and provider timestamps stay unchanged.</p>;
}
