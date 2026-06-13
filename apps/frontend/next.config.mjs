import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const API_URL = process.env.API_URL ?? "http://localhost:8080";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../../"),
  transpilePackages: ["@factory/shared-types"],
  experimental: {
    typedRoutes: false,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/:path*` },
      { source: "/sse/:path*", destination: `${API_URL}/:path*` },
      // Asset downloads proxied separately so clients can stream MP4s via the
      // same origin (no CORS, no separate :8080 exposure on LAN/tunnel).
      // We deliberately do NOT proxy `/jobs/*` or `/healthz` at the root,
      // because `/jobs/[id]` is an App Router page in this project.
      { source: "/assets/:path*", destination: `${API_URL}/assets/:path*` },
    ];
  },
};

export default nextConfig;
