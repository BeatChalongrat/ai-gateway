"use client";

// Client component purely so the default message can be translated — there
// is no server-only code here.

import { useT } from "../../lib/i18n";

export default function EmptyState({
  message,
  icon = "○",
  action,
}: {
  message?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const t = useT();
  return (
    <div className="sran-empty">
      <div className="sran-empty-icon">{icon}</div>
      <p style={{ marginBottom: action ? "1rem" : 0 }}>{message ?? t("state.empty")}</p>
      {action}
    </div>
  );
}
