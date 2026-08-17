"use client";

import { useLang } from "./LanguageProvider";

const copy = {
  th: [
    { v: "30+", l: "ตัวตรวจจับข้อมูล" },
    { v: "15", l: "ผู้ให้บริการโมเดล" },
    { v: "97%", l: "ความแม่นยำการปิดบัง" },
    { v: "4", l: "ทางเข้าใช้งาน" },
  ],
  en: [
    { v: "30+", l: "Data detectors" },
    { v: "15", l: "Model providers" },
    { v: "97%", l: "Masking accuracy" },
    { v: "4", l: "Ways in" },
  ],
} as const;

export function Stats() {
  const { lang } = useLang();

  return (
    <section className="stats">
      <div className="wrap">
        <div className="stats-grid">
          {copy[lang].map((s) => (
            <div className="stat reveal" key={s.v}>
              <div className="stat-v">{s.v}</div>
              <div className="stat-l">{s.l}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
