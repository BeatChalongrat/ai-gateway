# What each surface tells the model

FIX107 Part C. This is a report only: nothing here was changed or unified
beyond FIX107 Part B. The owner has parked the prompt register deliberately,
and this list is evidence for that work.

The desktop's system prompt is smaller than the web's:

- **Desktop:** about 1,855 estimated tokens (`docs/MODEL_CONTEXT_WINDOWS.md`, FIX105).
- **Web, an ordinary turn:** about 2,880 tokens after FIX112 (was 5,398 on the
  same kind of turn before it: the three Office plan contracts went out on
  every turn). Measured with chat-edge's own estimator (`system_prompt_tokens`
  in `/context-info`), qwen3.8-27b-fp8, the tool policy included.
- **Web, a turn that asks for an Office file:** about 3,575–3,801 tokens — the one
  contract that kind needs is added back.

All are measurements of specific conversations, not a fixed size. The
different table format FIX107 B2 fixed was one visible result of that gap.

Read on 2026-09-11 at the FIX107 tree; re-read on 2026-09-14 at the FIX112
tree (FIX109–FIX112 landed in between). Corrections made on the re-read are
marked **FIX112** below.

**Web:** `services/chat-edge-api/internal/service/`.
- `PromptCompiler.Build` (`prompt_compiler.go`) joins its layers.
- `buildSystemPrefixWithSources` (`message_service.go`) adds the canvas and
  plan instructions.
- The context compiler appends `<context_note>`.

**Desktop:** `apps/desktop/src/main/`.
- `baseSystemPrompt` and `buildSystemPromptParts` (`prompt-assembler.ts`) build the prompt.
- `context-compiler.ts` adds the context note.
- `turn-coordinator.ts` adds the image-mode and quick-ask hints.

## Side by side

**Pinned** means a test reads the other tree and fails on drift.

| Topic | Web | Desktop | Shared? |
|---|---|---|---|
| **Identity** | `<identity>`: "You are SRAN Chat, an AI assistant built by SRAN (sran.ai)…". A tenant can override it. | "You are SRAN Assistant, an AI desktop assistant…", with a separate no-tools sentence. | No. The two names differ. |
| **Date and time** | "Current date and time: … Asia/Bangkok (UTC+7). Treat this as the present moment…" | None. | Web only |
| **Language and tone** | `prompt_language_rules.txt` | `LANGUAGE_RULES_BLOCK` | **Pinned** (`prompt-language.test.ts`) |
| **Deciding and asking** | `<action_rules>`: pick when asked to choose, ask at most one question, prefer doing over asking. Always sent. | "## Asking the user (rare)". One question, uses `ask_user_question`. Only when tools are on. Nothing about deciding for the user. | No; similar wording. **FIX112:** confirmed — the web has five bullets (including "choose when asked" and "prefer doing over asking"), the desktop three with the `ask_user_question` mechanics; the two shared ideas are worded differently. Not pinned; a gap for its own round |
| **Output format** | `<output_format>`: tables for comparisons, numbered lists for steps, prose for explanations. | "## Response Style" said only "Be concise and direct" and two lines about tools. **FIX107 B2: now also "## Output format" with the web's text.** | **Pinned since FIX107** (`prompt-output-format.test.ts`, `fix107_prompt_assets_test.go`) |
| **Artifact dialect** | `prompt_artifacts.txt` | `ARTIFACT_INSTRUCTION_WEBCHAT` | **Pinned** (`prompt-artifacts.test.ts`) |
| **Canvas revisions** | "## Canvas Artifacts": lists up to 5 artifacts and says to use `update_canvas_artifact` instead of a new block. | None | Web only |
| **Capabilities** | `<capabilities>`: a "You CAN" list (files, charts, diagrams, and whichever tools are active) and a "You CANNOT (be honest, never pretend you can)" list. | None. The tool definitions are all it has. | Web only |
| **Safety** | `<safety>`: offer an alternative instead of a bare refusal, don't invent facts, a not-a-professional note on medical/legal/financial topics. | "## Safety Rules": no credentials, no destructive commands or sending without confirmation, no purchases, account creation or permanent deletion. | Different rules |
| **Untrusted content** | The last two `<safety>` bullets | The same two sentences in Safety Rules | **Pinned** (`prompt_injection_parity_test.go`) |
| **Privacy** | "never VOLUNTEER a secret…", but return the user's own data when they ask. | "## Privacy", the same idea, plus a rule on what `remember` may store. | **FIX112: not identical, so not pinned.** The desktop's bullet adds "files, or web forms" to where a secret must not be carried (it has those tools) and its middle sentence is worded differently ("This protects their data from anyone else" vs "This is about not exposing their data to anyone else"). The shared tail matches. Left as two wordings on purpose rather than bent to fit a pin |
| **Tools** | `<tool_policy>`: one paragraph per active tool, and "Do NOT mention tool names to the user". | "## Tool Use Philosophy": "Acting is the default; describing is the exception." Nothing per tool. | Different |
| **Environment** | None | Granted folders by absolute path; the home folder is not a workspace; PowerShell 5.1 shell line. | Desktop only |
| **Unavailable tools** | Folded into the CANNOT list | "## Browser access is unavailable this turn" and "## Web search is unavailable" | Different |
| **Per-model guidance** | `<model_guidance>` per provider family | None | Web only |
| **Tenant settings** | `<tenant_rules>`, tone override, `<disabled_capabilities>` | None | Web only |
| **User custom instructions** | "## About the user" and "## Response style" | None | Web only |
| **Writing style** | None | "## Writing style" | Desktop only |
| **Project** | "## Project instructions" | "## Project: {name}" | Different headings |
| **Skills** | Matched skills, plus a catalogue loaded with `use_skill` | "## Active skills (MUST follow)": they override Response Style. No catalogue. | Different |
| **Saved memory** | `<saved_user_memory>`, with standing instructions and known facts | "## Memory (global)", merged with local notes | **Pinned since FIX112, two of three sentences** (`prompt_memory_frame.txt`, `fix112-prompt-pins.test.ts`): the standing-instructions lead and the closing sentence are one asset. The facts sentence is NOT shared — the desktop's says "(newest first)" because it caps and orders its list; the web's does not. Recorded, not bent |
| **Conversation notes** | None | "## Notes (this conversation)" | Desktop only |
| **Temporary chat** | `<temporary_chat>` with a "This is a temporary chat." lead-in | "## Temporary chat", the same three sentences | **Pinned since FIX112** (`prompt_temporary_chat.txt`, `fix112-prompt-pins.test.ts`, `fix112_prompt_assets_test.go`): the body is one asset; each surface wraps its own envelope |
| **Conversation summary** | `<conversation_state>` sent as a user/assistant pair | The same pair and block | **Pinned** (`state_contract.json`: `state-contract.test.ts` and `state_contract_test.go` compare the rendered block; `compaction-prompt.test.ts` pins the injection wording). **FIX112:** the register used to cite `compaction-prompt.test.ts` for the extraction prompt too — FIX111 deleted the desktop's copy of that prompt: the server is the one summariser now, the desktop asks it (`POST /compact`) and keeps a copy |
| **Context-trim note** | `<context_note>` | `<context_note>` | **Pinned since FIX107** (`prompt_context_note.txt`, `context-note.test.ts`, `state_contract.json` notes). Before FIX107 it was two hand-ported copies, and the desktop had no partial or failed wording. |
| **Knowledge base (RAG)** | "## Retrieved Knowledge Base Context", with `[Source: filename]` citations | None | Web only |
| **Attached files** | "## Typed untrusted file observations" in the system prompt | The same envelope, but in the user message | **Pinned since FIX112** (`fix112-prompt-pins.test.ts` reads `file_service.go`): header, marker, footer, the fixed fields and both use lists, in order. Still a different place |
| **Excel / Word-PDF / PowerPoint plans** | Long `:::excel-plan` / `:::doc-plan` / `:::pptx-plan` instructions. **FIX112:** sent only when an inspected file of that kind is in the conversation or the user's recent words ask for that kind of file (`office_intent.go`, Thai and English, the newest user message plus the two before it). Before FIX112 all three went out on every turn — 9,358 bytes, about 2,500 estimated tokens | None | Web only |
| **Image mode** | None (image turns skip the chat model) | An image-mode hint appended when image mode is on | Desktop only |
| **Quick-ask** | None | `QUICK_ASK_HINT`: no tools in this window | Desktop only |
| **Sub-agents** | None | The sub-agent system prompt | Desktop only |

