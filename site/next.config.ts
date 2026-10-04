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
};

export default withNextIntl(nextConfig);
