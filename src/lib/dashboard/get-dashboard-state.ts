import { cache } from "react";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";

export const getDashboardState = cache(buildDashboardState);

export type DashboardState = Awaited<ReturnType<typeof getDashboardState>>;