"use client";

// Client component purely so the retry label can be translated — there is
// no server-only code here.

import { useT } from "../../lib/i18n";

interface Props {
  message: string;
  onRetry?: () => void;
}

export default function ErrorState({ message, onRetry }: Props) {
  const t = useT();
  return (
    <div className="sran-error-state">
      <div className="sran-error-state-icon">!</div>
      <p className="sran-error-state-message">{message}</p>
      {onRetry && (
        <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onRetry}>
          {t("action.retry")}
        </button>
      )}
    </div>
  );
}
