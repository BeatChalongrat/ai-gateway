"use client";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "งานที่เหมาะกับระบบนี้",
    heading: "สถานการณ์การใช้งานจากความสามารถที่มีจริง",
    s1: {
      fragComment: "// สรุปงานบริการ",
      fragCustomer: "ลูกค้า",
      fragMasked: "ปิดบังก่อนส่งออก",
      fragRestored: "คืนค่าในคำตอบ",
      h: "ทีมบริการลูกค้าสรุปเคส",
      p: "พนักงานวางบันทึกเคสที่มีชื่อและเบอร์โทร ระบบปิดบังข้อมูลระบุตัวตนก่อนถึงโมเดล และบทสรุปกลับมาพร้อมค่าจริง",
      tag: "Web Chat · การปิดบัง",
    },
    s2: {
      fragChunk: "ตัดท่อน → ฝังเวกเตอร์",
      fragMatch: "จับคู่ top-k",
      fragCite: "อ้างอิง น.12",
      h: "ตอบจากเอกสารภายใน",
      p: "อัปโหลดคู่มือพนักงานและนโยบายภายใน ทีมถามด้วยภาษาปกติและได้คำตอบพร้อมการอ้างอิงย่อหน้า โดยเอกสารไม่ออกจากบริษัท",
      tag: "คลังความรู้ · RAG",
    },
    s3: {
      fragOpen: "เปิดรายงาน",
      fragPull: "ดึงข้อมูลยอดขาย",
      fragApproval: "ขออนุมัติ",
      fragDelete: "ลบไฟล์ชั่วคราว 12 ไฟล์",
      h: "งานประจำบน Windows",
      p: "เอเจนต์เดสก์ท็อปเปิดโปรแกรม จัดการไฟล์ และรันคำสั่งบนเครื่องจริง โดยมีด่านขออนุมัติก่อนทุกขั้นตอนที่เสี่ยง",
      tag: "Desktop Agent · การอนุมัติ",
    },
    s4: {
      fragReq: "คำขอ #4821",
      fragDetect: "ตรวจจับ",
      fragMask: "ปิดบัง",
      fragRoute: "ส่งต่อ",
      fragRestore: "คืนค่า",
      fragTrace: "เก็บ trace ครบ",
      h: "หลักฐานให้ผู้ตรวจสอบ รายคำขอ",
      p: "ทุกคำขอถูกบันทึกพร้อมผลการตัดสินของนโยบาย ตรวจย้อนหลังได้ครบสี่ขั้น บันทึกเก็บเป็นจำนวนครั้งรายประเภท ไม่เก็บตัวข้อความ",
      tag: "Secure Trace · บันทึกตรวจสอบ",
    },
  },
  en: {
    kicker: "Where it fits",
    heading: "Deployment scenarios drawn from real capabilities",
    s1: {
      fragComment: "// support summary",
      fragCustomer: "customer",
      fragMasked: "masked before leaving",
      fragRestored: "restored on reply",
      h: "Summarising customer cases",
      p: "Support staff paste case notes with names and phone numbers. Identifiers are masked before the model sees them, and the summary returns with real values in place.",
      tag: "Web Chat · masking",
    },
    s2: {
      fragChunk: "chunk → embed",
      fragMatch: "top-k match",
      fragCite: "cite p.12",
      h: "Answering from internal documents",
      p: "Upload the employee handbook and internal policies. Teams ask in plain language and get answers citing the exact passage — documents never leave the company.",
      tag: "Knowledge base · RAG",
    },
    s3: {
      fragOpen: "open report",
      fragPull: "pull sales data",
      fragApproval: "approval required",
      fragDelete: "delete 12 temp files",
      h: "Automating routine work on Windows",
      p: "The desktop agent opens applications, handles files and runs commands on a real machine — with an approval gate before every risky step.",
      tag: "Desktop Agent · approvals",
    },
    s4: {
      fragReq: "request #4821",
      fragDetect: "detect",
      fragMask: "mask",
      fragRoute: "route",
      fragRestore: "restore",
      fragTrace: "full trace saved",
      h: "Evidence for auditors, per request",
      p: "Every request is recorded with the policy decision and can be replayed across four stages. Audit records store category counts, never the sensitive text.",
      tag: "Secure Trace · audit log",
    },
  },
} as const;

export function UseCases() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section section-grey">
      <div className="wrap">
        <div className="section-head">
          <span className="kicker">{t.kicker}</span>
          <h2 className="section-h">{t.heading}</h2>
        </div>
        <div className="scenarios-grid">
          <article className="scenario reveal">
            <div className="scenario-top">
              <div className="scenario-frag">
                <span className="frag-muted">{t.s1.fragComment}</span>
                <br />
                {t.s1.fragCustomer} <span className="frag-p">somchai@…</span>
                <br />→ <span className="frag-t">{t.s1.fragMasked}</span>
                <br />
                <span className="frag-ok">✓ {t.s1.fragRestored}</span>
              </div>
            </div>
            <div className="scenario-body">
              <h3>{t.s1.h}</h3>
              <p>{t.s1.p}</p>
              <div className="scenario-tag">{t.s1.tag}</div>
            </div>
          </article>

          <article className="scenario reveal">
            <div className="scenario-top">
              <div className="scenario-frag">
                <span className="frag-muted">handbook.pdf</span>
                <br />
                {t.s2.fragChunk}
                <br />
                <span className="frag-t">{t.s2.fragMatch}</span>
                <br />
                <span className="frag-ok">✓ {t.s2.fragCite}</span>
              </div>
            </div>
            <div className="scenario-body">
              <h3>{t.s2.h}</h3>
              <p>{t.s2.p}</p>
              <div className="scenario-tag">{t.s2.tag}</div>
            </div>
          </article>

          <article className="scenario reveal">
            <div className="scenario-top">
              <div className="scenario-frag">
                <span className="frag-ok">✓</span> {t.s3.fragOpen}
                <br />
                <span className="frag-ok">✓</span> {t.s3.fragPull}
                <br />
                <span className="frag-warn">⚠ {t.s3.fragApproval}</span>
                <br />
                <span className="frag-muted">{t.s3.fragDelete}</span>
              </div>
            </div>
            <div className="scenario-body">
              <h3>{t.s3.h}</h3>
              <p>{t.s3.p}</p>
              <div className="scenario-tag">{t.s3.tag}</div>
            </div>
          </article>

          <article className="scenario reveal">
            <div className="scenario-top">
              <div className="scenario-frag">
                <span className="frag-muted">{t.s4.fragReq}</span>
                <br />1 {t.s4.fragDetect} · 2 {t.s4.fragMask}
                <br />3 {t.s4.fragRoute} · 4 {t.s4.fragRestore}
                <br />
                <span className="frag-ok">✓ {t.s4.fragTrace}</span>
              </div>
            </div>
            <div className="scenario-body">
              <h3>{t.s4.h}</h3>
              <p>{t.s4.p}</p>
              <div className="scenario-tag">{t.s4.tag}</div>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
