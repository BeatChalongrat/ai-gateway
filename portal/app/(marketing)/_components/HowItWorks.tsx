"use client";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    kicker: "การทำงาน",
    sub: "ทุกคำขอผ่านสามขั้นตอนเดียวกัน บังคับใช้ที่ backend ไม่ใช่ที่เบราว์เซอร์หรือ SDK",
    steps: [
      {
        h: "ตรวจจับและแทนที่",
        p: "ตัวตรวจจับ 30 รายการร่วมกับการรู้จำชื่อเฉพาะภาษาไทยสแกนคำขอ แล้วแทนค่าอ่อนไหวด้วยค่าแทนกลางที่คงที่ตลอดบทสนทนา",
      },
      {
        h: "ส่งต่อไปยังโมเดล",
        p: null, // rendered inline — contains a <code> element
      },
      {
        h: "คืนค่าให้ผู้เรียก",
        p: "ค่าแทนถูกแปลงกลับเป็นค่าจริงในคำตอบ รองรับการตอบแบบสตรีม และคงอยู่ข้ามเทิร์นผ่านคลังบทสนทนาบน Redis",
      },
    ],
    step2Pre: "ระบุผู้ให้บริการเอง หรือใช้โหมด ",
    step2Post: " ให้ระบบให้คะแนนตามค่าใช้จ่ายและเวลาตอบ พร้อมการสำรองสองชั้น",
    tstep1: "สิ่งที่คุณส่ง",
    tstep2: "สิ่งที่โมเดลได้รับ",
    tstep3: "คำตอบที่คุณได้รับ",
    sent: "ติดต่อลูกค้าที่",
    or: "หรือ",
    back: "ติดต่อกลับลูกค้าได้ที่",
    foot1: "ค่าที่ปิดบังสอดคล้องกันตลอดบทสนทนา และรองรับการตอบแบบสตรีม",
    foot2: "ผลการตัดสินแนบใน policy_action / policy_reasons",
    egressH: "สี่ช่องทางที่จำเป็นต้องเห็นข้อมูลจริง",
    egressP: "ความสามารถบางอย่างทำงานไม่ได้หากไม่เห็นข้อความจริง ช่องทางเหล่านี้กำหนดเป็นค่าคงที่ในโค้ด และมีด่านตรวจใน CI ที่ทำให้บิลด์ล้มเหลวหากมีช่องทางใหม่ที่ยังไม่ถูกจัดประเภท",
    egressBlocked: "credentials / secrets — บล็อกเสมอ แม้ในช่องทางเหล่านี้",
    egressAside:
      "องค์กรที่มีข้อกำหนดเข้มงวดกว่านี้ตั้งค่าให้ช่องค้นหา สื่อ คลังความรู้ และเสียง บล็อกทั้งหมดเป็นรายองค์กรได้ ส่วนการบันทึกเก็บเพียงจำนวนครั้งรายประเภท ไม่เก็บตัวข้อความ",
  },
  en: {
    kicker: "How it works",
    sub: "Every request follows the same three stages, enforced in the backend — not in the browser or the SDK.",
    steps: [
      {
        h: "Detect and replace",
        p: "Thirty built-in detectors plus Thai named-entity recognition scan the request and replace each sensitive value with a neutral placeholder, stable for the whole conversation.",
      },
      {
        h: "Route to a model",
        p: null,
      },
      {
        h: "Restore for the caller",
        p: "The placeholders resolve back to the real values in the reply. This works with streaming and holds across turns through the conversation vault in Redis.",
      },
    ],
    step2Pre: "Name a provider, or use ",
    step2Post: " and let the router score by cost and latency, with two layers of fallback when an endpoint fails.",
    tstep1: "What you send",
    tstep2: "What the model sees",
    tstep3: "What comes back",
    sent: "Contact the customer at",
    or: "or",
    back: "Reach the customer on",
    foot1: "Masked values stay consistent across a conversation and work with streaming",
    foot2: "The decision returns in policy_action / policy_reasons",
    egressH: "Four channels that require the real value",
    egressP: "Some capabilities cannot work without the original text. These are fixed as constants in the code, and a CI gate fails the build if a new one appears unclassified.",
    egressBlocked: "credentials / secrets — always blocked, even here",
    egressAside:
      "Organisations with stricter needs can set search, media, knowledge base and voice to block per tenant. Audit records keep only per-category counts, never the text.",
  },
} as const;

export function HowItWorks() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <section className="section" id="how">
      <div className="wrap">
        <div className="section-head section-head-center">
          <span className="kicker">{t.kicker}</span>
          <h2 className="section-h">Mask · Route · Restore</h2>
          <p className="section-sub">{t.sub}</p>
        </div>

        <div className="steps" id="steps">
          {t.steps.map((s, i) => (
            <div className="step" key={s.h} tabIndex={0}>
              <div className="step-num">{i + 1}</div>
              <h3>{s.h}</h3>
              <p>
                {s.p ?? (
                  <>
                    {t.step2Pre}
                    <code>&quot;auto&quot;</code>
                    {t.step2Post}
                  </>
                )}
              </p>
            </div>
          ))}
        </div>

        <div className="transcript reveal">
          <div className="transcript-bar">
            <span>POST /v1/chat/completions</span>
          </div>
          <div className="transcript-grid">
            <div className="tstep">
              <div className="tstep-head">
                <span className="tstep-tag">1</span>
                <span className="tstep-label">{t.tstep1}</span>
              </div>
              <p className="tstep-body">
                {t.sent} <span className="pii">somchai@test.com</span> {t.or}{" "}
                <span className="pii">081-234-5678</span>
              </p>
            </div>
            <div className="tstep tstep-masked">
              <div className="tstep-head">
                <span className="tstep-tag">2</span>
                <span className="tstep-label">{t.tstep2}</span>
              </div>
              <p className="tstep-body">
                {t.sent} <span className="masked-val">•••••••••••</span> {t.or}{" "}
                <span className="masked-val">•••••••••</span>
              </p>
            </div>
            <div className="tstep">
              <div className="tstep-head">
                <span className="tstep-tag">3</span>
                <span className="tstep-label">{t.tstep3}</span>
              </div>
              <p className="tstep-body">
                {t.back} <span className="pii">somchai@test.com</span> {t.or}{" "}
                <span className="pii">081-234-5678</span>
              </p>
            </div>
          </div>
          <div className="transcript-foot">
            <span>{t.foot1}</span>
            <span>{t.foot2}</span>
          </div>
        </div>

        <div className="egress reveal">
          <div>
            <h3>{t.egressH}</h3>
            <p>{t.egressP}</p>
            <div className="egress-ch">
              <i>web_search</i> · <i>media_gen</i> · <i>embeddings</i> ·{" "}
              <i>voice</i>
              <br />
              <span className="egress-blocked">{t.egressBlocked}</span>
            </div>
          </div>
          <aside>{t.egressAside}</aside>
        </div>
      </div>
    </section>
  );
}
