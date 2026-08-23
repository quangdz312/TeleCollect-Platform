/** @type {import('next').NextConfig} */
// Standalone demo: there is no backend, so there is nothing to proxy.  The real
// frontend rewrites /api/* to the FastAPI server here.
const nextConfig = {
  reactStrictMode: true,
  // Production Docker image copies only .next/standalone + .next/static +
  // public/ (see frontend/Dockerfile) instead of the full node_modules tree.
  output: "standalone",
};

export default nextConfig;
