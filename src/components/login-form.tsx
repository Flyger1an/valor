"use client";

import { LockKeyhole, LogIn } from "lucide-react";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useState } from "react";

export function LoginForm(props: { configured: boolean }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const body = (await response.json().catch(() => null)) as {
        error?: string;
      } | null;

      if (!response.ok) {
        setError(body?.error ?? `Login failed with status ${response.status}.`);
        return;
      }

      router.replace("/");
      router.refresh();
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="login-form" onSubmit={submit}>
      <label>
        <span>Operator Password</span>
        <span className="login-input-wrap">
          <LockKeyhole size={15} aria-hidden="true" />
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            disabled={!props.configured || busy}
            autoFocus
          />
        </span>
      </label>
      {error ? <p className="login-error">{error}</p> : null}
      {!props.configured ? (
        <p className="login-error">
          Set VALOR_SESSION_SECRET and VALOR_ADMIN_PASSWORD_HASH before using
          browser login.
        </p>
      ) : null}
      <button type="submit" disabled={!props.configured || busy || !password.trim()}>
        <LogIn size={15} aria-hidden="true" />
        {busy ? "Checking" : "Enter Valor"}
      </button>
    </form>
  );
}
