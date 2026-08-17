"use client";

import CodeTabs, { type CodeTab } from "../_components/CodeTabs";
import { useLang } from "../_components/LanguageProvider";
import PageShell from "../_components/PageShell";

const QUICKSTART: CodeTab[] = [
  {
    label: "curl",
    code: `curl https://gateway.your-org/v1/chat/completions \\
  -H 'Authorization: Bearer gw_…' \\
  -H 'Content-Type: application/json' \\
  -d '{"model":"auto","messages":[{"role":"user","content":"…"}]}'

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

const SURFACES = [
  "chat",
  "models",
  "images",
  "videos",
  "audio",
  "voice",
  "search",
  "embeddings",
  "files",
];

const copy = {
  th: {
    eyebrow: "คู่มือนักพัฒนา",
    title: "เริ่มส่งคำขอผ่าน gateway ได้ในไม่กี่นาที",
    subtitle:
      "SRAN AI Gateway เข้ากันได้กับ OpenAI SDK — ชี้ SDK ไปที่ base URL ของ gateway ในองค์กรของคุณ โค้ดเดิมใช้ต่อได้ทันที",
    quickstartH: "เริ่มต้นใช้งาน",
    quickstartP:
      "เปลี่ยนสองอย่าง — base URL และ API key — ทุกคำขอจะผ่านการปิดบังข้อมูล การเลือกเส้นทางโมเดล และการบันทึกตรวจสอบของ gateway โดยอัตโนมัติ",
    copyLabel: "คัดลอก",
    copiedLabel: "คัดลอกแล้ว",
    refH: "ข้อมูลอ้างอิง",
    refP: "สิ่งที่ต้องรู้เพื่อส่งคำขอแรกให้ผ่าน:",
    refRows: [
      {
        dt: "Base URL",
        dd: null, // rendered inline — contains <code>
      },
      {
        dt: "การยืนยันตัวตน",
        dd: "ส่วนหัว Authorization: Bearer พร้อม API key ที่สร้างจากโปรเจกต์ของคุณ (Projects → API Keys)",
      },
      {
        dt: "Endpoint",
        dd: "POST /v1/chat/completions — รูปแบบคำขอและคำตอบตรงตาม OpenAI",
      },
      {
        dt: "โมเดล",
        dd: 'ระบุโมเดลที่องค์กรเปิดใช้ หรือใช้ "auto" ให้ระบบเลือกตามค่าใช้จ่ายและเวลาตอบ พร้อมการสำรองสองชั้น',
      },
      {
        dt: "ผลการตัดสิน",
        dd: "ทุกคำตอบแนบ policy_action และ policy_reasons บอกว่านโยบายทำอะไรกับคำขอ (mask / block / allow) และเพราะเหตุใด",
      },
    ],
    refBaseUrl: " — gateway ที่องค์กรของคุณติดตั้งเอง ไม่มี endpoint สาธารณะกลาง",
    surfacesH: "ครอบคลุม 9 กลุ่มความสามารถ",
    surfacesP:
      "ทุกกลุ่มอยู่หลัง gateway เดียวกันและอยู่ใต้นโยบายปิดบังข้อมูลชุดเดียวกัน:",
    responsesP:
      "คำตอบตรงตาม schema ของ OpenAI ดังนั้น SDK และเครื่องมือเดิมใช้ได้โดยไม่ต้องแก้ ข้อผิดพลาดตอบกลับเป็น HTTP status มาตรฐานพร้อม JSON error",
    contactH: "มีคำถาม?",
    contactP: "ติดต่อทีมงานได้ที่ ",
  },
  en: {
    eyebrow: "Developer docs",
    title: "Start sending requests through the gateway in minutes",
    subtitle:
      "SRAN AI Gateway is OpenAI-compatible — point your SDK at your organisation's gateway base URL and existing code keeps working.",
    quickstartH: "Quickstart",
    quickstartP:
      "Change two things — the base URL and your API key — and every request passes through the gateway's masking, routing and audit pipeline automatically.",
    copyLabel: "Copy",
    copiedLabel: "Copied",
    refH: "Reference",
    refP: "The essentials to get a first request through:",
    refRows: [
      {
        dt: "Base URL",
        dd: null,
      },
      {
        dt: "Authentication",
        dd: "Authorization: Bearer header with an API key created in your project (Projects → API Keys).",
      },
      {
        dt: "Endpoint",
        dd: "POST /v1/chat/completions — OpenAI-compatible request and response shape.",
      },
      {
        dt: "Models",
        dd: 'Name a model your organisation has enabled, or pass "auto" to let the router select by cost and latency with two layers of fallback.',
      },
      {
        dt: "Policy result",
        dd: "Every response carries policy_action and policy_reasons — what the policy did with the request (mask / block / allow) and why.",
      },
    ],
    refBaseUrl:
      " — your organisation's own deployment; there is no shared public endpoint.",
    surfacesH: "Nine capability surfaces",
    surfacesP:
      "All of them sit behind the same gateway and the same masking policy:",
    responsesP:
      "Responses match the OpenAI schema, so existing SDKs and tools work unchanged. Errors return standard HTTP status codes with a JSON error body.",
    contactH: "Questions?",
    contactP: "Reach the team at ",
  },
} as const;

export default function DocsContent() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <PageShell eyebrow={t.eyebrow} title={t.title} subtitle={t.subtitle}>
      <div>
        <h2>{t.quickstartH}</h2>
        <p>{t.quickstartP}</p>
        <CodeTabs
          tabs={QUICKSTART}
          copyLabel={t.copyLabel}
          copiedLabel={t.copiedLabel}
        />
      </div>

      <div>
        <h2>{t.refH}</h2>
        <p>{t.refP}</p>
        <dl className="docs-ref">
          {t.refRows.map((row) => (
            <div className="docs-ref-row" key={row.dt}>
              <dt>{row.dt}</dt>
              <dd>
                {row.dd ?? (
                  <>
                    <code>https://gateway.your-org/v1</code>
                    {t.refBaseUrl}
                  </>
                )}
              </dd>
            </div>
          ))}
        </dl>
        <p>{t.responsesP}</p>
      </div>

      <div>
        <h2>{t.surfacesH}</h2>
        <p>{t.surfacesP}</p>
        <p>
          {SURFACES.map((s, i) => (
            <span key={s}>
              <code>{s}</code>
              {i < SURFACES.length - 1 ? " · " : ""}
            </span>
          ))}
        </p>
      </div>

      <div>
        <h2>{t.contactH}</h2>
        <p>
          {t.contactP}
          <a href="mailto:info@gbtech.co.th">info@gbtech.co.th</a>
        </p>
      </div>
    </PageShell>
  );
}
