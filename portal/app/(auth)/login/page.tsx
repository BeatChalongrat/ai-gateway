import type { Metadata } from "next";
import { Suspense } from "react";

import "./login.css";
import AuthSplitLayout from "./_components/AuthSplitLayout";
import LoginForm from "./_components/LoginForm";
import VisualPanel, { type Slide } from "./_components/VisualPanel";

// FIX132 F15 — in Thai, like the form (the portal has no i18n layer).
export const metadata: Metadata = {
  title: "เข้าสู่ระบบ — SRAN",
  description: "เข้าสู่ระบบ SRAN",
};

// Every figure here must be verifiable against the running system —
// no throughput, latency, uptime, or certification claims.
const loginSlides: Slide[] = [
  {
    headlineTop: "ปกป้องข้อมูล",
    headlineBottom: "ตั้งแต่ต้นทาง",
    subtext:
      "ตัวตรวจจับกว่า 30 แบบ รวมการจดจำชื่อเฉพาะภาษาไทย ปิดบังข้อมูลอ่อนไหวก่อนคำขอออกจากระบบ แล้วคืนค่าในคำตอบ",
    stats: [
      { value: "30+", label: "ตัวตรวจจับ" },
      { value: "PDPA", label: "ตรวจจับตามกฎหมาย" },
      { value: "AES-256", label: "เข้ารหัสข้อมูลที่เก็บ" },
    ],
  },
  {
    headlineTop: "ระบบ AI ขององค์กร",
    headlineBottom: "ทำงานราบรื่น",
    subtext:
      "ผู้ให้บริการโมเดล 15 ราย ผ่าน endpoint เดียวที่ใช้แบบเดียวกับ OpenAI — ติดตั้งบนระบบที่ทีมของคุณดูแลเอง",
    stats: [
      { value: "15", label: "ผู้ให้บริการ" },
      { value: "OpenAI", label: "API แบบเดียวกัน" },
      { value: "On-prem", label: "ติดตั้งเอง" },
    ],
  },
];

export default function LoginPage() {
  // ``useSearchParams`` inside LoginForm requires a Suspense
  // boundary so Next.js can prerender the static shell without
  // bailing on the whole route. The fallback re-uses the form
  // markup with the action disabled — visually indistinguishable
  // from the live form for the brief paint before search params
  // resolve.
  return (
    <AuthSplitLayout
      visual={<VisualPanel slides={loginSlides} />}
      form={
        <Suspense fallback={<LoginFormSkeleton />}>
          <LoginForm />
        </Suspense>
      }
    />
  );
}

function LoginFormSkeleton() {
  return (
    <div className="auth-form-card" aria-hidden="true" lang="th">
      <div className="af-eyebrow">เข้าสู่ระบบ</div>
      <h1 className="af-title">ยินดีต้อนรับกลับมา</h1>
      <p className="af-desc">
        กำลังโหลด…
      </p>
      <div className="af-form">
        <div className="af-field">
          <div className="af-skeleton af-skeleton-label skeleton" />
          <div className="af-skeleton af-skeleton-input skeleton" />
        </div>
        <div className="af-field">
          <div className="af-skeleton af-skeleton-label skeleton" />
          <div className="af-skeleton af-skeleton-input skeleton" />
        </div>
        <div className="af-skeleton af-skeleton-button skeleton" />
      </div>
    </div>
  );
}
