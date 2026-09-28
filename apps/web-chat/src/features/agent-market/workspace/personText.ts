// AM5 — what the agent says, as a person may read it.
//
// The server builds a failure's words from a reason's code and never from an
// error's text, and cleans what it stores and serves (failure.go). This is
// the same rule once more, at the last place before the screen: whatever
// arrives — from an older server, from a row written before the rule — an
// exception, a type name, a stack frame, a transport error, a step id or a
// sentence of an error message in English is not shown.

const ERROR_TEXT: RegExp[] = [
  /\btraceback\b|\bstack ?trace\b|\bpanic:|\bgoroutine \d|\bnil pointer\b|\bsegmentation fault\b|\berrno\b/i,
  /\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b/,
  /\b(?:error|fatal|warning|exception):/i,
  /\bobject (?:is not|has no)\b|\bNoneType\b|'[A-Za-z_]+' object|\bnot (?:iterable|subscriptable|callable)\b/i,
  /\b[\w./\\-]+\.(?:py|go|js|ts)\b(?:["']?[:,] ?(?:line )?\d+)?|\bline \d+\b/i,
  /\bdial tcp\b|\bconnection re(?:fused|set)\b|\bunexpected EOF\b|\bEOF\b|\bcontext (?:canceled|deadline exceeded)\b|\bno such (?:file|host)\b|\bexit (?:status|code) ?\d+|\bbroken pipe\b|\bi\/o timeout\b/i,
  /\bengine step failed\b|\bsandbox:|\bquality gate:|\bconverter(?: \d{3}|:)|\bstopped:|\bno result \(exit|\bjson: |\binvalid character\b|\bcannot unmarshal\b|\bSQLSTATE\b|\bpq: |\bpgx\b/i,
  /\bdeadlock\b|\bviolates\b|\bconstraint\b|\bsyntax error\b|\bpermission denied\b|\btimed out\b|\bnot found\b|\bundefined\b/i,
  /\bhttps?:\/\/\S+|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b/i,
];

const ENGINE_WORDS: RegExp[] = [
  // an identifier: a step id, a reason code, a finding key (file names are taken out first)
  /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/,
  // a sentence in English: three lower-case words in a row
  /\b[a-z]{2,}(?:\s+[a-z]{2,}){2,}\b/,
];

// a file's name is the person's own (or a deliverable's): never read as an identifier
const FILE_TOKEN = /[^\s,;:()"'“”]+\.(?:xlsx|xlsm|xls|csv|pdf|xml|jpe?g|png|docx?|pptx?|txt|json|zip)\b/gi;
const PARENTHESIS = /\s*[(（][^()（）]*[)）]/g;

/** The text carries something only an engineer can read. */
export function looksTechnical(text: string): boolean {
  const s = text.replace(FILE_TOKEN, " ");
  return ERROR_TEXT.some((re) => re.test(s)) || ENGINE_WORDS.some((re) => re.test(s));
}

/** Text a person may read: a parenthesis that carries technical detail is
 *  taken out of its sentence; if what remains is still technical there is
 *  nothing to show (""). */
export function personText(text: string): string {
  if (!text || !looksTechnical(text)) return text;
  let out = text.replace(PARENTHESIS, (p) => (looksTechnical(p) ? "" : p));
  // a parenthesis whose text was cut before it closed
  const open = Math.max(out.lastIndexOf("("), out.lastIndexOf("（"));
  if (open >= 0 && !/[)）]/.test(out.slice(open)) && looksTechnical(out.slice(open))) out = out.slice(0, open);
  out = out.trim();
  return out && !looksTechnical(out) ? out : "";
}
