// Single place to retarget the landing page's auth CTAs if sign-in
// ever splits into multiple flows. Both currently land on /login,
// which owns the Google flow — the landing page never starts OAuth
// itself.
export const SIGN_IN_HREF = "/login";
export const GET_STARTED_HREF = "/login";
