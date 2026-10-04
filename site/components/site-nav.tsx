"use client";

import { usePathname } from "next/navigation";

export interface NavItem {
  href: string;
  label: string;
}

/** The main navigation; marks the current page for assistive tech. */
export function SiteNav({ items, label }: { items: NavItem[]; label: string }) {
  const pathname = usePathname() ?? "";
  const here = pathname.endsWith("/") ? pathname : `${pathname}/`;
  return (
    <nav aria-label={label}>
      <ul className="flex flex-wrap gap-x-5 gap-y-0">
        {items.map((item) => {
          const current = here === item.href;
          return (
            <li key={item.href}>
              <a
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={
                  "inline-flex min-h-11 items-center text-[15px] font-medium underline-offset-[0.3em] hover:underline " +
                  (current ? "underline decoration-2" : "no-underline")
                }
              >
                {item.label}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
