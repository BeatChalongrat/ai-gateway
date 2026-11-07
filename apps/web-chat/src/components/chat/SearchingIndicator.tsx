"use client";

// Loading indicator shown while the backend is searching the web (the
// `search_status: "searching"` SSE event). Sits in place of the waiting
// indicator; auto-hides once the answer streams in. Renders the shared
// breathing-cat status mark so waiting and searching read as one visual
// language — only the label changes.

import { CatIndicator } from "./CatIndicator";

export function SearchingIndicator() {
  return <CatIndicator labelKey="gen.searching" />;
}
