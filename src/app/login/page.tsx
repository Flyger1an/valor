import { redirect } from "next/navigation";
import { LoginForm } from "@/components/login-form";
import { getBrowserSession } from "@/lib/auth/page-session";

export const dynamic = "force-dynamic";

export default async function LoginPage() {
  const session = await getBrowserSession();
  if (session) redirect("/");

  const configured = Boolean(
    process.env.VALOR_SESSION_SECRET?.trim() &&
      process.env.VALOR_ADMIN_PASSWORD_HASH?.trim(),
  );

  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <p className="eyebrow">Private operator access</p>
        <h1 id="login-title">Valor</h1>
        <p className="login-copy">
          Browser access uses a signed HttpOnly session cookie. Automation should
          keep using the ops secret header.
        </p>
        <LoginForm configured={configured} />
      </section>
    </main>
  );
}
