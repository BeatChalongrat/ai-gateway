"use client";

import CodeTabs, { type CodeTab } from "./CodeTabs";
import { useLang } from "./LanguageProvider";

const TABS: CodeTab[] = [
  {
    label: "curl",
    code: `curl https://gateway.your-org/v1/chat/completions \\
  -H 'Authorization: Bearer gw_…' \\
  -H 'Content-Type: application/json' \\
  -d '{"model":"auto","messages":[…]}'

# the policy decision comes back with the response
"policy_action": "mask"
"policy_reasons": ["th_national_id", "email"]`,
  },
  {
    label: "Python",
    code: `from openai import OpenAI

# same SDK, only base_url changes
client = OpenAI(
    base_url="https://gateway.your-org/v1",
    api_key="gw_…",
)

resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "…"}],
)`,
  },
  {
    label: "TypeScript",
    code: `import OpenAI from "openai";

// same SDK, only baseURL changes
const client = new OpenAI({
  baseURL: "https://gateway.your-org/v1",
  apiKey: "gw_…",
});

const resp = await client.chat.completions.create({
  model: "auto",
  messages: [{ role: "user", content: "…" }],
});`,
  },
];

const copy = {
  th: {
    kicker: "การติดตั้งระดับองค์กร",
    heading: "ข้อมูลไม่ออกจากโครงสร้างพื้นฐานขององค์กร",
    lead: "ทั้งระบบทำงานด้วย Docker Compose บนเครื่องขององค์กรเอง ตั้งแต่ gateway ฐานข้อมูล ไปจนถึงชุดเฝ้าระวัง",
    items: [
      { k: "bootstrap", v: "สร้างค่าลับที่ปลอดภัย ประกอบอิมเมจ ปรับปรุงฐานข้อมูล และสร้างผู้ใช้เริ่มต้น ในคำสั่งเดียว" },
      { k: "production", v: "ทุกพอร์ตผูกกับ 127.0.0.1 ภายใต้ nginx จึงไม่มีบริการใดเปิดออกสู่อินเทอร์เน็ตโดยไม่ตั้งใจ" },
      { k: "compatible", v: "เปลี่ยนเพียง base URL แล้วโค้ดที่เขียนกับ OpenAI SDK เดิมยังใช้งานได้ทันที" },
    ],
    copyLabel: "คัดลอก",
    copiedLabel: "คัดลอกแล้ว",
  },
  en: {
    kicker: "Enterprise deployment",
    heading: "Data never leaves your infrastructure",
    lead: "The whole stack runs on Docker Compose on your own machines — from the gateway and database through to the monitoring tools.",
    items: [
      { k: "bootstrap", v: "Generates strong secrets, builds images, applies migrations and seeds the initial users in one command." },
      { k: "production", v: "Every port binds to 127.0.0.1 behind nginx, so nothing is exposed to the internet by accident." },
      { k: "compatible", v: "Change only the base URL and code written against the OpenAI SDK keeps working." },
    ],
    copyLabel: "Copy",
    copiedLabel: "Copied",
  },
} as const;

export function Deploy() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section" id="deploy">
      <div className="wrap deploy-grid">
        <div>
          <span className="kicker">{t.kicker}</span>
          <h2 className="section-h">{t.heading}</h2>
          <p className="deploy-lead">{t.lead}</p>
          <ul className="deploy-list">
            {t.items.map((item) => (
              <li key={item.k}>
                <strong>{item.k}</strong>
                <span>{item.v}</span>
              </li>
            ))}
          </ul>
        </div>

        <CodeTabs tabs={TABS} copyLabel={t.copyLabel} copiedLabel={t.copiedLabel} />
      </div>
    </section>
  );
}
