/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // ``.next/server/app/chat`` got stuck with a Windows file handle
  // mid-rebuild (likely the indexer or AV scanner) and refused to
  // release. Writing the build into a sibling dir sidesteps the
  // lock entirely and means the running server reads from a known-
  // fresh tree. Delete .next-fresh manually when the lock clears.
  distDir: ".next-fresh",
  // The chat-edge-api lives on a separate origin (port 4000); we send
  // CORS-allowed requests directly from the browser instead of proxying.
  // env.NEXT_PUBLIC_API_URL drives the API base in lib/api.ts.
};

module.exports = nextConfig;
