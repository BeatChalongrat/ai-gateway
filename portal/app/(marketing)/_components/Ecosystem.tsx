"use client";

import Image from "next/image";

import { useLang } from "./LanguageProvider";

// Slugs match the files in public/providers/. All 15 render as equal
// cards — no "coming soon" badges.
const PROVIDERS: Array<[slug: string, name: string]> = [
  ["openai", "OpenAI"],
  ["anthropic", "Anthropic"],
  ["google", "Google AI"],
  ["groq", "Groq"],
  ["deepseek", "DeepSeek"],
  ["xai", "xAI"],
  ["openrouter", "OpenRouter"],
  ["minimax", "MiniMax"],
  ["qwen", "Qwen"],
  ["kimi", "Kimi"],
  ["zai", "Z.AI"],
  ["mistral", "Mistral"],
  ["cohere", "Cohere"],
  ["perplexity", "Perplexity"],
  ["sran", "SRAN AI"],
];

const copy = {
  th: {
    kicker: "ระบบนิเวศ",
    heading: "ทำงานร่วมกับ AI ที่องค์กรของคุณใช้อยู่แล้ว",
    p: "นำคีย์ของคุณมาใช้เอง หรือตั้งค่าจากศูนย์กลางแล้วให้สิทธิ์เป็นรายโปรเจกต์",
  },
  en: {
    kicker: "Ecosystem",
    heading: "Works with the AI stack your organisation already uses",
    p: "Bring your own keys, or configure them centrally and grant access per project.",
  },
} as const;

export function Ecosystem() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section section-grey eco" id="ecosystem">
      <div className="wrap eco-grid">
        <div>
          <span className="kicker">{t.kicker}</span>
          <h2 className="eco-h">{t.heading}</h2>
          <p>{t.p}</p>
        </div>
        <div className="prov-field">
          {PROVIDERS.map(([slug, name]) => (
            <div className="prov-item" key={slug}>
              <Image
                src={`/providers/${slug}.png`}
                alt=""
                width={44}
                height={44}
                sizes="22px"
              />
              <span>{name}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
