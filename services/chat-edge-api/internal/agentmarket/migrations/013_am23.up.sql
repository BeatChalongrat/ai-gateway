-- AM23 (§3.2) — the one line a document carries about how its commentary was written is the organisation's to
-- switch off: "the commentary was drafted by the organisation's AI assistant; every figure is from the source cited
-- beside it", in the closing block of a file whose words a model drafted. On by default, so nothing changes for an
-- organisation by this migration: the line simply replaces the one that named the model (AM22).
--
-- The sentence a file carries when the system's rules composed its words is not under this switch: it changes what
-- a reader should trust, and it stays whatever the organisation chooses.
ALTER TABLE agent_market.org_settings ADD COLUMN IF NOT EXISTS assistant_line boolean NOT NULL DEFAULT true;
