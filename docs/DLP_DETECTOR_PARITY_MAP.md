# DLP Detector Parity Map — Python ↔ Rust

Audit date: 2026-05-30. Scope: `backend/app/services/dlp/detectors/*.py` (registered in
`registry.py:272-344`) vs `dlp-sidecar/src/detectors/*.rs` (registered in `registry.rs:24-64`).
Goal: verify the Rust port replicates the Python detectors byte-for-byte on regex patterns,
checksums, context gating, and entity/category/severity/priority metadata before the production
DLP engine cutover.

Severity legend (Rust integer ↔ Python `SensitivityLevel`): 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL.

## Parity table

| Detector | Category | Method | Python file:line | Rust file:line | Verdict | Notes |
|---|---|---|---|---|---|---|
| email | pii | regex | email.py:12-14 | email.rs:11-13 | ✅ | Pattern identical. entity `email_address`, MEDIUM. |
| phone | pii | regex + context + heuristic | phone.py:33-167 | phone.rs:13-134 | ⚠️ | All 4 regexes + context window + dedup + sort match. **Missing the all-same-digit reject** (`len(set(digits_only))==1`, phone.py:106-108). Rust never drops `000-000-0000` / `111-111-1111`. Also Rust has no `from_decoded` branch (phone.py:156-158), but that path is only used for base64-decoded sub-scans, not the main scan. |
| ssn | pii | regex + validation | ssn.py:25-43 | ssn.rs:14-62 | ✅ | Pattern + SSA area/group/serial rules identical. entity `us_ssn`, HIGH. |
| credit_card | financial | regex + Luhn | credit_card.py:19-49 | credit_card.rs:13-83 | ✅ | Pattern + mod-10 Luhn + 13-19 digit bounds identical. entity `credit_card_number`, HIGH. |
| iban | financial | regex + mod-97 | iban.py:17-58 | iban.rs:12-99 | ✅ | Pattern + normalize + length 15-34 + CC/check-digit guard + chunked mod-97 (residue==1) identical. HIGH. |
| ip_address (v4) | network | regex | ip_address.py:25-28 | ip_address.rs:12-17 | ✅ | Octet pattern + lookbehind/lookahead identical. entity `ipv4_address`, MEDIUM. |
| ip_address (v6) | network | regex + post-filter | ip_address.py:32-43, 67-83 | ip_address.rs:19-33, 79-97 | ✅ | 6-alternation IPv6 pattern + `':' in value` post-filter identical. entity `ipv6_address`, MEDIUM. |
| passport | identity | regex (context-gated) | passport.py:24-51 | passport.rs:14-27 | ✅ | Keyword set + joiner + `(?P<value>[A-Z]{1,2}\d{6,9}|\d{9})` identical. entity `passport_number`, HIGH. |
| date_of_birth | pii | regex (context-gated) | date_of_birth.py:53-76 | date_of_birth.rs:12-37 | ⚠️ | **Rust omits the person-role-noun keyword branch** `(?:ลูกค้า\|ผู้ป่วย\|คนไข้\|สมาชิก\|พนักงาน\|ผู้ใช้)\s*(?:วันเกิด\|เกิดวันที่)` (dob.py:65). Date sub-patterns + `(?<![฀-๿])` lookbehind otherwise identical. |
| vehicle_plate | identity | regex (keyword/province-gated) | vehicle_plate.py:27-53 | vehicle_plate.rs:12-33 | ✅ | Province list, plate body, keyword + province patterns, dedup identical. MEDIUM. |
| bearer_token | secret | regex (5 sub-patterns) | bearer_token.py:29-37 | bearer_token.rs:21-48 | ⚠️ | All 5 patterns char-identical. **`re.ASCII` flag on the Bearer pattern is not reproduced** in Rust (no equivalent). Practically inert (Bearer tokens are ASCII), but `\b`/`\s` semantics differ at non-ASCII edges. entity per-pattern, CRITICAL. |
| api_key_secret | credential | regex (8 standalone + 1 assignment) | api_key_secret.py:24-108 | api_key_secret.rs:19-53 | ❌ | **Rust ships only 6 of the 8 standalone patterns** — the two `github_pat` patterns (`ghp_[A-Za-z0-9]{36}` and `github_pat_[A-Za-z0-9_]{82}`, api_key_secret.py:68-82) are **missing**. Anthropic-long/short, openai proj/live, google, jwt + assignment all present and identical. `_DECODED_PATTERNS` (sk-ant short, from_decoded only) also absent, but that path is decode-only. |
| private_key | secret | regex | private_key.py:25-27 | private_key.rs:8-11 | ✅ | PEM header pattern identical. CRITICAL. |
| password_field | secret | regex (3 patterns) | password_field.py:38-83 | password_field.rs:14-22 | ❌ | **Rust ships only `_PASSWORD_RE`** (label+separator). The Thai variant `_THAI_PASSWORD_RE` (`รหัสผ่าน`/`พาสเวิร์ด` + value, pw.py:56-60) and the English no-separator variant `_PASSWORD_NOSEP_RE` (`password <value>` with entropy lookaheads, pw.py:75-83) are **missing**. HIGH. |
| thai_national_id | identity | regex + mod-11 checksum + keyword gate | thai_national_id.py:26-145 | thai_national_id.rs:66-146 | ⚠️ | Gate-1 checksum path identical. **Gate-2 keyword path diverges**: (a) Rust keyword set omits `บัตร\s*ปชช\.?`, `เลข\s*ปชช\.?`, `หมายเลข\s*บัตร`, `เลข\s*13\s*หลัก`, `N\.?I\.?D\.?`; uses `บัตรประชาชน`/`เลขบัตร` without the `\s*` Python has (`บัตร\s*ประชาชน`/`เลข\s*บัตร`). (b) separator class Rust `[:#]?` vs Python `[:#.]?` (no `.`). (c) **digit pattern Rust `(\d{13})` vs Python `(?:\d[-\s]?){12}\d`** — Rust keyword path can't match dashed/spaced IDs. HIGH, priority 10. |
| thai_tax_id | identity | regex + mod-11 + context gate | thai_tax_id.py:17-138 | thai_tax_id.rs:18-89 | ❌ | Three divergences. (1) **Checksum: Python `_thai_tax_id_valid` does NOT reject leading-zero** (corporate IDs start with 0); Rust reuses `is_valid_thai_id` which **rejects leading 0** (thai_national_id.rs:53) → all `0…` corporate tax IDs are false-negatives. (2) Keyword set: Rust has 7, Python has 14 — missing `เลขผู้เสียภาษี`, `เลขทะเบียนนิติบุคคล`, `เลขนิติบุคคล`, `tax id number`, `taxpayer number`, `tax registration`, `vat registration`. (3) **Checksum-bypass path missing**: Python `_has_strong_tax_prefix` (30-char window, strong labels) emits checksum-invalid IDs; Rust has no such path. HIGH, priority 10. |
| thai_bank_account | financial | regex + context gate | thai_bank_account.py:19-74 | thai_bank_account.rs:15-89 | ✅ | Pattern, 10-12 digit count, ±60 window, 9-keyword list identical. HIGH, priority 10. |
| thai_promptpay | financial | regex + checksum + context gate | thai_promptpay.py:19-73 | thai_promptpay.rs:18-84 | ✅ | 10-digit mobile / 13-digit ID pattern, ±80 window, 3 keywords, mod-11 on 13-digit branch identical. MEDIUM, priority 10. |
| thai_address | pii | regex (keyword-anchored) | thai_address.py:21-54 | thai_address.rs:12-60 | ✅ | Two-group pattern, span = g1.start..g2.end, `len<6` strip-reject identical. MEDIUM. |
| address | pii | regex + inner address-like check | address.py:15-67 | address.rs:13-88 | ✅ | Label + value pattern, address-like sub-pattern, lstrip span recompute identical. MEDIUM. |
| income | financial | regex (label-gated) | income.py:14-81 | income.rs:11-75 | ❌ | Three divergences. (1) **Label set: Rust omits `wage\|wages\|pay` and Thai `ค่าจ้าง`, `ค่าตอบแทน`** (income.py:18,24-25). (2) **Entire `_OPTIONAL_PRONOUN` block missing** (income.py:34-40: `ฉัน\|ของฉัน\|ผม\|หนู\|เรา\|my\|of mine`) → "เงินเดือนฉัน 85,000" / "salary my 50000" won't match. (3) per-month tail Rust `(?:/\|per\s+)?` omits Thai `ต่อ` (income.py:46). value pattern + span logic otherwise identical. HIGH. |
| thai_name | pii | regex (prefix-gated) + FP filter | thai_name.py:21-91 | thai_name.rs:18-112 | ✅ | THAI_CHAR class `[ก-ฺเ-๎]` = U+0E01-0E3A/U+0E40-0E4E reproduced exactly. Title prefixes, English prefixes, false-positive filter, anchored `.match()`→`find().start()==0` semantics, dedup identical. HIGH. |
| thai_name_no_prefix | pii | regex (context-gated) + cursor walk | thai_name_no_prefix.py:44-146 | thai_name_no_prefix.rs:19-138 | ⚠️ | Pattern, lookbehind/lookahead negative-keyword guard, ±25 outside-window context, one-codepoint cursor advance all faithfully ported. **Registration gate differs** (see Risk summary): Python registers this ALWAYS (registry.py:323); Rust registers it ONLY when `ner_enabled=false` (registry.rs:60-61). MEDIUM, priority -5. |
| db_connection_string | credential | regex | db_connection_string.py:34-39 | db_connection_string.rs:12-17 | ✅ | Connection-string pattern identical. CRITICAL. |
| medical_record | pii | regex + keyword `\b` gate | medical_record.py:18-103 | medical_record.rs:15-99 | ✅ | Number pattern, 11-keyword `\b`-anchored alternation, ±60 lead-in filter (keyword.end≤num_start AND keyword.start≥num_start-60) replicated correctly. HIGH, priority 10. |
| religion_belief | pii | regex + personal-context + merge | religion_belief.py:21-115 | religion_belief.rs:16-123 | ✅ | Keyword regex, personal-context regex, ±150 window, `_merge_adjacent` (overlap merge by start≤prev.end) identical. HIGH. |
| health_condition | pii | regex (2-stage) + condition check | health_condition.py:15-151 | health_condition.rs:16-151 | ⚠️ | Labelled stage (tempered-greedy value + field-boundary lookahead), condition-value regex, ±150 keyword stage, dedup all match. **Rust keyword regex omits `ภูมิแพ้`, `โรคภูมิแพ้`, `allergy`, `allergic`** (health_condition.py:24-25). HIGH. |
| criminal_record | pii | regex + personal-context | criminal_record.py:15-83 | criminal_record.rs:13-88 | ✅ | Keyword regex, personal-context regex, ±150 window, dedup identical. HIGH. |
| biometric_data | pii | regex + personal/action-context | biometric_data.py:14-79 | biometric_data.rs:11-83 | ✅ | Keyword regex, personal/action-context regex, ±150 window, dedup identical. HIGH. |
| presidio_ner | pii (NER) | spaCy/Presidio | presidio_ner.py | — | ❌ (intentional) | Python-only. Explicitly NOT ported (registry.rs:1-22 comment). `thai_name_no_prefix` is the Rust fallback for bare names. |

