/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8080/:path*",
      },
      {
        source: "/static/:path*",
        destination: "http://localhost:8080/static/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
