// NextAuth handler for Google + GitHub "Continue with..." buttons.
//
// Next.js 14 App Router only permits HTTP-method named exports from a
// route file. ``authOptions`` lives in ``./authOptions`` and is
// re-exported from that module when other server code needs the type
// or the options object.
//
// This file is SAFE to ship without OAuth credentials configured. When
// the required env vars aren't set the corresponding provider is
// simply omitted from the providers list — NextAuth's ``signIn`` will
// then fail fast with "provider not found", which the client checks
// against ``NEXT_PUBLIC_OAUTH_ENABLED`` before even offering the
// button.

import NextAuth from "next-auth";

import { authOptions } from "./authOptions";

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
