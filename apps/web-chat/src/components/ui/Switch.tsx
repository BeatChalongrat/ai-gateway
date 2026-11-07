"use client";

// FIX43 Part A — the one switch.
//
// Four hand-rolled toggles lived in the app. Two of them (settings, skills)
// positioned the knob with `absolute` and NO horizontal inset, so the knob
// sat at its static position — the centre of the track, because a <button>
// is `text-align: center` by UA default and Tailwind's preflight leaves that
// alone. Off looked like "almost on"; on put the knob 20 px outside the
// track. aria-checked was right the whole time, which is how it survived.
//
// This component does not depend on static position at all: the track is a
// flex row, the knob is a flex item that starts at the left edge, and the
// state only changes a transform. There is nothing the browser can fall
// back to, so there is nothing to get wrong. The knob is opaque white with
// a shadow so it reads on the light theme's pale "off" track too.

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Accessible name. Required when no visible label is associated. */
  label?: string;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
}

/** Track 44×24, knob 20 with a 2 px inset: off → x=2, on → x=22, so the knob's right edge lands at 42, inside the 44 track. */
export const SWITCH_KNOB_OFF_PX = 2;
export const SWITCH_KNOB_ON_PX = 22;

export function Switch({ checked, onChange, label, disabled, className = "", ...rest }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      data-testid={rest["data-testid"]}
      className={`focus-ring inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? "bg-status-success-solid" : "bg-border-strong"
      } ${className}`}
    >
      <span
        aria-hidden
        data-testid="switch-knob"
        className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
          checked ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
