import localFont from "next/font/local";

// Outfit is VENDORED, not fetched from Google at build time — as web-chat
// did (apps/web-chat/src/app/layout.tsx says why at length). Until 14 Oct
// 2026 the layout and seven pages each called next/font/google, and `next
// build` fetched the woff2 from fonts.gstatic.com inside the image build; it
// failed two builds in three on the day the admin image was last built. The
// file here is the same single variable woff2 Google served for every weight
// the pages asked (400–800), latin subset, so rendering is unchanged; the
// Dockerfile's build step now runs with no network, so a fetch could not
// pass by luck. Licence: SIL OFL 1.1, the notice travels with the font
// (fonts/OFL-Outfit.txt).
export const outfit = localFont({
  src: [{ path: "./fonts/Outfit-latin-variable.woff2", weight: "100 900", style: "normal" }],
  variable: "--font-outfit",
  display: "swap",
});