## Only the web has

- The date and time
- The CAN / CANNOT capability list
- Deciding when asked to choose, and preferring doing over asking
- Per-tool guidance, including not naming tools to the user
- Per-model guidance
- Tenant rules, tone override and disabled capabilities
- The user's custom instructions
- The skill catalogue
- "Don't invent facts" and the medical/legal/financial note
- Knowledge-base context with citations
- The Excel, Word/PDF and PowerPoint plan dialects (FIX112: only on turns that ask for such a file or hold one)
- Canvas revision context

## Only the desktop has

- The environment section: granted folders and the shell
- Tool Use Philosophy
- Safety bullets for destructive commands, sending, purchases, account creation and deletion
- The `remember` credential rule
- Writing style
- Conversation notes
- The browser and web-search unavailable notices
- Image-mode and quick-ask hints
- The sub-agent prompt

## Two things noticed while reading, not acted on

1. **The desktop always says it has tools.** `turn-coordinator.ts` passes
   `toolsAvailable: true` on its only call to `buildSystemPromptParts`. A model
   that gets no tools therefore still reads "You have tools to ACT…" and the
   folder and shell section:
   - Quick-ask turns have a hint that corrects this.
   - A model the catalogue marks tool-less has nothing that corrects it.
2. **Same wording, no cross-tree test.** On 2026-09-11 this listed five
   texts that matched only because someone had ported them by hand. FIX112
   re-read them and found the list itself was wrong in two places (the
   asking rules were never the same wording, the table above said so; the
   privacy bullet is not identical either). What stands now:
   - the temporary-chat notice — **pinned** (one asset)
   - the memory wording — **pinned for two of its three sentences** (one
     asset); the facts sentence differs on purpose, see the table
   - the attached-file envelope — **pinned** against chat-edge's source
   - the privacy bullet — two wordings, not pinned, differences recorded
     in the table
   - the asking rules — two wordings, not pinned, out of FIX112's scope
