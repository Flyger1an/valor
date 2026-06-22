import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Valor | Private Crypto RV + Risk Intel",
  description:
    "Local-first crypto relative-value research, risk monitoring, backtesting, and paper trading dashboard.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
