"use client";

import { Fragment } from "react";

import { useLang } from "../_components/LanguageProvider";
import PageShell from "../_components/PageShell";

type Block =
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] };

type Section = { id: string; h: string; blocks: Block[] };

const copy = {
  th: {
    eyebrow: "กฎหมาย",
    title: "นโยบายความเป็นส่วนตัว",
    subtitle:
      "อธิบายว่าระบบ SRAN AI Gateway ประมวลผลข้อมูลอะไร อย่างไร — เขียนจากพฤติกรรมจริงของระบบ",
    updated: "ปรับปรุงล่าสุด: สิงหาคม 2569",
    draftNote:
      "เอกสารฉบับนี้เป็นฉบับร่างที่เขียนจากพฤติกรรมจริงของระบบ ควรให้ทีมกฎหมายขององค์กรตรวจสอบก่อนนำไปใช้อย่างเป็นทางการ",
    tocTitle: "ในหน้านี้",
    sections: [
      {
        id: "controller",
        h: "ใครเป็นผู้ควบคุมข้อมูล",
        blocks: [
          {
            type: "p",
            text: "SRAN AI Gateway เป็นซอฟต์แวร์แบบ self-hosted ติดตั้งและทำงานบนโครงสร้างพื้นฐานขององค์กรที่นำไปใช้เอง ข้อมูลทั้งหมดจึงอยู่บนเซิร์ฟเวอร์ขององค์กรนั้น และองค์กรที่ติดตั้งเป็นผู้ควบคุมข้อมูลส่วนบุคคล (data controller) ตาม PDPA",
          },
          {
            type: "p",
            text: "บริษัท โกลบอลเทคโนโลยี อินทิเกรเทด จำกัด เป็นผู้พัฒนาซอฟต์แวร์ ไม่ได้เข้าถึงข้อมูลที่ประมวลผลในระบบที่องค์กรของคุณติดตั้ง",
          },
        ],
      },
      {
        id: "what-we-process",
        h: "ข้อมูลที่ระบบประมวลผล",
        blocks: [
          {
            type: "ul",
            items: [
              "บัญชีผู้ใช้ — เข้าสู่ระบบด้วย Google SSO เท่านั้น ระบบตรวจสอบโดเมนอีเมลและสถานะการยืนยันที่ฝั่งเซิร์ฟเวอร์ก่อนเข้าถึงบัญชีใด",
              "เมทาดาต้าคำขอ — โมเดลที่ใช้ จำนวน token เวลา และคีย์/โปรเจกต์ที่เรียก เพื่อการคิดปริมาณการใช้และการตรวจสอบย้อนหลัง",
              "บันทึกการตรวจจับ — เก็บเป็นจำนวนครั้งรายประเภทข้อมูล (เช่น พบอีเมล 2 ครั้ง) ไม่เก็บตัวข้อความอ่อนไหวในบันทึกตรวจสอบ",
            ],
          },
        ],
      },
      {
        id: "masking",
        h: "การปิดบังข้อมูลก่อนออกนอกระบบ",
        blocks: [
          {
            type: "p",
            text: "ตัวตรวจจับมากกว่า 30 รายการร่วมกับการรู้จำชื่อเฉพาะภาษาไทยสแกนทุกคำขอ ค่าอ่อนไหวถูกแทนด้วยค่าแทนกลางก่อนคำขอไปถึงผู้ให้บริการโมเดลภายนอก และคืนค่าในคำตอบ",
          },
          {
            type: "p",
            text: "ระหว่างการประมวลผล ค่าจริงอยู่ในหน่วยความจำของคำขอเพียงชั่วคราว ระบบไม่เขียนค่าจริงเป็นข้อความเปิด (plaintext) ลงบันทึกหรือฐานข้อมูล ข้อความคำขอที่ระบบเลือกจัดเก็บจะถูกเข้ารหัสด้วย AES-256-GCM",
          },
        ],
      },
      {
        id: "raw-egress",
        h: "ช่องทางที่จำเป็นต้องเห็นข้อมูลจริง",
        blocks: [
          {
            type: "p",
            text: "ความสามารถ 4 กลุ่ม — การค้นเว็บ การสร้างสื่อ เวกเตอร์ฝังตัว และเสียง — ทำงานไม่ได้หากไม่ส่งข้อความจริงไปยังผู้ให้บริการภายนอก ช่องทางเหล่านี้ถูกกำหนดเป็นค่าคงที่ในโค้ด องค์กรที่ต้องการเข้มงวดกว่านี้ตั้งค่าให้บล็อกทั้งหมดเป็นรายองค์กรได้",
          },
          {
            type: "p",
            text: "ข้อมูลประเภทรหัสผ่าน คีย์ และโทเคน ถูกบล็อกเสมอ แม้ในช่องทางเหล่านี้",
          },
        ],
      },
      {
        id: "security",
        h: "การจัดเก็บและมาตรการความปลอดภัย",
        blocks: [
          {
            type: "ul",
            items: [
              "ข้อมูลที่จัดเก็บ (คีย์ผู้ให้บริการ บันทึกการตรวจสอบ ข้อความคำขอที่เลือกเก็บ และโทเคนของ connector) เข้ารหัสด้วย AES-256-GCM โดยคีย์หลักแยกจากฐานข้อมูล",
              "โค้ดที่โมเดลสั่งรันอยู่ในแซนด์บ็อกซ์ที่ไม่มีเครือข่ายและไม่ใช้สิทธิ์ root",
              "ระบบไม่นำเนื้อหาของผู้ใช้ไปฝึกโมเดล และไม่ส่งข้อมูลให้ผู้โฆษณา",
            ],
          },
        ],
      },
      {
        id: "your-rights",
        h: "สิทธิของเจ้าของข้อมูล",
        blocks: [
          {
            type: "p",
            text: "ภายใต้ PDPA เจ้าของข้อมูลมีสิทธิขอเข้าถึง แก้ไข หรือลบข้อมูลของตน เนื่องจากผู้ควบคุมข้อมูลคือองค์กรที่ติดตั้งระบบ โปรดติดต่อผู้ดูแลระบบขององค์กรของคุณเป็นลำดับแรก คำถามเกี่ยวกับตัวซอฟต์แวร์ติดต่อผู้พัฒนาได้ตามช่องทางด้านล่าง",
          },
        ],
      },
      {
        id: "changes",
        h: "การเปลี่ยนแปลงนโยบาย",
        blocks: [
          {
            type: "p",
            text: "เมื่อนโยบายนี้เปลี่ยนแปลงอย่างมีนัยสำคัญ วันที่ปรับปรุงที่หัวเอกสารจะถูกแก้ไข และการเปลี่ยนแปลงจะแจ้งผ่านองค์กรที่ใช้งานระบบ",
          },
        ],
      },
      {
        id: "contact",
        h: "ติดต่อ",
        blocks: [
          {
            type: "p",
            text: "บริษัท โกลบอลเทคโนโลยี อินทิเกรเทด จำกัด — 48/6 ซอยแจ้งวัฒนะ 14 แขวงทุ่งสองห้อง เขตหลักสี่ กรุงเทพฯ 10210 โทร 02-982-5445 / 02-982-5454",
          },
        ],
      },
    ] as Section[],
  },
  en: {
    eyebrow: "Legal",
    title: "Privacy Policy",
    subtitle:
      "What the SRAN AI Gateway system processes and how — written from the system's actual behaviour.",
    updated: "Last updated: August 2026",
    draftNote:
      "This document is a draft written from the system's actual behaviour. Your organisation's legal team should review it before formal use.",
    tocTitle: "On this page",
    sections: [
      {
        id: "controller",
        h: "Who controls the data",
        blocks: [
          {
            type: "p",
            text: "SRAN AI Gateway is self-hosted software, installed and run on the infrastructure of the organisation deploying it. All data stays on that organisation's servers, and the deploying organisation is the data controller under PDPA.",
          },
          {
            type: "p",
            text: "Global Technology Integrated Co., Ltd. develops the software and does not have access to data processed in your organisation's deployment.",
          },
        ],
      },
      {
        id: "what-we-process",
        h: "What the system processes",
        blocks: [
          {
            type: "ul",
            items: [
              "Accounts — sign-in is Google SSO only; the email domain and its verification status are checked server-side before any account is touched.",
              "Request metadata — the model used, token counts, timestamps and the key/project that made the call, for usage accounting and auditability.",
              "Detection records — stored as per-category counts (e.g. two emails found); audit records never store the sensitive text itself.",
            ],
          },
        ],
      },
      {
        id: "masking",
        h: "Masking before anything leaves",
        blocks: [
          {
            type: "p",
            text: "More than 30 detectors plus Thai named-entity recognition scan every request. Sensitive values are replaced with neutral placeholders before the request reaches an external model provider, and restored in the reply.",
          },
          {
            type: "p",
            text: "During processing, real values exist only transiently in request memory. The system does not write real values as plaintext to logs or the database; request text the system does retain is encrypted with AES-256-GCM.",
          },
        ],
      },
      {
        id: "raw-egress",
        h: "Channels that require the real value",
        blocks: [
          {
            type: "p",
            text: "Four capabilities — web search, media generation, embeddings and voice — cannot work without sending real text to an external provider. These channels are fixed as constants in the code, and organisations with stricter needs can block all four per tenant.",
          },
          {
            type: "p",
            text: "Credentials, keys and tokens are always blocked, even on these channels.",
          },
        ],
      },
      {
        id: "security",
        h: "Storage and security measures",
        blocks: [
          {
            type: "ul",
            items: [
              "Data at rest (provider keys, audit records, retained request text and connector tokens) is encrypted with AES-256-GCM, with the master key held separately from the database.",
              "Model-authored code runs in a sandbox with no network access and a non-root user.",
              "The system does not train models on user content and does not share data with advertisers.",
            ],
          },
        ],
      },
      {
        id: "your-rights",
        h: "Your rights",
        blocks: [
          {
            type: "p",
            text: "Under PDPA you can request access to, correction of, or deletion of your data. Because the data controller is the organisation running the deployment, contact your organisation's administrator first. Questions about the software itself can go to the developer using the contact below.",
          },
        ],
      },
      {
        id: "changes",
        h: "Changes to this policy",
        blocks: [
          {
            type: "p",
            text: "When this policy changes materially, the revision date at the top is updated and the change is communicated through the organisations using the system.",
          },
        ],
      },
      {
        id: "contact",
        h: "Contact",
        blocks: [
          {
            type: "p",
            text: "Global Technology Integrated Co., Ltd. — 48/6 Soi Chaeng Watthana 14, Thung Song Hong, Lak Si, Bangkok 10210. Tel 02-982-5445 / 02-982-5454.",
          },
        ],
      },
    ] as Section[],
  },
} as const;

export default function PrivacyContent() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <PageShell eyebrow={t.eyebrow} title={t.title} subtitle={t.subtitle}>
      <p className="legal-updated">{t.updated}</p>
      <p className="legal-draft-note">{t.draftNote}</p>

      <nav className="legal-toc" aria-label={t.tocTitle}>
        <p className="legal-toc-title">{t.tocTitle}</p>
        <ul>
          {t.sections.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`}>{s.h}</a>
            </li>
          ))}
        </ul>
      </nav>

      {/* Fragments keep each h2 a direct sibling of .legal-toc so the
          `.legal-toc ~ h2` divider rule applies. */}
      {t.sections.map((s) => (
        <Fragment key={s.id}>
          <h2 id={s.id}>{s.h}</h2>
          {s.blocks.map((b, i) =>
            b.type === "p" ? (
              <p key={i}>{b.text}</p>
            ) : (
              <ul key={i}>
                {b.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )
          )}
        </Fragment>
      ))}

      <p>
        <a href="mailto:info@gbtech.co.th">info@gbtech.co.th</a>
      </p>
    </PageShell>
  );
}
