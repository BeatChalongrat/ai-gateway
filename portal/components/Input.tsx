"use client";

import { InputHTMLAttributes, forwardRef, useState } from "react";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  showToggle?: boolean; // for password inputs
}

export const Input = forwardRef<HTMLInputElement, Props>(function Input(
  { label, error, showToggle, type = "text", className = "", style, id, ...rest },
  ref
) {
  const [reveal, setReveal] = useState(false);
  const realType = showToggle && type === "password" ? (reveal ? "text" : "password") : type;
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, "-");

  return (
    <div className="w-full">
      {label ? (
        <label
          htmlFor={inputId}
          className="block mb-1.5"
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: "var(--text-tertiary)",
          }}
        >
          {label}
        </label>
      ) : null}
      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          type={realType}
          className={[
            "w-full h-10 px-3 rounded-lg text-sm",
            "focus:outline-none",
            showToggle && type === "password" ? "pr-16" : "",
            className,
          ].join(" ")}
          style={{
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
            border: `1px solid ${error ? "var(--accent-danger)" : "var(--border-default)"}`,
            ...style,
          }}
          {...rest}
        />
        {showToggle && type === "password" ? (
          <button
            type="button"
            onClick={() => setReveal((v) => !v)}
            tabIndex={-1}
            className="absolute right-2 top-1/2 -translate-y-1/2 px-1"
            style={{
              fontSize: 12,
              fontWeight: 500,
              color: "var(--accent-primary)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
            }}
          >
            {reveal ? "Hide" : "Show"}
          </button>
        ) : null}
      </div>
      {error ? (
        <p style={{ fontSize: 12, color: "var(--accent-danger)", marginTop: 4 }}>
          {error}
        </p>
      ) : null}
    </div>
  );
});
