/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
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
