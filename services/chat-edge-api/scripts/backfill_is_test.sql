-- FIX119 Part C2 — label rows that existed BEFORE migration 045.
--
-- NOT run by apply_migrations.sh and NOT run by FIX119: it changes existing
-- rows in bulk, which the owner decides on. Run it once, by hand, after
-- 045 is on the database and TEST_ACCOUNT_EMAIL_PATTERN is what you mean:
--
--   docker exec -i ai-gateway-postgres-webchat psql -U chatedge -d sran_ai \
--     -v pattern='@local$' -v ON_ERROR_STOP=1 < services/chat-edge-api/scripts/backfill_is_test.sql
--
-- Dry-run first: replace the three UPDATEs with the SELECT COUNT(*) forms
-- shown in the comments, or wrap the file in BEGIN; ... ROLLBACK;.
--
-- Rule (the same one provisioning applies from now on): a user whose email
-- matches :pattern is a test account; their conversations and messages
-- inherit the flag. Nothing else is touched. Idempotent.

\set pattern :pattern

-- users:  SELECT COUNT(*) FROM identity.users WHERE email ~ :'pattern' AND NOT is_test;
UPDATE identity.users
   SET is_test = true
 WHERE email ~ :'pattern'
   AND NOT is_test;

-- conversations:  SELECT COUNT(*) FROM chat.conversations c JOIN identity.users u ON u.id = c.user_id WHERE u.is_test AND NOT c.is_test;
UPDATE chat.conversations c
   SET is_test = true
  FROM identity.users u
 WHERE u.id = c.user_id
   AND u.is_test
   AND NOT c.is_test;

-- messages:  SELECT COUNT(*) FROM chat.messages_masked m JOIN chat.conversations c ON c.id = m.conversation_id WHERE c.is_test AND NOT m.is_test;
UPDATE chat.messages_masked m
   SET is_test = true
  FROM chat.conversations c
 WHERE c.id = m.conversation_id
   AND c.is_test
   AND NOT m.is_test;
