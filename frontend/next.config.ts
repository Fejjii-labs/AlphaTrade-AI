import type { NextConfig } from "next";

import { PHASE_B_REDIRECTS } from "./src/lib/navigation/phase-b-redirects";
import { buildSecurityHeaders, resolveApiOrigin } from "./src/lib/security-headers";

const nextConfig: NextConfig = {
  output: "standalone",
  // Playwright runs `next dev`. Strict Mode remounts abort in-flight queries and
  // fetch them again, which the validation smoke counts as extra requests.
  // Production (`next start`) does not double-invoke those effects.
  reactStrictMode: process.env.E2E_DISABLE_REACT_STRICT_MODE !== "1",
  async redirects() {
    return PHASE_B_REDIRECTS.map((rule) => ({
      source: rule.source,
      destination: rule.destination,
      permanent: rule.permanent,
    }));
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: buildSecurityHeaders({
          apiOrigin: resolveApiOrigin(process.env.NEXT_PUBLIC_API_URL),
          dev: process.env.NODE_ENV === "development",
        }),
      },
    ];
  },
};

export default nextConfig;
