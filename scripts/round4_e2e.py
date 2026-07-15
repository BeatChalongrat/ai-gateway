"""Round 4 E2E — verify NID detection + reduced hallucination."""

from __future__ import annotations

import io
import json
import os
import sys
import time

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

API = "http://localhost:8080/v1/chat/completions"
KEY = os.environ.get("GW_API_KEY")
if not KEY:
    sys.exit("GW_API_KEY is not set — export a gateway API key before running this script")
MODEL = "gpt-4o-mini"
nonce = int(time.time())

CASES = {
    # Case 2 — fraud analysis (was: 1 hallucination gap)
    "case2_fraud": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"วิเคราะห์ fraud ให้หน่อย (r4-{nonce}):\n"
                    "1. ลูกค้า สมชาย ใจดี email somchai@gmail.com "
                    "สั่ง MacBook Air M4 ยอด 45,000 บาท IP 203.150.45.12\n"
                    "2. ลูกค้า สมหญิง รักดี email somying@hotmail.com "
                    "สั่ง MacBook Air M4 ยอด 45,000 บาท IP 203.150.45.12\n"
                    "ทั้งคู่ใช้ IP เดียวกัน สินค้าเดียวกัน ยอดเท่ากัน น่าสงสัยไหม?"
                ),
            }
        ],
    },
    # Case 4 — secret protection (non-stream)
    "case4_secret_nonstream": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"ดู code นี้หน่อย (r4-{nonce}) มี hardcoded credentials:\n"
                    "api_key = 'sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901vwx234'\n"
                    "password = 'P@ssw0rd123!'\n"
                    "แนะนำวิธีแก้ให้หน่อย"
                ),
            }
        ],
    },
    # NID detection — valid checksum dashed form with Thai keyword
    "nid_thai_keyword": {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"ขอข้อมูลลูกค้า (r4-{nonce}) สมชาย ใจดี "
                    "บัตร ปชช. 1-1234-56789-01-4 "
                    "email contact@example.com"
                ),
            }
        ],
    },
}


def post(payload, stream=False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    if stream:
        payload["stream"] = True
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        r = requests.post(API, headers=headers, data=body, timeout=120, stream=True)
        chunks = []
        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8") if isinstance(line, bytes) else line
            if s.startswith("data: "):
                p = s[6:]
                if p == "[DONE]":
                    break
                try:
                    j = json.loads(p)
                    t = (j.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                    if t:
                        chunks.append(t)
                except Exception:
                    pass
        return {
            "rid": r.headers.get("X-Request-Id"),
            "stream": "".join(chunks),
        }
    r = requests.post(API, headers=headers, data=body, timeout=120)
    b = r.json()
    return {"rid": b.get("id"), "cached": b.get("cached"), "status": r.status_code}


print("=== Case 2 (fraud, non-stream) ===")
r = post(CASES["case2_fraud"])
print(json.dumps(r, ensure_ascii=False))

print("\n=== Case 4 (secret, non-stream) ===")
r = post(CASES["case4_secret_nonstream"])
print(json.dumps(r, ensure_ascii=False))

print("\n=== Case 4 (secret, STREAM) ===")
r = post(CASES["case4_secret_nonstream"], stream=True)
print("rid:", r["rid"])
print("contains sk-ant-api03-abc123:", "sk-ant-api03-abc123" in r["stream"])
print("contains P@ssw0rd123:", "P@ssw0rd123" in r["stream"])

print("\n=== NID Thai keyword ===")
r = post(CASES["nid_thai_keyword"])
print(json.dumps(r, ensure_ascii=False))
