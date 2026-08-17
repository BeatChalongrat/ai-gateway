"use client";

import { ArrowRight } from "lucide-react";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "สิ่งที่เราช่วยองค์กรทำ",
    heading: "นำ AI มาใช้ โดยไม่เปิดเส้นทางข้อมูลออกนอกบริษัท",
    sub: "ทุกความสามารถด้านล่างมีอยู่จริงในระบบวันนี้ และบังคับใช้บนเซิร์ฟเวอร์ที่ทีมของคุณเป็นเจ้าของ",
    cards: [
      {
        h: "ใช้ AI อย่างปลอดภัย",
        p: "ข้อมูลอ่อนไหวถูกตรวจจับและแทนด้วยค่าแทนกลางก่อนคำขอไปถึงผู้ให้บริการภายนอก แล้วคืนค่าในคำตอบ โมเดลไม่เคยได้รับข้อมูลจริง",
        link: "ดูวิธีการทำงาน",
        href: "/#how",
      },
      {
        h: "กำกับดูแลข้อมูลตาม PDPA",
        p: "นโยบายชุดเดียวมีผลกับทุกคำขอ AI ในองค์กร ด้วยตัวตรวจจับที่เข้าใจข้อมูลระบุตัวตนแบบไทยและหมวดข้อมูลอ่อนไหวตามมาตรา 26",
        link: "ดูรายการตัวตรวจจับ",
        href: "/#security",
      },
      {
        h: "รวมทุกโมเดลไว้หลัง gateway เดียว",
        p: "ผู้ให้บริการโมเดล 15 รายอยู่หลัง endpoint แบบ OpenAI-compatible เดียว เลือกโมเดลเอง หรือให้ระบบเลือกตามค่าใช้จ่ายและเวลาตอบ พร้อมการสำรองสองชั้น",
        link: "ดูรายชื่อผู้ให้บริการ",
        href: "/#ecosystem",
      },
      {
        h: "เครื่องมือ AI สำหรับทีม ไม่มีเส้นทางที่คุมไม่ได้",
        p: "เว็บแชตพร้อมคลังความรู้ภายใน เอเจนต์เดสก์ท็อปบน Windows ที่มีด่านขออนุมัติ และการรันโค้ดในแซนด์บ็อกซ์ — ทั้งหมดผ่านกระบวนการปิดบังข้อมูลชุดเดียวกัน",
        link: "ดูผลิตภัณฑ์",
        href: "/#products",
      },
    ],
  },
  en: {
    kicker: "What we help organisations do",
    heading: "AI adoption without new data paths out of the company",
    sub: "Every capability below exists in the system today and is enforced on servers your team owns.",
    cards: [
      {
        h: "Secure enterprise AI",
        p: "Sensitive values are detected and replaced with neutral placeholders before any request reaches an external provider, then restored in the reply. The model never receives the original.",
        link: "How it works",
        href: "/#how",
      },
      {
        h: "PDPA & data governance",
        p: "One policy applies to every AI request in the organisation, with detectors that understand Thai identifiers and the sensitive categories in PDPA section 26.",
        link: "View detectors",
        href: "/#security",
      },
      {
        h: "One governed gateway for every model",
        p: "Fifteen model providers behind one OpenAI-compatible endpoint. Choose a model explicitly, or let the router select by cost and latency with two layers of fallback.",
        link: "View providers",
        href: "/#ecosystem",
      },
      {
        h: "AI tools for teams, no uncontrolled paths",
        p: "Web chat with an internal knowledge base, a Windows desktop agent with approval gates, and sandboxed code execution — all through the same masking pipeline.",
        link: "View products",
        href: "/#products",
      },
    ],
  },
} as const;

export function Solutions() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section" id="solutions">
      <div className="wrap">
        <div className="section-head">
          <span className="kicker">{t.kicker}</span>
          <h2 className="section-h">{t.heading}</h2>
          <p className="section-sub">{t.sub}</p>
        </div>
        <div className="solutions-grid">
          {t.cards.map((c) => (
            <div className="sol" key={c.href + c.h}>
              <h3>{c.h}</h3>
              <p>{c.p}</p>
              <a className="link-arrow" href={c.href}>
                {c.link} <ArrowRight size={17} aria-hidden="true" />
              </a>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
