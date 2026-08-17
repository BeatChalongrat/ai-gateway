import type { Metadata } from "next";

import { About } from "./_components/About";
import { Contact } from "./_components/Contact";
import { Deploy } from "./_components/Deploy";
import { Ecosystem } from "./_components/Ecosystem";
import { Featured } from "./_components/Featured";
import HashScrollHandler from "./_components/HashScrollHandler";
import { Hero } from "./_components/Hero";
import { HowItWorks } from "./_components/HowItWorks";
import { Security } from "./_components/Security";
import { Solutions } from "./_components/Solutions";
import { Stats } from "./_components/Stats";
import { Surfaces } from "./_components/Surfaces";
import { UseCases } from "./_components/UseCases";

export const metadata: Metadata = {
  title: "SRAN AI Gateway — ใช้ AI ในองค์กรโดยข้อมูลไม่ออกไปข้างนอก",
  description:
    "SRAN AI Gateway ปิดบังข้อมูลอ่อนไหวก่อนคำขอไปถึงโมเดล AI ภายนอก และคืนค่าเมื่อคำตอบกลับมา — ติดตั้งบนโครงสร้างพื้นฐานขององค์กรเอง รองรับ PDPA",
};

export default function LandingPage() {
  return (
    <>
      <HashScrollHandler />
      <Hero />
      <Solutions />
      <Stats />
      <Featured />
      <UseCases />
      <HowItWorks />
      <Surfaces />
      <Security />
      <Ecosystem />
      <Deploy />
      <About />
      <Contact />
    </>
  );
}
