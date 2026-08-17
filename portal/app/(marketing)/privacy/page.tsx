import type { Metadata } from "next";

import PrivacyContent from "./PrivacyContent";

export const metadata: Metadata = {
  title: "นโยบายความเป็นส่วนตัว — SRAN AI Gateway",
  description:
    "ระบบ SRAN AI Gateway ประมวลผลข้อมูลอะไรและอย่างไร — เขียนจากพฤติกรรมจริงของระบบแบบ self-hosted",
};

export default function PrivacyPage() {
  return <PrivacyContent />;
}
