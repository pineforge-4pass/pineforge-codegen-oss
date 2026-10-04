import type { ReactNode } from "react";

// The real root layout is app/[locale]/layout.tsx (it renders <html lang>).
// This pass-through exists because app/not-found.tsx needs a layout.
export default function RootLayout({ children }: { children: ReactNode }) {
  return children;
}
