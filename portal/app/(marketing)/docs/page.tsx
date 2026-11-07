import type { Metadata } from "next";

import DocsContent from "./DocsContent";

export const metadata: Metadata = {
  title: "คู่มือเริ่มต้น — SRAN AI Gateway",
  description:
    "เรียกใช้ SRAN AI Gateway ผ่าน API แบบ OpenAI-compatible — เปลี่ยนแค่ base URL แล้วโค้ดเดิมใช้งานได้ทันที",
};

export default function DocsPage() {
  return <DocsContent />;
}
