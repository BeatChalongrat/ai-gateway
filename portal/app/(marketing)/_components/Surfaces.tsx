"use client";

import { Check, ShieldCheck } from "lucide-react";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    agentAria: "ตัวอย่างหน้าจอ Desktop Agent",
    task1: "เปิดรายงานยอดขายไตรมาส 3",
    task2: "ดึงตัวเลขออกมาเป็นตาราง",
    task3: "กำลังจัดการไฟล์ชั่วคราว",
    approveHead: "ต้องขออนุมัติก่อน",
    approveBody: "ลบไฟล์ชั่วคราว 12 ไฟล์ในโฟลเดอร์ทำงานหรือไม่?",
    approve: "อนุมัติ",
    decline: "ปฏิเสธ",
    kicker: "Desktop Agent · Windows",
    heading: "เอเจนต์ที่ทำงานบนเครื่องจริง โดยมีการอนุมัติคั่นก่อนความเสี่ยง",
    lead: "ควบคุมหน้าจอ จัดการไฟล์ และรันคำสั่งพร้อมแสดงผลสด และจะขออนุมัติก่อนทุกขั้นตอนที่อาจเปลี่ยนแปลงสิ่งสำคัญ",
    facts: [
      { b: "เครื่องมือในเครื่อง 39 รายการ", rest: " — ควบคุมหน้าจอผ่าน UIA ไฟล์ และคำสั่งพร้อมผลสด" },
      { b: "ระดับการขออนุมัติ", rest: " และตัวตรวจจับคำสั่งอันตราย" },
      { b: "เชื่อมต่อ Chrome", rest: " หน่วยความจำ ทักษะ และการตั้งเวลา" },
    ],
    mod1H: "นโยบาย ตัวตรวจจับ และหลักฐาน ในที่เดียว",
    mod1P: "ตั้งค่าผู้ให้บริการและรายการโมเดล แก้ไขนโยบาย ทดสอบการปิดบังแบบสด และตรวจย้อนหลังคำขอได้ครบสี่ขั้น",
    mod2H: "โค้ดชุดเดิม เปลี่ยนแค่ base URL",
    mod2P: "เข้ากันได้กับ OpenAI รองรับ tools และการตอบแบบสตรีมครบ พร้อมการเลือกโมเดลอัตโนมัติ ครอบคลุม 9 ความสามารถตั้งแต่แชตจนถึงเวกเตอร์",
  },
  en: {
    agentAria: "Desktop Agent screen example",
    task1: "Opened Q3 sales report",
    task2: "Extracted figures to a table",
    task3: "Cleaning up temporary files",
    approveHead: "Approval required",
    approveBody: "Delete 12 temporary files in the working folder?",
    approve: "Approve",
    decline: "Decline",
    kicker: "Desktop Agent · Windows",
    heading: "An agent that works the machine, with approvals in front of risk",
    lead: "It drives the screen, handles files and runs commands with live output — and asks before any step that could change something important.",
    facts: [
      { b: "39 local tools", rest: " — screen control via UIA, files, live command output" },
      { b: "Approval tiers", rest: " and a detector for destructive commands" },
      { b: "Chrome bridge", rest: ", memory, skills and scheduled runs" },
    ],
    mod1H: "Policy, detectors and evidence in one place",
    mod1P: "Configure providers and model catalogues, edit detection policy, test masking live, and replay any request across four stages.",
    mod2H: "The same code, a different base URL",
    mod2P: "OpenAI-compatible with full tools and streaming support, plus automatic model selection. Nine capability surfaces from chat to embeddings.",
  },
} as const;

// The Desktop Agent visual is a CSS-rendered mock (no real screenshot
// of SranDesktop exists yet) — swap for a real capture when one lands.
export function Surfaces() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section section-grey">
      <div className="wrap">
        <div className="split split-flip">
          <div className="split-media ui reveal" role="img" aria-label={t.agentAria}>
            <div className="ui-bar">
              <span className="ui-dot ui-dot-r" />
              <span className="ui-dot ui-dot-y" />
              <span className="ui-dot ui-dot-g" />
              <span className="ui-title">SRAN Desktop Agent</span>
            </div>
            <div className="agent-body">
              <div className="agent-task">
                <span className="tick">
                  <Check size={12} aria-hidden="true" />
                </span>
                <span>{t.task1}</span>
              </div>
              <div className="agent-task">
                <span className="tick">
                  <Check size={12} aria-hidden="true" />
                </span>
                <span>{t.task2}</span>
              </div>
              <div className="agent-task agent-task-run">
                <span className="tick">3</span>
                <span>{t.task3}</span>
              </div>
              <div className="agent-approve">
                <div className="aa-head">
                  <ShieldCheck size={15} aria-hidden="true" /> <span>{t.approveHead}</span>
                </div>
                <p>{t.approveBody}</p>
                <div className="aa-btns">
                  <span className="aa-btn aa-btn-yes">{t.approve}</span>
                  <span className="aa-btn aa-btn-no">{t.decline}</span>
                </div>
              </div>
            </div>
          </div>
          <div className="reveal">
            <span className="kicker">{t.kicker}</span>
            <h2 className="split-h">{t.heading}</h2>
            <p className="split-lead">{t.lead}</p>
            <ul className="split-facts">
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
        </div>

        <div className="modules">
          <div className="module reveal">
            <span className="kicker">Admin Console</span>
            <h3>{t.mod1H}</h3>
            <p>{t.mod1P}</p>
            <p className="module-meta">Secure Trace · audit log</p>
          </div>
          <div className="module reveal">
            <span className="kicker">Gateway API</span>
            <h3>{t.mod2H}</h3>
            <p>{t.mod2P}</p>
            <p className="module-meta">OpenAI-compatible · model: &quot;auto&quot;</p>
          </div>
        </div>
      </div>
    </section>
  );
}
