/** @type {import('next').NextConfig} */
// Standalone demo: there is no backend, so there is nothing to proxy.  The real
// frontend rewrites /api/* to the FastAPI server here.
const nextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
