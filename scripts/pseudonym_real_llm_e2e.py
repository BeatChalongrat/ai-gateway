"""Real-LLM E2E driver — sends 5 cases through gateway, captures rids."""

from __future__ import annotations

import json
import sys
import time

import requests

API = "http://localhost:8080/v1/chat/completions"
KEY = "REMOVED_CREDENTIAL"
MODEL = "gpt-4o-mini"

CASES: dict[str, dict] = {
    "case1_basic_identity": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "สรุปข้อมูลลูกค้าให้หน่อย:\n"
                    "สมชาย ใจดี email somchai@gmail.com โทร 081-234-5678\n"
                    "สั่งซื้อยอด 45,000 บาท วันที่ 2026-05-20 สินค้า MacBook Air M4"
                ),
            }
        ],
    },
    "case2_fraud_analysis": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "วิเคราะห์ fraud ให้หน่อย:\n"
                    "1. ลูกค้า สมชาย ใจดี email somchai@gmail.com "
                    "สั่ง MacBook Air M4 ยอด 45,000 บาท IP 203.150.45.12\n"
                    "2. ลูกค้า สมหญิง รักดี email somying@hotmail.com "
                    "สั่ง MacBook Air M4 ยอด 45,000 บาท IP 203.150.45.12\n"
                    "ทั้งคู่ใช้ IP เดียวกัน สินค้าเดียวกัน ยอดเท่ากัน น่าสงสัยไหม?"
                ),
            }
        ],
    },
    "case3_multiturn": {
        "model": MODEL,
        "metadata": {"conversation_id": "real-llm-e2e-001"},
        "messages": [
            {
                "role": "user",
                "content": (
                    "ลูกค้า สมชาย ใจดี email somchai@gmail.com "
                    "สั่งของยอด 45,000 บาท"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "รับทราบข้อมูลลูกค้า สมชาย ใจดี (somchai@gmail.com) "
                    "ยอด 45,000 บาท"
                ),
            },
            {
                "role": "user",
                "content": "สมชาย เคยสั่งซื้อกี่ครั้ง?",
            },
        ],
    },
    "case4_secret_protection": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "ดู code นี้หน่อย มี hardcoded credentials:\n"
                    "api_key = 'sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901vwx234'\n"
                    "password = 'P@ssw0rd123!'\n"
                    "แนะนำวิธีแก้ให้หน่อย"
                ),
            }
        ],
    },
    "case5_income_calc": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "พนักงาน สมชาย ใจดี เงินเดือน 85,000 บาท "
                    "ขอกู้ 2,000,000 บาท ผ่อน 30 ปี ดอกเบี้ย 6.5% "
                    "คำนวณ ratio debt-to-income ให้หน่อย ผ่อนไหวไหม?"
                ),
            }
        ],
    },
}


def main() -> int:
    rids: dict[str, str | None] = {}
    for name, payload in CASES.items():
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            r = requests.post(
                API,
                headers={
                    "Authorization": f"Bearer {KEY}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                data=body_bytes,
                timeout=60,
            )
        except Exception as exc:
            print(f"{name}: REQUEST_EXC {exc}")
            rids[name] = None
            continue
        if r.status_code != 200:
            print(f"{name}: HTTP {r.status_code} body={r.text[:200]}")
            rids[name] = None
            continue
        body = r.json()
        rid = body.get("id")
        provider = r.headers.get("X-Gateway-Provider")
        print(f"{name}: {r.status_code} rid={rid} provider={provider}")
        rids[name] = rid
        time.sleep(0.5)

    print("\n--- request_ids ---")
    print(json.dumps(rids, indent=2))
    return 0 if all(rids.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
