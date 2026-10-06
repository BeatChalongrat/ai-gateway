package agentmarket

// ── What a person reads when something goes wrong, in English (AM11) ─────
//
// The same codes and the same {placeholders} as userMessages (messages.go),
// each still saying three things: what happened, what to do next, and
// whether their data is safe. Wording: docs/agent-market/GLOSSARY.md — and no
// sentence here may read like an error's own text (§6 rule 4), because the
// last line of defence (writeErr) withholds what does.
//
// TestEveryMessageHasBothLanguages fails a code that is in one map only, or
// whose placeholders differ.

var userMessagesEn = map[string]string{
	"NOT_FOUND":                "Could not find this {what}. It may have been deleted, or it belongs to another account — go back to Agent Market and open it again. Your other data is not affected.",
	"AGENT_MARKET_BUSY":        "The agent system has a lot of work queued right now — wait a moment and try again. What you sent earlier is all still here, and this request changed nothing.",
	"AGENT_MARKET_ERROR":       "Something went wrong inside the agent system — try again, and if it still does not work, press “Report a problem”. This request saved nothing. Your files and earlier work are all still here.",
	"UNAUTHORIZED":             "Your session has expired — sign in again. Your data is all still here.",
	"AGENT_MARKET_NOT_ALLOWED": "This account does not have access to Agent Market yet — ask {contact} for access. None of your data has been sent or saved.",
	"BAD_REQUEST":              "This request is not valid (code “{code}”) — reload this page and try again, and if it happens again, press “Report a problem”. Nothing was changed.",
	"RUNNER_DOWN":              "The agent's processing service is not ready right now — try again in a few minutes, and if it lasts more than 15 minutes, tell {contact}. Nothing has been saved.",
	"CAPABILITY_MISSING":       "The system does not yet have something this agent needs ({detail}) — tell {contact}. Nothing has been installed or changed.",
	"UPLOAD_TOO_LARGE":         "The file is larger than {mb} MB — make it smaller or split it into several files, then send it again. This file has not been stored.",
	"UNSUPPORTED_FILE":         "This agent cannot read this type of file — send it as {types} instead. This file has not been stored.",
	"TEMPLATE_FILE_TYPE":       "A form must be a .docx, .xlsx or .pptx file — save the form as one of these and upload it again. The form in use has not been changed.",
	"TOO_LONG":                 "The message is longer than 4,000 characters — shorten it, or attach it as a file instead. Nothing has been sent.",
	"BAD_FILE":                 "Could not find the attached file. It may have been deleted or have expired (files not used in any run are kept for 30 days), or it was already used in another run — upload it again. No work has been started.",
	"BAD_ANSWER":               "{detail} — choose one of the options on the card, or type your answer as a sentence. This answer has not been saved.",
	"ANSWER_REQUIRED":          "Not every question has been answered — answer the rest (or press “Skip” to use the recommended answer), then send again. The answers you chose have not been saved.",
	"NOT_COMPUTED":             "This run has no table of figures yet (the calculation is not finished, or the run stopped first) — wait for this run to finish, or open the latest run instead. The files you sent are all still here.",
	"RUN_NOT_FINISHED":         "This run is not finished yet — wait for it to finish, then request another file type. The files already delivered are all still here.",
	"BAD_FORMAT":               "This agent cannot make {format} files — choose one of the file types the agent lists. Nothing was changed.",
	"PDF_UNAVAILABLE":          "A PDF cannot be made right now, because the document converter is not ready — use the .docx file for now, or request the PDF again later. This run's other files are all still here.",
	"NOT_OPEN":                 "This exception has already been decided — reload this page to see the decision. Pressing just now changed nothing.",
	"BAD_CHOICE":               "This exception does not have that option — choose one of the buttons shown. Nothing was changed.",
	"NO_TEMPLATES":             "This agent cannot fill in a company form yet — use the agent's standard files for now. Nothing was changed.",
	"NO_TEMPLATE_FILE":         "Could not find this form's file any more — upload the form again. Your other settings are all still here.",
	"BAD_MAPPING":              "{detail} — complete the mapping and save again. The form in use has not been changed.",
	"NO_TEMPLATE":              "No company form has been set up yet.",
	"BAD_VALUE":                "{detail} — correct this value and save again. The previous value was not changed.",
	"BAD_SETTING":              "{detail} — correct this value and save again. The previous value was not changed.",
	"BAD_VERSION":              "This version cannot be used with this agent — choose a version shown in settings. The agent is still using its current version.",
	"MISSING_CAPABILITY":       "The new version needs something the system does not have yet ({detail}) — tell {contact}. The agent keeps using its current version as usual.",
	"NO_PREVIOUS":              "There is no earlier version to roll back to — the agent keeps using its current version, as before.",
	"AGENT_DISABLED":           "An admin has switched this agent off for now{reason} — contact {contact} if you need to use it. Your history, files and earlier work are all still here.",
	"AGENT_UNPUBLISHED":        "This agent is not yet available to install in the organisation — ask {contact} to publish it in the catalogue first. Nothing has been installed.",
	"NOT_ADMIN":                "This part is for Agent Market admins — contact {contact} if you need a setting changed. Nothing was changed.",
	"NOT_PUBLISHER":            "Only the people who run the catalogue can publish an agent — contact {contact}. Nothing was changed.",
	"LAST_ADMIN":               "There must always be at least one admin — add another admin first, then change or remove this one. Nothing was changed.",
	"BAD_EMAIL":                "This email address is not valid — check the spelling and try again. Nothing was changed.",
	"NOT_IN_ORG":               "{email} is not in this organisation (the email address must end with {domains}) — you can share only with people in the organisation. Nothing has been shared.",
	"SHARE_SELF":               "This is your own email address — enter the email address of the colleague you want to share with. Nothing has been shared.",
	"NOT_SHARED":               "This link was not shared with this account, or it has been withdrawn or has expired — ask the owner of the run to share it with your email address again. No data has been disclosed.",
	"SAME_PERSON":              "The reviewer must be a different person from the preparer — share this run with the reviewer, allowing them to review, and have the reviewer sign off from their own account. The review has not been recorded.",
	"NOT_REVIEWABLE":           "Review and sign-off is not possible because {why} — {next}. The review has not been recorded.",
	"CANNOT_REVIEW":            "This link allows viewing only, not reviewing — ask the owner of the run to share it again with “Allow them to review and sign off” ticked. Nothing has been saved.",
	"EMPTY_PROBLEM":            "You have not said what happened yet — type one short sentence and send again. Nothing has been sent.",
	"BUDGET_STILL_EXHAUSTED":   "The credit budget {which} is still used up for this month — ask {contact} for an increase. This run will wait here. Your files and the answers you gave are all still here.",
	"NOT_BUDGET_PARKED":        "This run is no longer waiting for budget — reload this page to see its current status. Nothing was changed.",
	"BAD_BUDGET":               "A budget must be a number of credits that is not negative (or left blank to use the default) — correct it and save again. The previous value was not changed.",
	"BAD_ACCESS":               "Access must be Off, Listed people only or Everyone in the organisation — choose again. Nothing was changed.",
	"BAD_ROLE":                 "A role must be Member, Agent tester or Admin — choose again. Nothing was changed.",
	"BAD_STATUS":               "A status must be Published, Unpublished or Disabled — choose again. Nothing was changed.",
	"REASON_REQUIRED":          "Switching an agent off for everyone needs a short reason (people will see it) — enter the reason and press again. The agent is still switched on, as before.",
	"NO_SAMPLE":                "This agent has no sample output yet — an admin is having it made. Open this page again in a few minutes.",
	"NOT_LEFT_OUT":             "This document is no longer on this run's “Documents not included” list — reload this page to see the current list. Your files and earlier work are all still here.",
	"ALREADY_INCLUDED":         "This same file is already in the register, so it does not need to be included again — if it is a different document, send that document's file in a new run. Nothing was changed.",
	"ROUND_NOT_DONE":           "This run is still calculating — wait for it to finish, then include the document again. Your files and earlier work are all still here.",
	"NOT_FAILED":               "This run did not stop because it failed, so there is nothing to try again — send files or press “Start a new run” to do the next run. Your files and earlier work are all still here.",
	"NOTHING_TO_RETRY":         "This run's files are no longer here (the files of a run that did not complete are kept for {days} days) — send the files again to do this run once more. The work of earlier runs is all still here.",
	"THREAD_GONE":              "This chat has been removed from the list — its runs, files and records are all still here and can be opened from the agent's overview page. Nothing was changed.",
	"TITLE_TOO_LONG":           "The chat name is longer than 80 characters — choose a shorter name. The current name has not been changed.",
	"ROUND_IN_PROGRESS":        "This run is still in progress — wait for it to finish, or press “Cancel this run” on the progress card first, then start a new run. This run's files and answers are all still here.",
	"RECEIPT_NOT_OPEN":         "Could not confirm the goods received because {why} — {next}. Nothing was changed.",
	"NOT_OFFERED":              "This line is not among those of this run whose goods receipt can be confirmed in the system — reload this page to see the current list. Nothing was changed.",
	"BAD_QUANTITY":             "The quantity received must be “All received” or a number from 0 upwards with at most 6 decimal places — correct it and send again. Nothing has been saved.",
	"MODEL_NOT_ALLOWED":        "This model cannot be chosen ({detail}) — use “Auto” or another model in the list. The model the agent uses has not been changed.",
	"ACCESS_REASON_REQUIRED":   "Opening someone else's run needs a reason (at least 8 characters) — the run's owner will see it. Nothing has been opened.",
	"REMOVE_REASON_REQUIRED":   "Removing a run's files before the retention period ends needs a reason (at least 8 characters) — the run's owner will see it. Nothing has been removed.",
	"AUDIT_UNAVAILABLE":        "The admin change history cannot be recorded right now, so this value was not changed — try again in a few minutes, and if it still does not work, tell the system admin. The previous value is still in place.",
	"ACCESS_REASON_NEEDED":     "This run has to be opened with “Open with a reason” first (or the hour that the last opening lasted is over) — open it again with a reason, and the run's owner will be told. No data has been shown.",
	// AM12
	"BAD_HOST":         "This website name could not be read — type a website name such as “example.com”, or paste the address of a page on it, and save again. Nothing has been changed.",
	"BAD_WEB_POLICY":   "A value is outside the range that can be set — each field's range is shown beside it. Correct it and save again. The previous values were not changed.",
	"BAD_TAX_ID":       "A tax ID must be 13 digits — correct it and save again, or leave it blank. Nothing has been changed.",
	"SUPPLIER_EXISTS":  "A supplier with this name is already on the list — edit that entry, or use another name. Nothing has been changed.",
	"WEB_PAGE_EXPIRED": "The saved web page of this source was deleted at the end of its retention period, because the source was not used as a candidate — its entry is still on the run's page.",
}

// whatEnglish names the thing a NOT_FOUND is about ("Could not find this {what}").
var whatEnglish = map[string]string{
	"install": "installed agent", "agent": "agent", "run": "run", "card": "question card", "file": "file",
	"artifact": "output file", "exception": "exception", "template": "form", "share": "share",
	"member": "member", "department": "department", "problem": "problem report", "sample": "sample output",
	"thread": "chat", "chip": "question", "model": "model list", "round": "run",
	"reminder": "reminder", "notification": "notification", "email_draft": "email draft",
}
