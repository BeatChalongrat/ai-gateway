# Agent Market — handover, one page

Written 13 Oct 2026, before the first trial with an accountant. Facts as of that morning; the detail is
in the places named. The reports (`AM1_REPORT.md` … `AM16_REPORT.md`) are files in the repo's folder on
the host and are **not in git**.

## What exists

- **Where it runs:** one laptop, which is also live. Every door is loopback and https with a local CA
  (web chat `https://localhost:3002/chat`, admin app `:3000`). Nobody reaches it from another machine.
- **Who gets in:** Google sign-in; the allowed address domain is set to `gbtech.co.th`. Agent Market is "listed people
  only"; the list holds two admins (the owner and the walk account `qa@`). **No ordinary member has
  used it on live yet.**
- **Live images:** backend `67d9fc0` · chat-edge, runner, admin-ui `90fbe38` · web-chat `9a1af45`.
  Everything on `main` is pushed and live; nothing waits in the repo.
- **Live since 13 Oct, 10:58:** the admin's lists of who may receive documents (migration `011_am16b`).
  Both lists are empty, so no agent reaches anybody outside.

## The six agents

| Agent | Version | On live | Takes → gives | A model is used for |
|---|---|---|---|---|
| Bank reconciliation | 1.7.0 | published | bank statement + bank ledger → reconciliation, Excel | typed words only |
| Tax-invoice register (invoice processing) | 1.10.0 | published | tax invoices, receipts, e-Tax → purchase register, Excel | re-reading scans and photos |
| Expense audit | 1.9.0 | published | claims file + receipts → every claim checked, Excel | re-reading scanned receipts |
| Invoice ↔ PO matching | 1.7.0 | published | POs, goods receipts, invoices → each invoice passed / blocked / to decide, Excel | re-reading scanned invoices |
| Financial report + deck | 1.9.0 | published | GL or income statement, Excel → report (Word, PDF), deck, workbook | writing the commentary |
| Procurement | 1.1.0 | **not published** | a typed request → price comparison + purchase-request draft | reading the request and web pages |

Every figure is computed by scripts and rules; a model proposes a reading or writes words, and a
person confirms. A run waits for a person's answer, and for its model when the model is away.

## What an admin decides (admin app → Agent Market)

Access and budgets · catalogue (publish, samples) · per agent: may its documents leave (off for all),
are they masked (off for all), and to whom (nobody named, for the organisation or for any agent) · all
runs, reported problems, spend · every change in the audit log.

## Known broken or weak — and where it is written

1. **The in-house model can go away and nothing notices until 06:30.** Chat then waits ~40 s and
   another model answers; agent runs wait. It was away on 9 Oct, and again on 13 Oct from about 04:17
   until some time before 10:50 — the 06:30 check was red for it, and nobody has said why it went.
   `AM16_REPORT.md` §20 and §23, `AM15_REPORT.md` §12.
2. **A failed Agent Market smoke check pages nobody, and the live check runs as an admin** — it does
   not prove a member can reach an agent. `AM15_REPORT.md` §13.
3. **65 backend tests fail by name and 17 run nowhere.** `scripts/gate/backend_marked_failing.txt`;
   the owner's notes, "Added after AM-15".
4. **Behind OpenRouter the company that runs a model is not its maker** (Claude → Amazon, GPT →
   Microsoft); Anthropic's and OpenAI's own endpoints are refused by the account's own setting.
   `AM16_REPORT.md` §17, `OPERATIONS.md` "To whom".
5. **The recipient list binds Agent Market only.** Ordinary chat's fallback still picks any fallback
   model when the in-house one is away (its text is masked). `AM16_REPORT.md` §20.
6. **A run's card says its documents left, not to whom.** The admin's row names them. §20.
7. **The words inside generated files are Thai only** for five agents. The owner's written next card.
8. **A wait for budget is titled "Waiting for your answer".** `AM15_REPORT.md` §10.
9. **Procurement has no search provider on live**, so it stays unpublished. `AM12_REPORT.md`.
10. **CI has never run**; the gate is `scripts/gate.sh`, by hand, in pieces, refused on battery.
    `OPERATIONS.md`, last sections.

## Waiting on the owner

The whole list, in one block, is `AM16_REPORT.md` §22. In short: why the in-house model went away on
13 Oct · one install to upgrade · the accountant's account and place on the list · the desktop
checklists nobody has walked · direct keys, if a company should read a document without a router in
between.

## Where to start reading

`ARCHITECTURE.md` (how it is built, by card) · `OPERATIONS.md` (how it is run) · `TRIAL-WATCH.md`
(what to watch when a person uses it) · `REMOVAL.md` (how all of it comes out) · the newest report.