Detector count: 29 ported entities (+ Presidio NER Python-only). 18 ✅ full match, 7 ⚠️ difference, 4 ❌ missing-logic.

## Risk summary (every ⚠️ / ❌)

Ordered by detection-impact severity.

1. **thai_tax_id — leading-zero checksum rejects all corporate tax IDs (❌, false-negative).**
   Python `_thai_tax_id_valid` (thai_tax_id.py:22-33) intentionally drops the leading-zero check
   because Thai corporate/juristic tax IDs are issued in the `0xxxxxxxxxxxx` range. Rust reuses
   `is_valid_thai_id` (thai_national_id.rs:41-64) which returns `false` when `bytes[0]==b'0'`.
   Result: **every corporate tax ID beginning with 0 (the common case) is never masked.** This is
   the highest-impact divergence — a hard false-negative on a whole class of sensitive identifiers.

2. **thai_tax_id — checksum-bypass strong-prefix path missing (❌, false-negative).**
   Python emits checksum-INVALID 13-digit runs when a strong tax label sits within 30 chars before
   them (`_has_strong_tax_prefix`, thai_tax_id.py:83-95, 119-125). Rust unconditionally requires
   `is_valid_thai_id`. Fake / typo'd / masked tax IDs in explicitly-labelled fields leak under Rust.

