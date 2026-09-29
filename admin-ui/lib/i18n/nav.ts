// Sidebar navigation — section titles + item labels.
//
// Kept separate from common.ts because the nav is the one surface where a
// wrong translation is immediately obvious to every admin, and because
// Sidebar.tsx builds its NAV_SECTIONS array at module scope (outside the
// React tree), so it stores KEYS and resolves them at render time.

export const en = {
  "nav.section.overview": "Overview",
  "nav.section.infrastructure": "Infrastructure",
  "nav.section.tenancy": "Tenancy",
  "nav.section.portal": "Portal",
  "nav.section.observability": "Observability",
  "nav.section.webChat": "Web Chat",

  "nav.dashboard": "Dashboard",
  "nav.providers": "Providers",
  "nav.models": "Models",
  "nav.policies": "Policies",
  "nav.dlpDetectors": "DLP Detectors",
  "nav.routing": "Routing",
  "nav.fallback": "Fallback",
  "nav.dlpCoverage": "DLP coverage",
  "nav.search": "Search",
  "nav.tenants": "Tenants",
  "nav.projects": "Projects",
  "nav.apiKeys": "API Keys",
  "nav.portalUsers": "Users",
  "nav.portalSettings": "Settings",
  "nav.tokenPacks": "Token Packs",
  "nav.requests": "Requests",
  "nav.usage": "Usage & Cost",
  "nav.auditLog": "Admin Actions",
  "nav.alertEvents": "Alert events",
  "nav.aiReview": "AI review",
  "nav.chatAnalytics": "Chat Analytics",
  "nav.chatConversations": "Conversation Logs",
  "nav.chatUsers": "Chat Users",
  "nav.chatKnowledge": "Knowledge Bases",
  "nav.chatRetention": "Data Retention",
  // The product's name — the same in both languages (dict.test.ts lists it).
  "nav.agentMarket": "Agent Market",

  "nav.openMenu": "Open menu",
  "nav.closeMenu": "Close menu",
};

export const th: Record<keyof typeof en, string> = {
  "nav.section.overview": "ภาพรวม",
  "nav.section.infrastructure": "โครงสร้างระบบ",
  "nav.section.tenancy": "องค์กรและโปรเจกต์",
  "nav.section.portal": "พอร์ทัลผู้ใช้",
  "nav.section.observability": "การตรวจสอบระบบ",
  "nav.section.webChat": "เว็บแชท",

  "nav.dashboard": "แดชบอร์ด",
  "nav.providers": "ผู้ให้บริการ AI",
  "nav.models": "โมเดล",
  "nav.policies": "นโยบาย",
  "nav.dlpDetectors": "ตัวตรวจจับข้อมูลอ่อนไหว",
  "nav.routing": "การจัดเส้นทาง",
  "nav.fallback": "สายสำรอง",
  "nav.dlpCoverage": "ความครอบคลุม DLP",
  "nav.search": "การค้นหา",
  "nav.tenants": "องค์กร",
  "nav.projects": "โปรเจกต์",
  "nav.apiKeys": "API Key",
  "nav.portalUsers": "ผู้ใช้",
  "nav.portalSettings": "ตั้งค่า",
  "nav.tokenPacks": "แพ็กเกจโทเคน",
  "nav.requests": "คำขอใช้งาน",
  "nav.usage": "การใช้งานและค่าใช้จ่าย",
  "nav.auditLog": "ประวัติการทำงานของผู้ดูแล",
  "nav.alertEvents": "การแจ้งเตือน",
  "nav.aiReview": "รีวิวโดย AI",
  "nav.chatAnalytics": "สถิติการแชท",
  "nav.chatConversations": "บันทึกบทสนทนา",
  "nav.chatUsers": "ผู้ใช้เว็บแชท",
  "nav.chatKnowledge": "ฐานความรู้",
  "nav.chatRetention": "การเก็บรักษาข้อมูล",
  "nav.agentMarket": "Agent Market",

  "nav.openMenu": "เปิดเมนู",
  "nav.closeMenu": "ปิดเมนู",
};
