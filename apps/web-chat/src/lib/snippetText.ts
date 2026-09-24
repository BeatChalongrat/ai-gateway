// FIX132 F20 — a search snippet is one line of plain text. Message bodies
// are markdown; NIGHT3 j2-06 showed "**ศาลเจ้าฟูชิมิอินาริ** — …" in the
// sidebar. This drops the markup that reads as noise in a one-line preview
// (emphasis, inline code, fences, headings, list / quote markers, table
// pipes, link and image syntax — their text is kept) and leaves ordinary
// punctuation alone ("5*3", "file_name_v2").

export function snippetText(markdown: string, max = 140): string {
  let s = markdown;
  s = s.replace(/```[^\n]*\n?/g, " "); // code fences (the code itself stays)
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1"); // images → alt text
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1"); // links → their text
  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, ""); // headings
  s = s.replace(/^\s{0,3}>\s?/gm, ""); // blockquotes
  s = s.replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, ""); // list markers
  s = s.replace(/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$/gm, " "); // table rule rows
  s = s.replace(/\|/g, " "); // table cell pipes
  s = s.replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, "$2"); // **bold** / __bold__
  s = s.replace(/(^|[^\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])/g, "$1$2"); // *italic*
  s = s.replace(/(^|[^\w])_(?=\S)([^_\n]*?\S)_(?!\w)/g, "$1$2"); // _italic_
  s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "$1"); // ~~strike~~
  s = s.replace(/`([^`\n]+)`/g, "$1"); // `inline code`
  s = s.replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}
