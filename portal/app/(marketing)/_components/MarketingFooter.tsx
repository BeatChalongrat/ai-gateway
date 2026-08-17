"use client";

import Image from "next/image";
import Link from "next/link";

import { useLang } from "./LanguageProvider";

const copy = {
  th: {
    about:
      "SRAN โดย บริษัท โกลบอลเทคโนโลยี อินทิเกรเทด จำกัด — แพลตฟอร์ม AI ที่มี DLP masking gateway เป็นแกนกลาง สำหรับทีมที่ดูแลข้อมูลส่วนบุคคลภายใต้ PDPA",
    address: "48/6 ซอยแจ้งวัฒนะ 14 แขวงทุ่งสองห้อง เขตหลักสี่ กรุงเทพฯ 10210",
    products: "ผลิตภัณฑ์",
    platform: "แพลตฟอร์ม",
    company: "บริษัท",
    howMasking: "การทำงานของ masking",
    securityPdpa: "ความปลอดภัยและ PDPA",
    deployment: "การติดตั้ง",
    contact: "ติดต่อเรา",
    privacy: "นโยบายความเป็นส่วนตัว",
    terms: "เงื่อนไขการใช้งาน",
    copyright: "© 2026 บริษัท โกลบอลเทคโนโลยี อินทิเกรเทด จำกัด",
  },
  en: {
    about:
      "SRAN by Global Technology Integrated Co., Ltd. — an enterprise AI platform built around a DLP masking gateway, for teams responsible for personal data under PDPA.",
    address: "48/6 Soi Chaeng Watthana 14, Thung Song Hong, Lak Si, Bangkok 10210",
    products: "Products",
    platform: "Platform",
    company: "Company",
    howMasking: "How masking works",
    securityPdpa: "Security and PDPA",
    deployment: "Deployment",
    contact: "Contact",
    privacy: "Privacy policy",
    terms: "Terms of use",
    copyright: "© 2026 Global Technology Integrated Co., Ltd.",
  },
} as const;

export function MarketingFooter() {
  const { lang } = useLang();
  const t = copy[lang];

  return (
    <footer className="marketing-footer">
      <div className="wrap">
        <div className="footer-grid">
          <div>
            <Link href="/" className="brand" aria-label="SRAN AI Gateway">
              <span className="brand-tile">
                <Image src="/sran-icon-white.png" alt="" width={23} height={25} />
              </span>
              <span className="brand-text">
                <span className="brand-name">SRAN</span>
                <span className="brand-tag">AI Gateway</span>
              </span>
            </Link>
            <p className="footer-about">{t.about}</p>
            <address className="footer-contact">
              {t.address}
              <br />
              <a href="tel:029825445">02-982-5445</a> ·{" "}
              <a href="tel:029825454">02-982-5454</a>
              <br />
              <a href="mailto:info@gbtech.co.th">info@gbtech.co.th</a>
            </address>
          </div>
          <div>
            <h4>{t.products}</h4>
            <ul>
              <li><a href="/#products">Web Chat</a></li>
              <li><a href="/#products">Desktop Agent</a></li>
              <li><a href="/#products">Admin Console</a></li>
              <li><a href="/#products">Gateway API</a></li>
            </ul>
          </div>
          <div>
            <h4>{t.platform}</h4>
            <ul>
              <li><a href="/#how">{t.howMasking}</a></li>
              <li><a href="/#security">{t.securityPdpa}</a></li>
              <li><a href="/#deploy">{t.deployment}</a></li>
            </ul>
          </div>
          <div>
            <h4>{t.company}</h4>
            <ul>
              <li><a href="/#contact">{t.contact}</a></li>
              <li><a href="/privacy">{t.privacy}</a></li>
              <li><a href="/terms">{t.terms}</a></li>
            </ul>
          </div>
        </div>
        <div className="footer-bottom">
          <span>{t.copyright}</span>
          <span>Built in Thailand</span>
        </div>
      </div>
    </footer>
  );
}
