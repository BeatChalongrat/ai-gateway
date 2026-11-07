"use client";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "เกี่ยวกับเรา",
    heading: "ความมั่นคงปลอดภัยไซเบอร์สัญชาติไทย ตั้งแต่ปี 2546",
    p1: "SRAN (Security Revolution Analysis Network) พัฒนาโดย บริษัท โกลบอลเทคโนโลยี อินทิเกรเทด จำกัด ก่อตั้งปี 2546 ด้วยประสบการณ์ด้านการประเมินความเสี่ยงและความปลอดภัยข้อมูลสารสนเทศกว่า 20 ปี และเป็นผู้พัฒนาผลิตภัณฑ์ Cyber Security ตัวแรกของประเทศไทย ภายใต้เครื่องหมายการค้า SRAN ที่มีแมววิเชียรมาศเป็นสัญลักษณ์",
    p2: "เราเชื่อว่าเทคโนโลยีที่ใช้ปกป้องข้อมูลของชาติ ควรเป็นสิ่งที่คนในชาติควบคุมได้เอง",
    facts: [
      { v: "2546", l: "ปีที่ก่อตั้งบริษัท" },
      { v: "20+", l: "ปีด้านความปลอดภัยข้อมูลสารสนเทศ" },
      { v: "รายแรก", l: "ผลิตภัณฑ์ Cyber Security ที่พัฒนาโดยคนไทย" },
    ],
  },
  en: {
    kicker: "About us",
    heading: "Thai-built cyber security since 2003",
    p1: "SRAN (Security Revolution Analysis Network) is developed by Global Technology Integrated Co., Ltd., founded in 2003 with more than 20 years in information-security and risk assessment. It was the first Thai-developed cyber security product, carrying the SRAN trademark and its Wichian Mat cat as the emblem.",
    p2: "We believe the technology that protects a nation's data should be something the nation can control itself.",
    facts: [
      { v: "2546", l: "Founded (2003)" },
      { v: "20+", l: "Years in information security" },
      { v: "1st", l: "Thai-developed cyber security product" },
    ],
  },
} as const;

export function About() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section section-grey" id="about">
      <div className="wrap about-grid">
        <div>
          <span className="kicker">{t.kicker}</span>
          <h2 className="section-h">{t.heading}</h2>
          <p>{t.p1}</p>
          <p>{t.p2}</p>
          <p className="about-slogan">Lower Cost, More Secure</p>
        </div>
        <div className="about-facts">
          {t.facts.map((f) => (
            <div key={f.l}>
              <div className="af-v">{f.v}</div>
              <div className="af-l">{f.l}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
