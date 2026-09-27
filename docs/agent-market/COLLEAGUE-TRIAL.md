# Agent Market — the unassisted colleague trial (AM3)

The one test that decides whether Agent Market is self-serve: **a colleague
who did not build it installs an agent and produces a correct deliverable
from their own real file, with no help from anyone, while you watch and say
nothing.** Nothing in the test suites replaces it. This page is the script
for the person who watches.

## Before (10 minutes, without the colleague)

1. Pick the colleague: someone who does the work the agent does (invoices,
   expense claims, or the monthly close), has never seen Agent Market, and has
   a real file of their own from this month.
2. As an admin (Agent Market → จัดการ Agent Market):
   - **การเข้าถึง**: add their email to the list (department filled in), or set
     access to "ทุกคนในองค์กร".
   - **แผนกและงบ**: check their personal budget and their department's.
   - **แคตตาล็อก**: the agent they will use is "เผยแพร่" and its sample says
     "สำเร็จ".
3. Do not send them a link to the agent, a guide, or a hint. Tell them only:
   "เปิดแชท มีอะไรชื่อ Agent Market ลองใช้ทำงาน … จากไฟล์ของคุณเองดู ผม/ฉันจะนั่งดูเฉย ๆ ไม่ช่วย".
4. Start a timer and open a blank note for the log below.

## During — say nothing

- Do not answer questions, do not point, do not nod. If they ask you, say
  "ลองดูตามที่หน้าจอบอกครับ/ค่ะ".
- Stop the trial only if their real data is at risk (they are about to send
  a file that must not leave their desk) or they give up. Giving up is a result.
- Write down, in order, every moment they **stop, hesitate, go back, re-read,
  or say something aloud**. Quote them exactly. Note the screen and the time.
- If a question card appears, ask them (once, after they answered it) to read
  it aloud. Mark every card they hesitate on — that card is a defect.

## After — ask three questions

1. "ตรงไหนที่ไม่แน่ใจว่าต้องทำอะไรต่อ"
2. "ไฟล์ที่ได้ ถูกต้องไหม เอาไปใช้จริงได้เลยไหม ถ้าไม่ได้ ต้องแก้อะไร"
3. "ถ้าพรุ่งนี้ต้องทำอีก จะทำเองได้ไหม"

Then check the deliverable yourself against their source file (a few totals,
one line item, the dates). Correct means correct against their file, not
"looks plausible".

## The log (copy this table)

| Time | Screen | What they did / said (quote) | Stopped? | Why (your reading) |
|---|---|---|---|---|
| 0:00 | แชท | | | |

Result: finished with a correct deliverable · finished, deliverable wrong
(what) · gave up at (screen) · stopped by the watcher (why).

## What the builder can see without asking (read-only)

Whoever builds the next card can read what happened from the database — the
colleague's steps are all recorded:

```sql
-- the colleague's installs, runs, questions and problems (replace the email)
SELECT i.slug, i.created_at FROM agent_market.agent_installs i
  JOIN agent_market.org_members m ON m.user_id = i.user_id WHERE m.email = 'colleague@…';
SELECT r.kind, r.status, r.status_reason, r.created_at, r.finished_at, r.credits_spent
  FROM agent_market.agent_runs r JOIN agent_market.org_members m ON m.user_id = r.user_id
  WHERE m.email = 'colleague@…' ORDER BY r.created_at;
SELECT kind, payload->>'text', created_at FROM agent_market.agent_thread_items t
  JOIN agent_market.org_members m ON m.user_id = t.user_id WHERE m.email = 'colleague@…' ORDER BY rev;
SELECT text, step, step_error, created_at FROM agent_market.problem_reports p
  JOIN agent_market.org_members m ON m.user_id = p.user_id WHERE m.email = 'colleague@…';
```

Spend and runs per person during the trial: จัดการ Agent Market →
การใช้จ่าย (7 วัน).

## What to hand to the next card

The log, the deliverable they got, the file it broke (if any, with their
permission), and the list of every stop — in their words. That list is
worth more than any test output.
