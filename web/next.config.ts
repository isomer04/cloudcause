import type { NextConfig } from "next";

/**
 * The frontend never talks to an agent framework, a provider SDK, or a database.
 * Everything it renders arrives from the CloudCause gateway contract, proxied
 * server-side through /gw so the browser needs no CORS grant and the gateway
 * needs no public origin allowlist.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  distDir: process.env.CLOUDCAUSE_WEB_DIST_DIR ?? ".next",
  output: "standalone",
  outputFileTracingRoot: __dirname,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