3. **thai_tax_id — keyword set roughly halved (❌, false-negative).**
   Rust 7 keywords vs Python 14. Missing `เลขผู้เสียภาษี`, `เลขทะเบียนนิติบุคคล`, `เลขนิติบุคคล`,
   `tax id number`, `taxpayer number`, `tax registration`, `vat registration`. Numbers labelled
   only by these phrases are not gated in → never masked.

4. **password_field — two of three sub-detectors missing (❌, false-negative).**
   Rust only implements the strict `label[:=]value` form. The Thai prose form (`รหัสผ่านคือ …`,
   `พาสเวิร์ด …`) and the English no-separator form (`password hunter2!`, `password smtp_pass_123`)
   are absent. Any password written in Thai prose, or in English without a `:`/`=`, leaks. These
   variants were added specifically to close the May-2026 audit gaps; the port regressed them.

5. **api_key_secret — both GitHub PAT patterns missing (❌, false-negative).**
   Classic `ghp_<36>` and fine-grained `github_pat_<82>` (api_key_secret.py:68-82) are not in the
   Rust standalone set. GitHub personal-access tokens pass through unmasked under Rust. (Note: these
   are the only two Python patterns using lookbehind/lookahead boundaries — see Regex-feature risks.)

6. **income — label set + optional-pronoun + Thai per-month tail all reduced (❌, false-negative).**
   Rust label set drops `wage|wages|pay` and Thai `ค่าจ้าง`/`ค่าตอบแทน`; the `_OPTIONAL_PRONOUN`
   bridge between label and value is entirely absent. Common phrasings — "wage 45000", "เงินเดือนฉัน
   85,000", "salary my 50000", "ค่าจ้าง 30000" — are false-negatives. The missing `ต่อ` in the tail
   only shortens the masked span end (low impact) when the other paths do match.

