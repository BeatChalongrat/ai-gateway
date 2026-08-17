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
    title: "เงื่อนไขการใช้งาน",
    subtitle:
      "ข้อตกลงการใช้ระบบ SRAN AI Gateway สำหรับการติดตั้งแบบ self-hosted ในองค์กร",
    updated: "ปรับปรุงล่าสุด: สิงหาคม 2569",
    draftNote:
      "เอกสารฉบับนี้เป็นฉบับร่าง ควรให้ทีมกฎหมายขององค์กรตรวจสอบก่อนนำไปใช้อย่างเป็นทางการ ข้อตกลงเป็นลายลักษณ์อักษรระหว่างองค์กรของคุณกับบริษัท (ถ้ามี) มีผลเหนือเอกสารนี้",
    tocTitle: "ในหน้านี้",
    sections: [
      {
        id: "basics",
        h: "ภาพรวม",
        blocks: [
          {
            type: "p",
            text: "SRAN AI Gateway เป็นซอฟต์แวร์ที่พัฒนาโดย บริษัท โกลบอลเทคโนโลยี อินทิเกรเทด จำกัด ติดตั้งและทำงานบนโครงสร้างพื้นฐานขององค์กรผู้ใช้ตามข้อตกลงระหว่างองค์กรกับบริษัท ไม่มีการสมัครใช้งานหรือชำระเงินแบบออนไลน์ผ่านหน้านี้ — การนำระบบไปใช้เริ่มจากการติดต่อทีมงาน",
          },
        ],
      },
      {
        id: "accounts",
        h: "บัญชีผู้ใช้",
        blocks: [
          {
            type: "ul",
            items: [
              "การเข้าสู่ระบบใช้ Google SSO ภายใต้โดเมนอีเมลที่องค์กรของคุณอนุญาตเท่านั้น",
              "คุณมีหน้าที่รักษาบัญชีและอุปกรณ์ที่ใช้เข้าสู่ระบบให้ปลอดภัย",
              "สิทธิ์การใช้งานถูกกำหนดโดยผู้ดูแลระบบขององค์กรของคุณ",
            ],
          },
        ],
      },
      {
        id: "acceptable-use",
        h: "การใช้งานที่ยอมรับได้",
        blocks: [
          { type: "p", text: "ห้ามใช้ระบบเพื่อ:" },
          {
            type: "ul",
            items: [
              "กระทำการที่ผิดกฎหมายในเขตอำนาจที่มีผลกับคุณหรือองค์กรของคุณ",
              "หลบเลี่ยงการปิดบังข้อมูล การจำกัดอัตราการเรียกใช้ หรือนโยบายที่องค์กรกำหนด",
              "นำสิทธิ์เข้าถึงโมเดลไปให้บริการต่อ (resell) โดยไม่ได้รับอนุญาตเป็นลายลักษณ์อักษร",
              "สร้างเนื้อหาที่ละเมิดสิทธิผู้อื่นหรือก่อความเสียหาย",
            ],
          },
          {
            type: "p",
            text: "การใช้งานยังอยู่ใต้เงื่อนไขการใช้งานของผู้ให้บริการโมเดลที่คำขอถูกส่งไปด้วย",
          },
        ],
      },
      {
        id: "your-content",
        h: "เนื้อหาของคุณ",
        blocks: [
          {
            type: "p",
            text: "องค์กรของคุณเป็นเจ้าของคำขอที่ส่งและคำตอบที่ได้รับ บริษัทไม่อ้างสิทธิ์ในเนื้อหา ไม่นำเนื้อหาไปฝึกโมเดล และประมวลผลเนื้อหาเท่าที่จำเป็นต่อการทำงานของระบบเท่านั้น",
          },
        ],
      },
      {
        id: "ip",
        h: "ทรัพย์สินทางปัญญา",
        blocks: [
          {
            type: "p",
            text: "ซอฟต์แวร์ การออกแบบ เอกสาร และเครื่องหมายการค้า SRAN เป็นของบริษัท องค์กรของคุณได้รับสิทธิ์ใช้งานตามขอบเขตของข้อตกลงที่ทำไว้กับบริษัท",
          },
        ],
      },
      {
        id: "service",
        h: "การให้บริการ",
        blocks: [
          {
            type: "p",
            text: "ระบบให้บริการตามสภาพ (as-is) โดยไม่มีคำมั่นเรื่องระยะเวลาพร้อมใช้งานในเอกสารนี้ หากข้อตกลงเป็นลายลักษณ์อักษรระหว่างองค์กรของคุณกับบริษัทกำหนดไว้เป็นอย่างอื่น ให้ยึดตามข้อตกลงนั้น",
          },
        ],
      },
      {
        id: "liability",
        h: "ข้อจำกัดความรับผิด",
        blocks: [
          {
            type: "p",
            text: "ภายใต้ขอบเขตสูงสุดที่กฎหมายอนุญาต บริษัทไม่รับผิดต่อความเสียหายทางอ้อมหรือความเสียหายต่อเนื่องจากการใช้ระบบ ขอบเขตความรับผิดโดยละเอียดเป็นไปตามข้อตกลงเป็นลายลักษณ์อักษรระหว่างองค์กรของคุณกับบริษัท ทั้งนี้ไม่ตัดสิทธิ์ความรับผิดที่กฎหมายไม่ให้ยกเว้น",
          },
        ],
      },
      {
        id: "changes",
        h: "การเปลี่ยนแปลงเงื่อนไข",
        blocks: [
          {
            type: "p",
            text: "เมื่อเงื่อนไขนี้เปลี่ยนแปลงอย่างมีนัยสำคัญ วันที่ปรับปรุงที่หัวเอกสารจะถูกแก้ไข และการเปลี่ยนแปลงจะแจ้งผ่านองค์กรที่ใช้งานระบบ",
          },
        ],
      },
      {
        id: "governing-law",
        h: "กฎหมายที่ใช้บังคับ",
        blocks: [
          {
            type: "p",
            text: "เงื่อนไขนี้อยู่ใต้บังคับกฎหมายไทย ข้อพิพาทอยู่ในเขตอำนาจของศาลไทย เว้นแต่ข้อตกลงเป็นลายลักษณ์อักษรกำหนดไว้เป็นอย่างอื่น",
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
    title: "Terms of Use",
    subtitle:
      "The agreement for using SRAN AI Gateway as a self-hosted deployment inside your organisation.",
    updated: "Last updated: August 2026",
    draftNote:
      "This document is a draft; your organisation's legal team should review it before formal use. Where a written agreement between your organisation and the company exists, that agreement controls.",
    tocTitle: "On this page",
    sections: [
      {
        id: "basics",
        h: "The basics",
        blocks: [
          {
            type: "p",
            text: "SRAN AI Gateway is software developed by Global Technology Integrated Co., Ltd., installed and run on the deploying organisation's own infrastructure under an agreement between that organisation and the company. There is no online sign-up or payment on this site — adoption starts by contacting the team.",
          },
        ],
      },
      {
        id: "accounts",
        h: "Accounts",
        blocks: [
          {
            type: "ul",
            items: [
              "Sign-in is Google SSO only, restricted to email domains your organisation allows.",
              "You are responsible for keeping your account and sign-in devices secure.",
              "Access rights are governed by your organisation's administrator.",
            ],
          },
        ],
      },
      {
        id: "acceptable-use",
        h: "Acceptable use",
        blocks: [
          { type: "p", text: "Don't use the system to:" },
          {
            type: "ul",
            items: [
              "Break the law in any jurisdiction that applies to you or your organisation.",
              "Circumvent masking, rate limits, or policies your organisation has set.",
              "Resell model access without explicit written permission.",
              "Generate content that infringes others' rights or causes harm.",
            ],
          },
          {
            type: "p",
            text: "Use is also subject to the terms of the model providers your requests are routed to.",
          },
        ],
      },
      {
        id: "your-content",
        h: "Your content",
        blocks: [
          {
            type: "p",
            text: "Your organisation owns the prompts it sends and the completions it receives. The company claims no rights to that content, does not train models on it, and processes it only as needed to operate the system.",
          },
        ],
      },
      {
        id: "ip",
        h: "Intellectual property",
        blocks: [
          {
            type: "p",
            text: "The software, design, documentation and the SRAN trademark belong to the company. Your organisation receives a licence to use the system within the scope of its agreement with the company.",
          },
        ],
      },
      {
        id: "service",
        h: "The service",
        blocks: [
          {
            type: "p",
            text: "The system is provided as-is, with no availability commitment made in this document. Where a written agreement between your organisation and the company says otherwise, that agreement controls.",
          },
        ],
      },
      {
        id: "liability",
        h: "Limitation of liability",
        blocks: [
          {
            type: "p",
            text: "To the maximum extent permitted by law, the company is not liable for indirect or consequential damages arising from use of the system. Detailed liability terms follow the written agreement between your organisation and the company. Nothing here excludes liability that cannot legally be limited.",
          },
        ],
      },
      {
        id: "changes",
        h: "Changes to these terms",
        blocks: [
          {
            type: "p",
            text: "When these terms change materially, the revision date at the top is updated and the change is communicated through the organisations using the system.",
          },
        ],
      },
      {
        id: "governing-law",
        h: "Governing law",
        blocks: [
          {
            type: "p",
            text: "These terms are governed by the laws of Thailand, with disputes under the jurisdiction of Thai courts unless a written agreement says otherwise.",
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

export default function TermsContent() {
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
