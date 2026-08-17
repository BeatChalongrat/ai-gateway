"use client";

import Image from "next/image";
import Link from "next/link";
import { Check } from "lucide-react";

import { SIGN_IN_HREF } from "./auth-links";
import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "Enterprise AI · Data Protection · PDPA",
    h1: "ให้ทุกทีมใช้ AI ได้ โดยข้อมูลยังอยู่ในองค์กร",
    body: "SRAN AI Gateway อยู่ระหว่างทีมของคุณกับโมเดล AI ภายนอก ปิดบังข้อมูลอ่อนไหวก่อนคำขอออกไป และคืนค่าเมื่อคำตอบกลับมา บนระบบที่คุณดูแลเอง",
    contact: "ติดต่อทีมงาน",
    signIn: "เข้าสู่ระบบ",
    trustSelfHosted: "ติดตั้งในองค์กรของคุณเอง",
    trustApi: "API แบบ OpenAI-compatible",
    shotAlt: "ตัวอย่างหน้าจอ SRAN Web Chat แสดงการปิดบังข้อมูลก่อนส่งถึงโมเดล",
  },
  en: {
    kicker: "Enterprise AI · Data protection · PDPA",
    h1: "Use AI across the organisation. Keep the data inside it.",
    body: "SRAN AI Gateway sits between your teams and external AI models — concealing sensitive data before a request leaves, and restoring it when the answer returns. On infrastructure you run yourself.",
    contact: "Contact us",
    signIn: "Sign in",
    trustSelfHosted: "Self-hosted on your infrastructure",
    trustApi: "OpenAI-compatible API",
    shotAlt: "SRAN Web Chat screen showing data masked before it reaches the model",
  },
} as const;

export function Hero() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="hero" id="top">
      <div className="wrap hero-grid">
        <div>
          <span className="kicker">{t.kicker}</span>
          <h1>{t.h1}</h1>
          <p className="hero-copy">{t.body}</p>
          <div className="hero-actions">
            <a className="btn btn-light" href="/#contact">
              {t.contact}
            </a>
            <Link className="btn btn-ghost-light" href={SIGN_IN_HREF}>
              {t.signIn}
            </Link>
          </div>
          <div className="hero-trust">
            <span>
              <Check size={15} aria-hidden="true" /> {t.trustSelfHosted}
            </span>
            <span>
              <Check size={15} aria-hidden="true" /> {t.trustApi}
            </span>
          </div>
        </div>

        <div className="shot">
          <Image
            src="/screenshots/webchat-hero.jpg"
            alt={t.shotAlt}
            width={1440}
            height={844}
            priority
            sizes="(max-width: 1040px) 100vw, 600px"
          />
        </div>
      </div>
    </section>
  );
}