7. **date_of_birth — person-role-noun keyword branch missing (⚠️, false-negative).**
   Rust drops `(?:ลูกค้า|ผู้ป่วย|คนไข้|สมาชิก|พนักงาน|ผู้ใช้)\s*(?:วันเกิด|เกิดวันที่)` (dob.py:65).
   DOBs phrased as "ลูกค้าเกิดวันที่ 12/05/1998" (which the bare-`เกิด` lookbehind branch deliberately
   rejects to avoid `เหตุเกิด`/`ประชุมเกิด` FPs) are no longer recovered → false-negative.

8. **thai_national_id — Gate-2 keyword path weaker (⚠️, false-negative).**
   The checksum path (Gate 1) is identical, so any checksum-valid ID is still caught regardless.
   Divergence is confined to the keyword-bypass path for checksum-INVALID IDs: fewer keywords,
   `[:#]?` vs `[:#.]?` separator, and `(\d{13})` vs `(?:\d[-\s]?){12}\d` (Rust can't match dashed
   `1-1234-56789-01-0` or spaced forms via the keyword path). Net effect: checksum-invalid /
   placeholder IDs in labelled-but-dashed fields leak under Rust.

9. **health_condition — 4 keywords missing (⚠️, false-negative).**
   `ภูมิแพ้`, `โรคภูมิแพ้`, `allergy`, `allergic` absent from the Rust keyword stage. Allergy
   disclosures with personal context (and not also caught by the labelled stage / `allerg` in the
   condition-value regex) are false-negatives. Partial mitigation: the labelled stage's
   `_CONDITION_VALUE_RE` still contains `allerg` and `ภูมิแพ้`, so `Medical: allergy` style inputs
   are still caught; only the bare keyword-stage path regresses.

10. **phone — all-same-digit reject missing (⚠️, false-positive).**
    Python suppresses `000-000-0000`, `111-111-1111`, etc. (phone.py:99-108) so example/sample
    numbers in prompts aren't masked. Rust masks them → false-POSITIVE relative to Python. Low data
    risk (over-masking), but it changes shadow-compare counts and can mangle "explain this format"
    prompts.

