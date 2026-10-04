import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// Static export: Cloudflare Pages serves `out/`; the API and the certificate
// are Pages Functions in `functions/` (see README.md).
const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  // site/ is its own project inside the repository (which has its own
  // package-lock.json); npm scripts always run from site/.
  turbopack: { root: process.cwd() },
};

export default withNextIntl(nextConfig);
