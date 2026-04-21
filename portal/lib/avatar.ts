// Resolve an avatar URL for <img src>.
//
// Backend returns relative paths like "/static/avatars/xxx.webp". Next.js
// rewrites "/static/*" to the backend (see next.config.js), so the
// browser can request the URL as-is from the same origin. OAuth-sourced
// avatars come back as absolute http(s) URLs and are returned unchanged.

export function resolveAvatarUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return url;
}
