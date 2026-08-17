"use client";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "ความปลอดภัยและ PDPA",
    heading: "บังคับใช้ที่เซิร์ฟเวอร์ และเขียนจากสิ่งที่โค้ดทำจริง",
    sub: "การตรวจจับออกแบบเพื่อข้อมูลส่วนบุคคลของไทย และมาตรการทุกข้อด้านล่างมีอยู่จริงในระบบวันนี้ สิ่งที่ยังไม่ได้พัฒนาจะไม่ถูกระบุไว้",
    col1H: "เฉพาะประเทศไทย",
    col1Count: "8 detectors",
    col1: ["เลขบัตรประชาชน", "เลขประจำตัวผู้เสียภาษี", "เลขที่บัญชีธนาคาร", "PromptPay", "ที่อยู่ในประเทศไทย", "ชื่อ–นามสกุลภาษาไทย", "LINE ID"],
    col2H: "PDPA มาตรา 26",
    col2Count: "ข้อมูลอ่อนไหว",
    col2: ["ศาสนา", "ข้อมูลสุขภาพ", "ประวัติอาชญากรรม", "ข้อมูลชีวมิติ"],
    col3H: "สากลและกำหนดเอง",
    col3Count: "ส่วนที่เหลือ",
    col3: ["อีเมลและหมายเลขโทรศัพท์", "บัตรชำระเงิน", "คีย์และโทเคน", "IP / MAC", "ตัวตรวจจับที่คุณกำหนดเอง"],
    enforcement: [
      {
        dt: "เข้าสู่ระบบด้วย Google เท่านั้น",
        dd: "ตรวจสอบโดเมนอีเมลและสถานะการยืนยันที่ฝั่งเซิร์ฟเวอร์ก่อนเข้าถึงบัญชีใด ครอบคลุมการเข้าสู่ระบบ การสร้างบัญชีครั้งแรก และการเชื่อมบัญชี",
      },
      {
        dt: "เข้ารหัสข้อมูลที่จัดเก็บด้วย AES-256-GCM",
        dd: "คีย์ผู้ให้บริการ บันทึกการตรวจสอบ ข้อความคำขอที่จัดเก็บ และโทเคนของ connector เข้ารหัสด้วยคีย์หลักที่แยกจากฐานข้อมูล",
      },
      {
        dt: "ปฏิเสธการเริ่มระบบเมื่อค่าลับไม่ปลอดภัย",
        dd: "ในโหมด production ระบบปฏิเสธการเริ่มทำงานเมื่อพบค่าลับที่อ่อนหรือยังเป็นค่าตัวอย่าง และมีตัวเฝ้าระวังความต่างของ schema ที่แจ้งเตือนชัดเจน",
      },
      {
        dt: "จำกัดอัตราการเรียกใช้สามระดับ",
        dd: "นับแยกตามคีย์ โปรเจกต์ และองค์กร พร้อมแยกรายผู้ใช้บนคีย์ที่ใช้ร่วมกัน และมีส่วนเสริม Rust สำหรับปริมาณสูง",
      },
      {
        dt: "แซนด์บ็อกซ์เป็นขอบเขตความเชื่อถือ",
        dd: "โค้ดที่โมเดลสั่งรันอยู่ในคอนเทนเนอร์ที่ไม่มีเครือข่าย ระบบไฟล์อ่านได้อย่างเดียว และไม่ใช้สิทธิ์ root จึงไม่มีเส้นทางให้ข้อมูลออกผ่านการรันโค้ด",
      },
      {
        dt: "ตรวจสอบย้อนหลังได้ทุกคำขอ",
        dd: "ตรวจย้อนหลังได้ทีละคำขอครบสี่ขั้น พร้อมบันทึกการตรวจสอบฉบับเต็ม รายงานความครอบคลุม และเมตริกส่งออกไปยัง Prometheus และ Grafana",
      },
    ],
    disclosureLabel: "สิ่งที่ยังไม่มี:",
    disclosure:
      " ยังไม่มีการรับรองมาตรฐานใดที่อ้างอิงได้ ทั้ง SOC 2, ISO 27001, HIPAA และ GDPR certification และจะไม่ถูกระบุบนหน้านี้จนกว่าจะได้รับจริง เอกสารสถาปัตยกรรมภายในระบุรายการที่ยังต้องดำเนินการไว้ครบถ้วน และเปิดให้ตรวจสอบได้ระหว่างการประเมิน",
  },
  en: {
    kicker: "Security & PDPA",
    heading: "Enforced on the server. Written from what the code does.",
    sub: "Detection is built for Thai personal data, and every control below exists in the system today. Anything not yet built is not listed.",
    col1H: "Thailand-specific",
    col1Count: "8 detectors",
    col1: ["National ID number", "Taxpayer ID", "Bank account number", "PromptPay", "Thai postal address", "Thai personal names", "LINE ID"],
    col2H: "PDPA section 26",
    col2Count: "Sensitive data",
    col2: ["Religion", "Health data", "Criminal record", "Biometric data"],
    col3H: "General and custom",
    col3Count: "Everything else",
    col3: ["Email and phone", "Payment cards", "Keys and tokens", "IP / MAC", "Detectors you define"],
    enforcement: [
      {
        dt: "Google sign-in only",
        dd: "The email domain and its verification status are checked server-side before any account is touched — covering sign-in, first-time creation and account linking.",
      },
      {
        dt: "AES-256-GCM encryption at rest",
        dd: "Provider keys, audit records, retained request text and connector tokens are encrypted with a master key held separately from the database.",
      },
      {
        dt: "Refuses to start on weak secrets",
        dd: "In production the service refuses to start when a secret is weak or still a sample value, and a schema drift guard fails loudly rather than continuing silently.",
      },
      {
        dt: "Rate limiting at three levels",
        dd: "Counted per key, per project and per organisation, with per-user separation on shared keys. A Rust sidecar can be enabled for high volume.",
      },
      {
        dt: "The sandbox is a trust boundary",
        dd: "Model-authored code runs in a container with no network, a read-only filesystem and a non-root user — no path for data to leave through code execution.",
      },
      {
        dt: "Every request is auditable",
        dd: "Requests replay across four stages, with a full audit log, detector coverage reports and metrics exported to Prometheus and Grafana.",
      },
    ],
    disclosureLabel: "What we do not have:",
    disclosure:
      " no certification we could claim — no SOC 2, ISO 27001, HIPAA or GDPR certification — and none will appear on this page before it is actually granted. Internal architecture documents list the outstanding items in full and are available during an evaluation.",
  },
} as const;

export function Security() {
  const { lang } = useLang();
  const t = copy[lang];

  const cols = [
    { cls: "dcol", h: t.col1H, count: t.col1Count, items: t.col1 },
    { cls: "dcol dcol-warn", h: t.col2H, count: t.col2Count, items: t.col2 },
    { cls: "dcol dcol-grey", h: t.col3H, count: t.col3Count, items: t.col3 },
  ];

  return (
    <section className="section" id="security">
      <div className="wrap">
        <div className="section-head">
          <span className="kicker">{t.kicker}</span>
          <h2 className="section-h">{t.heading}</h2>
          <p className="section-sub">{t.sub}</p>
        </div>

        <div className="detector-cols">
          {cols.map((c) => (
            <div className={`${c.cls} reveal`} key={c.h}>
              <h3>{c.h}</h3>
              <span className="dcol-count">{c.count}</span>
              <ul>
                {c.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <dl className="enforcement">
          {t.enforcement.map((e) => (
            <div key={e.dt}>
              <dt>{e.dt}</dt>
              <dd>{e.dd}</dd>
            </div>
          ))}
        </dl>

        <p className="disclosure">
          <strong>{t.disclosureLabel}</strong>
          {t.disclosure}
        </p>
      </div>
    </section>
  );
}
