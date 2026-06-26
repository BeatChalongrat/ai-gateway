"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

export type CodeTab = { label: string; code: string };

// Tabbed code block with copy-to-clipboard — used by the docs quickstart.
// Dark navy surface matches the existing docs <pre> treatment.
export default function CodeTabs({ tabs }: { tabs: CodeTab[] }) {
  const [active, setActive] = useState(0);
  const [copied, setCopied] = useState(false);

  const current = tabs[active] ?? tabs[0];

  async function copy() {
    try {
      await navigator.clipboard.writeText(current.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard may be unavailable (insecure context) — no-op */
    }
  }

  return (
    <div className="code-tabs">
      <div className="code-tabs-bar">
        <div className="code-tabs-list" role="tablist">
          {tabs.map((t, i) => (
            <button
              key={t.label}
              role="tab"
              aria-selected={i === active}
              className={`code-tab${i === active ? " is-active" : ""}`}
              onClick={() => setActive(i)}
              type="button"
            >
              {t.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="code-copy"
          onClick={copy}
          aria-label="Copy code"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="code-tabs-pre">
        <code>{current.code}</code>
      </pre>
    </div>
  );
}
