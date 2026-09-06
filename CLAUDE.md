# Project rules

## Commit and MR authorship

Commits and merge requests in this repository identify only their human author.
Do not add trailers, `Co-Authored-By` lines, tool attribution, or links referring
to AI tools in commit messages, MR descriptions, or anywhere else in the repo.

## Live test models

`qwen3.8-27b-fp8` (self-hosted, free) is fine for smoke tests of plain text,
streaming, and one isolated tool call. It is a reasoning model: its thinking
shares the 8,192-token output budget the clients send, and the length of that
thinking is not stable across identical requests. FIX71 measured the
desktop-shaped 35-tool request "เข้า youtube ที หาข่าว ตำเฟดเฟ" (a name the model
does not know): 2/3 runs called browser_navigate after 342 to 4,906 reasoning
tokens, 1/3 spent all 8,192 tokens thinking and returned nothing
(output_limit_reached). With one tool it failed 2/3, with no tools 2/3 (thinking
ended with no answer). The same request with a word it knows ("หาข่าว Fed")
called the tool 3/3 in under 6 s. So a qwen pass or fail on a browser,
connector, or multi-step workflow, or on a Thai prompt with names or slang,
says nothing about the code. For acceptance of those workflows use another
tool-capable chat model from the catalogue, verify the actual tool results and
final answer, and treat a qwen run that hits its output limit as a model
outcome, not a defect. Do not change the prompt, tool list, timeout or output
budget to make a test pass.
