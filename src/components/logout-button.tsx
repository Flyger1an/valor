"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

export function LogoutButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function logout() {
    setBusy(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
      router.replace("/login");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className="logout-button"
      type="button"
      disabled={busy}
      onClick={() => void logout()}
    >
      <LogOut size={14} aria-hidden="true" />
      {busy ? "Leaving" : "Logout"}
    </button>
  );
}
