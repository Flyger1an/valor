import { DashboardShell } from "@/components/dashboard/shell";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const state = await getDashboardState();

  return (
    <DashboardShell
      state={state}
      title="Crypto Relative-Value + Risk Intelligence"
      subtitle="Local-first research cockpit"
    >
      {children}
    </DashboardShell>
  );
}