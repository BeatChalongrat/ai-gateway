/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // FIX134 C4 — no sharp in the standalone image (same build as the portal,
  // where the optimiser logged an error per <Image>); nothing here needs it.
  images: { unoptimized: true },
  async redirects() {
    return [
      // /audit used to be a near-duplicate of /requests (gateway
      // request log); the two pages were consolidated. Anyone with
      // a stale bookmark lands on /requests now. The admin-action
      // log lives at /audit-log.
      { source: "/audit", destination: "/requests", permanent: true },
    ];
  },
};

module.exports = nextConfig;
