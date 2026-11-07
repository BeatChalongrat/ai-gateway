"use client";

import { useT } from "../../lib/i18n";

interface Props {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export default function SearchFilter({ value, onChange, placeholder }: Props) {
  const t = useT();
  return (
    <div className="sran-search-bar">
      <span className="sran-search-icon" aria-hidden>⌕</span>
      <input
        className="sran-search-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? t("ui.search.placeholder")}
        aria-label={placeholder ?? t("action.search")}
      />
      {value && (
        <button
          className="sran-search-clear"
          type="button"
          aria-label={t("action.clear")}
          title={t("action.clear")}
          onClick={() => onChange("")}
        >
          ✕
        </button>
      )}
    </div>
  );
}
