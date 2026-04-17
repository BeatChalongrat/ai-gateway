"use client";

interface Props {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export default function SearchFilter({ value, onChange, placeholder = "Search..." }: Props) {
  return (
    <div className="sran-search-bar">
      <span className="sran-search-icon">⌕</span>
      <input
        className="sran-search-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      {value && (
        <button className="sran-search-clear" onClick={() => onChange("")}>
          ✕
        </button>
      )}
    </div>
  );
}
