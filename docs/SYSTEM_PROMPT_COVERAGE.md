# What each surface tells the model

FIX107 Part C. This is a report only: nothing here was changed or unified
beyond FIX107 Part B. The owner has parked the prompt register deliberately,
and this list is evidence for that work.

The desktop's system prompt is roughly 2.5 times smaller than the web's:

- **Desktop:** about 1,855 estimated tokens (`docs/MODEL_CONTEXT_WINDOWS.md`, FIX105).
- **Web:** about 4,731 tokens (`docs/COMPACTION_FIRST_RUN.md`, FIX101).

Both are measurements of specific conversations, not a fixed size. The
different table format FIX107 B2 fixed was one visible result of that gap.

Read on 2026-09-11 at the FIX107 tree.

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
| **Deciding and asking** | `<action_rules>`: pick when asked to choose, ask at most one question, prefer doing over asking. Always sent. | "## Asking the user (rare)". One question, uses `ask_user_question`. Only when tools are on. Nothing about deciding for the user. | No; similar wording |
| **Output format** | `<output_format>`: tables for comparisons, numbered lists for steps, prose for explanations. | "## Response Style" said only "Be concise and direct" and two lines about tools. **FIX107 B2: now also "## Output format" with the web's text.** | **Pinned since FIX107** (`prompt-output-format.test.ts`, `fix107_prompt_assets_test.go`) |
| **Artifact dialect** | `prompt_artifacts.txt` | `ARTIFACT_INSTRUCTION_WEBCHAT` | **Pinned** (`prompt-artifacts.test.ts`) |
| **Canvas revisions** | "## Canvas Artifacts": lists up to 5 artifacts and says to use `update_canvas_artifact` instead of a new block. | None | Web only |
| **Capabilities** | `<capabilities>`: a "You CAN" list (files, charts, diagrams, and whichever tools are active) and a "You CANNOT (be honest, never pretend you can)" list. | None. The tool definitions are all it has. | Web only |
| **Safety** | `<safety>`: offer an alternative instead of a bare refusal, don't invent facts, a not-a-professional note on medical/legal/financial topics. | "## Safety Rules": no credentials, no destructive commands or sending without confirmation, no purchases, account creation or permanent deletion. | Different rules |
| **Untrusted content** | The last two `<safety>` bullets | The same two sentences in Safety Rules | **Pinned** (`prompt_injection_parity_test.go`) |
| **Privacy** | "never VOLUNTEER a secret…", but return the user's own data when they ask. | "## Privacy", the same idea, plus a rule on what `remember` may store. | Near-identical; not pinned |
| **Tools** | `<tool_policy>`: one paragraph per active tool, and "Do NOT mention tool names to the user". | "## Tool Use Philosophy": "Acting is the default; describing is the exception." Nothing per tool. | Different |
| **Environment** | None | Granted folders by absolute path; the home folder is not a workspace; PowerShell 5.1 shell line. | Desktop only |
| **Unavailable tools** | Folded into the CANNOT list | "## Browser access is unavailable this turn" and "## Web search is unavailable" | Different |
| **Per-model guidance** | `<model_guidance>` per provider family | None | Web only |
| **Tenant settings** | `<tenant_rules>`, tone override, `<disabled_capabilities>` | None | Web only |
| **User custom instructions** | "## About the user" and "## Response style" | None | Web only |
| **Writing style** | None | "## Writing style" | Desktop only |
| **Project** | "## Project instructions" | "## Project: {name}" | Different headings |
| **Skills** | Matched skills, plus a catalogue loaded with `use_skill` | "## Active skills (MUST follow)": they override Response Style. No catalogue. | Different |
| **Saved memory** | `<saved_user_memory>`, with standing instructions and known facts | "## Memory (global)", merged with local notes | Same sentences; only the desktop side is tested |
| **Conversation notes** | None | "## Notes (this conversation)" | Desktop only |
| **Temporary chat** | `<temporary_chat>` | "## Temporary chat", the same three sentences | Same wording; not pinned |
| **Conversation summary** | `<conversation_state>` sent as a user/assistant pair | The same pair and block | **Pinned** (`compaction-prompt.test.ts`, `state_contract.json`) |
| **Context-trim note** | `<context_note>` | `<context_note>` | **Pinned since FIX107** (`prompt_context_note.txt`, `context-note.test.ts`, `state_contract.json` notes). Before FIX107 it was two hand-ported copies, and the desktop had no partial or failed wording. |
| **Knowledge base (RAG)** | "## Retrieved Knowledge Base Context", with `[Source: filename]` citations | None | Web only |
| **Attached files** | "## Typed untrusted file observations" in the system prompt | The same envelope, but in the user message | Same text in a different place; not pinned |
| **Excel / Word-PDF / PowerPoint plans** | Long `:::excel-plan` / `:::doc-plan` / `:::pptx-plan` instructions, sent even when no file is attached | None | Web only |
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
- The Excel, Word/PDF and PowerPoint plan dialects
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
2. **Same wording, no cross-tree test.** These match only because someone
   ported them by hand:
   - the temporary-chat notice
   - the memory wording
   - the privacy bullet
   - the asking rules
   - the attached-file envelope
