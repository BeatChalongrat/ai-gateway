"use client";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    heading: "ปรึกษาทีมงานก่อนติดตั้ง",
    p: "เราช่วยวางรูปแบบการติดตั้ง ปรับนโยบายให้ตรงกับข้อมูลที่องค์กรของคุณจัดเก็บจริง และพาทีมเริ่มใช้งานระบบ",
    call: "โทร",
    cta: "ติดต่อทีมงาน",
  },
  en: {
    heading: "Talk to us before you deploy",
    p: "We help plan the deployment, tune the policy to the data your organisation actually holds, and bring the team onto the system.",
    call: "Call",
    cta: "Contact us",
  },
} as const;

export function Contact() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="contact" id="contact">
      <div className="wrap contact-inner">
        <div>
          <h2>{t.heading}</h2>
          <p>{t.p}</p>
          <p className="contact-meta">
            {t.call} <a href="tel:029825445">02-982-5445</a> ·{" "}
            <a href="mailto:info@gbtech.co.th">info@gbtech.co.th</a>
          </p>
        </div>
        <a className="btn" href="mailto:info@gbtech.co.th">
          {t.cta}
        </a>
      </div>
    </section>
  );
}
