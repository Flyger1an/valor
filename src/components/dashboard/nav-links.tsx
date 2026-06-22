"use client";

import {
  BellRing,
  Bot,
  CircleDollarSign,
  FileClock,
  Gauge,
  LineChart,
  Radar,
  ShieldAlert,
  SlidersHorizontal,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/signals", label: "Signals", icon: Radar },
  { href: "/risk", label: "Risk Intel", icon: ShieldAlert },
  { href: "/alerts", label: "Alerts", icon: BellRing },
  { href: "/analyst", label: "Analyst", icon: Bot },
  { href: "/backtests", label: "Backtests", icon: LineChart },
  { href: "/paper", label: "Paper Trading", icon: CircleDollarSign },
  { href: "/settings", label: "Settings", icon: SlidersHorizontal },
  { href: "/audit", label: "Audit", icon: FileClock },
] as const;

export function DashboardNavLinks() {
  const pathname = usePathname();

  return (
    <nav className="side-nav" aria-label="Dashboard sections">
      {LINKS.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link key={href} href={href} className={active ? "active" : undefined}>
            <Icon size={16} aria-hidden="true" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}