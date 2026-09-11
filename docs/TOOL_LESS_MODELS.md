# Which chat models get no tools, and what they are told

FIX108 Part C. This is a report only; nothing here was changed.

FIX107 found that the desktop always builds its prompt with
`toolsAvailable: true`. The owner may remove Typhoon rather than fix that.
Removing Typhoon only settles it if Typhoon is the only tool-less model. It
is not.

Checked on 2026-09-11 against the live catalogue (`GET /v1/models`) and the
code at the FIX108 tree.

## The list

**Receives tools?** means whether tool definitions are actually in the
request. **Told it has tools?** means what the system prompt says.

| Model id | Catalogue capabilities | Web: receives tools? | Web: told it has tools? | Desktop: receives tools? | Desktop: told it has tools? |
|---|---|---|---|---|---|
| `Typhoon-S-ThaiLLM-8B-Instruct` | `{"streaming":true}`, no `tools` key | No | **Yes** | No | **Yes** |
| `OpenThaiGPT-ThaiLLM-8B-Instruct-v7.2` | `{"streaming":true}`, no `tools` key | No | **Yes** | No | **Yes** |
| `Pathumma-ThaiLLM-qwen3-8b-think-3.0.0` | `{"streaming":true,"reasoning":true}`, no `tools` key | No | **Yes** | No | **Yes** |
| `THaLLE-0.2-ThaiLLM-8B-fa` | `{"streaming":true}`, no `tools` key | No | **Yes** | No | **Yes** |

All four are tool-less, and on both surfaces all four are told they have
tools.

## Why each cell says what it says

### Receives tools: no, on both surfaces
The catalogue sends `tools: true` only for models that support tools. A
missing key means false.

- **Web.** chat-edge's `KnownWithoutTools` reads that flag, and `attachTools`
  (`message_service.go`) returns before adding any tool definitions. It logs
  `tools_skipped_non_tool_model`, and that line appears live for Typhoon.
- **Desktop.** `modelSupportsTools` (`turn-coordinator.ts`) returns the
  catalogue flag. The turn sends `tools = []` and logs "TOOLS: skipped for
  tools-blind model".

### Web: told it has tools
The prompt's tool list comes from `activeToolNames(conv)`
(`message_service.go`). That function asks the tool executor which tools exist
and never looks at the model. It always includes `ask_user_question`, plus
the memory, search, fetch, python and image tools when those are configured.

That list drives what the prompt says, in two places:

- `<tool_policy>` (`prompt_compiler.go`) opens with "You have access to the
  following tools…".
- The `<capabilities>` "You CAN" list includes "remember user facts across
  chats and search past conversations (via tools)". It adds run_python,
  web_search and similar lines whenever those tools are in the list.

### Desktop: told it has tools
`buildSystemPromptParts` is called once, with `toolsAvailable: true`
(`turn-coordinator.ts`). So `baseSystemPrompt` says "…with the ability to use
tools to help users" and includes Tool Use Philosophy, Asking the user
(`ask_user_question`), and Environment (granted folders, PowerShell).

A no-tools wording exists in `prompt-assembler.ts`, but nothing passes
`false`.

## Caveats

- **Quick-ask on the desktop** gives every model no tools. There the
  `QUICK_ASK_HINT` ("you have NO tools") is added to the system message and
  corrects the base prompt. Ordinary tools-blind turns get no such hint.
- **When the catalogue has not loaded yet,** or the model is not in it, both
  surfaces treat the model as tool-capable and send tools. The gateway
  refuses the turn with `model_no_tool_support`, and both surfaces then retry
  without tools. The catalogue is cached for a few minutes on each side.
- **Everything rests on the catalogue flag** (`model_configs.supports_tools`).
  If an admin sets it, both surfaces start sending tools. Neither prompt
  changes in response, in either direction.
- **The desktop cells come from code.** The web "receives no tools" cell is
  backed by a live log line, for Typhoon only.
