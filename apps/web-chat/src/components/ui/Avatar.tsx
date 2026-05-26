// Initial-based avatar — used in sidebar footer + message bubbles.
export function Avatar({
  name,
  size = 32,
  tone = "user",
}: {
  name: string;
  size?: number;
  tone?: "user" | "assistant";
}) {
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  const palette =
    tone === "assistant"
      ? "bg-gradient-to-br from-accent to-accent-subtle text-white"
      : "bg-gradient-to-br from-sky-700 to-indigo-800 text-white";
  return (
    <span
      style={{ width: size, height: size, fontSize: size * 0.4 }}
      className={`inline-flex items-center justify-center rounded-full font-semibold ${palette}`}
    >
      {initial}
    </span>
  );
}
