import type { Metadata } from "next";

import TermsContent from "./TermsContent";

export const metadata: Metadata = {
  title: "เงื่อนไขการใช้งาน — SRAN AI Gateway",
  description:
    "ข้อตกลงการใช้ระบบ SRAN AI Gateway สำหรับการติดตั้งแบบ self-hosted ในองค์กร",
};

export default function TermsPage() {
  return <TermsContent />;
}
