-- Display Lifecycle (Phase 4A.2.4) — content_masked raw-at-rest strategy.
--
-- Until this phase ``content_masked`` held the restored, user-visible DISPLAY
-- text (raw PII at rest). When DLP_DISPLAY_LIFECYCLE_ENABLED=true, NEW assistant
-- writes no longer persist restored raw display: ``content_masked`` instead
-- holds either the tokenized canonical text (provider-safe) or a redacted
-- placeholder. The browser still sees the restored display LIVE during the
-- stream; reload uses restore-on-read (Vault V2) or a tokenized/redacted
-- fallback.
--
-- ``display_state`` classifies what ``content_masked`` actually holds, so the
-- read path, admin/export/search/share policy, and any future backfill can
-- distinguish raw-sensitive legacy rows from provider-safe canonical rows:
--
--   'legacy_display'  — content_masked holds restored RAW display (pre-feature
--                       rows AND flag-off writes). RAW-SENSITIVE.
--   'canonical_safe'  — content_masked holds the tokenized canonical text.
--   'redacted'        — content_masked holds a safe placeholder (non-complete row).
--
-- Backward-compatible: column is nullable-defaulted; existing rows default to
-- 'legacy_display' (correctly classified raw-sensitive). No backfill, no
-- content rewrite. ``content_masked`` is NOT renamed or altered.

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS display_state VARCHAR(32) NOT NULL DEFAULT 'legacy_display';