11. **bearer_token — `re.ASCII` flag not reproduced (⚠️, negligible).**
    Python compiles the Bearer pattern with `re.ASCII`, forcing `\s`/`\b` to ASCII. Rust/fancy-regex
    `\b`/`\s` are Unicode-aware. Only observable if a non-ASCII char abuts a Bearer token; in
    practice Bearer values are ASCII so no expected detection delta. Flagged for completeness.

12. **thai_name_no_prefix — registration gate differs (⚠️, behavioural, not regex).**
    Python's current registry registers this detector ALWAYS (registry.py:307-323; the old
    `not ner_enabled` gate was deliberately removed because the backend image ships only
    `en_core_web_sm` with no Thai NER). Rust still gates it on `!ner_enabled` (registry.rs:60-61) and
    its module docstring describes the OLD Python behaviour. **When `ner_enabled=true`, Python runs
    `thai_name_no_prefix` (as a priority -5 floor) but Rust does not** → bare Thai names that
    Presidio misses are false-negatives under Rust in NER-on mode. The detector logic itself is a
    faithful port; only the composition gate is stale.

## Regex-feature risks (lookbehind / backreference / `\p{}` / verbose / flags)

Patterns where Python `re` and Rust `fancy-regex` could evaluate differently. The port correctly
routes every detector through `fancy-regex` (not the plain `regex` crate) so lookaround compiles;
the residual risks are semantic, not compile-time.

- **Fixed-width negative lookbehind `(?<!\d)`** — phone, ip_address (both), thai_national_id,
  thai_tax_id, thai_bank_account, thai_promptpay, medical_record. All fixed-width → fancy-regex
  supports them and behaviour matches Python. No risk observed.
- **Thai-range lookbehind `(?<![฀-๿])`** (date_of_birth) and `(?<!{THAI_CHAR})` (thai_name_no_prefix)
  — single-codepoint negative lookbehind. fancy-regex evaluates these correctly; the Thai range
  `฀-๿` = U+0E00–U+0E7F and the char class `[ก-ฺเ-๎]` were reproduced exactly. No risk, but these
  are the patterns most sensitive to any future char-class typo.
- **Lookbehind+lookahead boundaries `(?<![A-Za-z0-9])…(?![A-Za-z0-9])`** — the two GitHub PAT
  patterns in api_key_secret.py:73,80. These compile fine in fancy-regex; the risk here is moot
  because **the patterns were dropped from the Rust port entirely** (❌ item 5), not because of a
  feature gap.
- **Negative lookahead `(?!ant-)`** (bearer_token openai key) and `(?!(?:KW)(?:\s|$|[^THAI]))`
  (thai_name_no_prefix), plus value-entropy lookaheads `(?=\S{8,})(?=\S*[\d!@#…])`
  (password_field `_PASSWORD_NOSEP_RE`) — fancy-regex supports lookahead. The bearer and
  no-prefix ones are ported faithfully; the password entropy-lookahead pattern is **not ported**
  (❌ item 4), so no runtime feature risk, just missing coverage.
- **Tempered-greedy value capture** `(?:(?![.;\n\r]|,\s*[A-Z][a-z]+\s*[:：])[^\n\r]){3,160}`
  (health_condition labelled stage) — relies only on inline negative lookahead inside a repetition;
  fancy-regex handles this and the Rust copy is byte-identical. No risk.
- **No backreferences and no `\p{...}` Unicode properties** are used by any detector — both engines
  would need extra care for those, but neither Python nor Rust source relies on them. All Unicode
  ranges are written as explicit codepoint ranges/escapes, which removes the `\p{}`-semantics risk
  class entirely.
- **Verbose mode `(?ix)`** — only api_key_secret `_ASSIGNMENT_RE`. Rust correctly expands it to a
  single non-verbose `(?i)` pattern with no stray literal whitespace. Verified equivalent.
- **`\b`/`\s` Unicode vs ASCII** — Python defaults to Unicode `\b`; the only ASCII-forced pattern is
  bearer_token's Bearer rule (`re.ASCII`), not reproduced in Rust (⚠️ item 11, negligible). For the
  Thai-keyword `\b` gating (medical_record, religion, health) both engines treat Thai letters as
  word characters, so boundaries align.
