"use client";

// Client component purely so the default message can be translated — there
// is no server-only code here.

import { useT } from "../../lib/i18n";

export default function LoadingState({ message }: { message?: string }) {
  const t = useT();
  return (
    <div className="sran-empty">
      <div
        style={{
          width: "1.5rem",
          height: "1.5rem",
          border: "2px solid var(--color-border-default)",
          borderTopColor: "var(--color-sran-700)",
          borderRadius: "50%",
          animation: "spin 0.6s linear infinite",
          margin: "0 auto 0.75rem",
        }}
      />
      <p>{message ?? t("state.loading")}</p>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
