import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Grok Trade Dashboard",
  description: "Trading bot monitoring dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-900 text-white">
        {children}
      </body>
    </html>
  );
}
