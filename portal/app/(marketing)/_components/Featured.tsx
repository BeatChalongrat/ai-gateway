"use client";

import Image from "next/image";
import { Check } from "lucide-react";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "แพลตฟอร์ม · คลังความรู้",
    heading: "ให้ AI ตอบจากเอกสารขององค์กรเอง",
    lead: "สร้างคลังความรู้จากเอกสารภายใน แล้ว Web Chat จะดึงมาอ้างอิงในคำตอบ โดยยังผ่านกระบวนการปิดบังข้อมูลชุดเดียวกัน",
    facts: [
      { b: "อ้างอิงเอกสารของคุณ", rest: " — อัปโหลดคลังความรู้ คำตอบอ้างอิงต้นทางให้" },
      { b: "ทำมาเพื่อภาษาไทย", rest: " — ทั้งหน้าจอและการตรวจจับออกแบบเพื่อข้อความไทยโดยตรง" },
      { b: "เสียง ภาพ และวิดีโอ", rest: " — การสร้างสื่ออยู่ใต้นโยบายเดียวกัน" },
    ],
    shotAlt: "ตัวอย่างหน้าจอ SRAN Web Chat: หน้าคลังความรู้สำหรับอัปโหลดเอกสารให้ AI ใช้อ้างอิง",
  },
  en: {
    kicker: "Platform · Knowledge base",
    heading: "Answers grounded in your own documents",
    lead: "Create a knowledge base from internal documents and Web Chat retrieves from it when answering — still passing through the same masking pipeline.",
    facts: [
      { b: "Grounded in your documents", rest: " — upload a knowledge base; answers cite the source." },
      { b: "Built for Thai", rest: " — interface and detection designed for Thai text, not adapted." },
      { b: "Voice, image and video", rest: " — media generation under the same policy." },
    ],
    shotAlt: "SRAN Web Chat: the knowledge-base page for uploading documents the AI can cite",
  },
} as const;

export function Featured() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section" id="products">
      <div className="wrap">
        <div className="featured-grid">
          <div className="reveal">
            <span className="kicker">{t.kicker}</span>
            <h2 className="section-h">{t.heading}</h2>
            <p className="featured-lead">{t.lead}</p>
            <ul className="featured-facts">
              {t.facts.map((f) => (
                <li key={f.b}>
                  <Check size={18} aria-hidden="true" />
                  <span>
                    <b>{f.b}</b>
                    {f.rest}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="shot reveal">
            <Image
              src="/screenshots/webchat-knowledge.jpg"
              alt={t.shotAlt}
              width={1360}
              height={778}
              sizes="(max-width: 1040px) 100vw, 640px"
            />
          </div>
        </div>
      </div>
    </section>
  );
}
