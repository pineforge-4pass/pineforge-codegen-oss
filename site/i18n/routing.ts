import { defineRouting } from "next-intl/routing";
import localeList from "./locales.json";

// The locales live in one place: i18n/locales.json. Adding one there (and its
// messages/<locale>.json catalog) adds a /<locale>/ tree to the static export.
export const routing = defineRouting({
  locales: localeList.locales,
  defaultLocale: localeList.defaultLocale,
  localePrefix: "always",
});
