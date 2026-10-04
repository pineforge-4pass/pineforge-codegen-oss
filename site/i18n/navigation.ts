import { createNavigation } from "next-intl/navigation";
import { routing } from "./routing";

// Locale-aware navigation helpers. The static pages mostly use plain anchors
// built by components/paths.ts (full page loads suit a static export).
export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
