/** "/en/" + path, e.g. localePath("en", "plans/") === "/en/plans/". */
export function localePath(locale: string, path = ""): string {
  return `/${locale}/${path.replace(/^\/+/, "")}`;
}

export const REPO_URL = "https://github.com/pineforge-4pass/pineforge-codegen-oss";
export const CONTACT_EMAIL = "enterprise@pineforge.dev";
export const KEYS_PATH = "/.well-known/pineforge-license-keys.json";
