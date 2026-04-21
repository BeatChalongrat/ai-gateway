// (auth) is a thin pass-through. Pages inside own their layout — login
// uses a full-height split, signup uses a centered card. Keeping the
// wrapper minimal lets each page reach the viewport edges when it
// wants to.
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
