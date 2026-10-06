package agentmarket

// ── The reminder and mail card's own messages, in English (AM11) ─────────
//
// The same codes and the same {placeholders} as reminderMailMessages
// (reminders.go), each still saying what happened, what to do next and what
// did not change. TestEveryMessageHasBothLanguages fails a code that is in
// one map only, or whose placeholders differ. NO_MAIL_CONNECTOR is the
// sentence mail.go's mailNotSetUp says in a thread too: the same test holds
// the two to one wording.

var reminderMailMessagesEn = map[string]string{
	"BAD_RULE":            "The reminder's day and time cannot be used — choose “Every month” with a day from 1 to 31, or “Every week” with a day from Monday to Sunday, and a time such as 09:00, then save again. Nothing was changed.",
	"BAD_LABEL":           "The reminder has no name yet (60 characters at most) — enter a short name such as “Month-end close”, then save again. Nothing was changed.",
	"BAD_SNOOZE":          "A reminder can be snoozed for 1 day, 3 days or 7 days — choose one of them and press again. The reminder is as it was.",
	"BAD_SURFACE":         "This request did not say whether it is shown on the web or in the desktop app — reload this page and try again. Nothing was changed.",
	"NOT_DELIVERED":       "This run has not delivered its work yet, so an email cannot be drafted — an email can be drafted once the run has finished and delivered its files. Nothing was changed.",
	"BAD_FIELDS":          "Sending an email takes only the recipients, the Cc and your confirmation — reload this page and try again. Nothing has been sent.",
	"NOT_APPROVED":        "Sending this message has not been confirmed — check the message, then press “Confirm and send”. Nothing has been sent.",
	"NO_RECIPIENT":        "No recipient has been entered — type at least one recipient's email address, then send again. Nothing has been sent.",
	"BAD_RECIPIENT":       "Each recipient must be a single email address, with no name or other marks mixed in, and recipients and Cc together may number 10 at most — correct the list and send again. Nothing has been sent.",
	"DRAFT_CHANGED":       "This email draft was made again after you opened it — open the latest draft and check the message again before sending. Nothing has been sent.",
	"NO_MAIL_CONNECTOR":   "The mail server has not been set up yet — an admin can set it up in the admin app, on the Agent Market page, under the “Email” tab. Until then, you can copy this message and send it from your own email.",
	"MAIL_FAILED":         "The email was not sent, because the organisation's mail server did not accept this message — try again, or copy the message and send it from your own mailbox. If it still does not work, tell your admin. The email draft is all still here.",
	"MAIL_RECIPIENT":      "The mail server did not accept the recipient {email} — check the recipient's email address and send again. Nothing has been sent.",
	"MAIL_BAD_FIELDS":     "This page sets only the server, port, encryption, username and sender address — the password goes only in .env, as AGENT_MARKET_SMTP_PASSWORD. Nothing was changed.",
	"MAIL_NOT_CONFIGURED": "The mail server is not set up yet — fill in the server, port and sender address, then save. Until it is set up, people can still copy an email draft and send it themselves, as before.",
	"MAIL_NO_PASSWORD":    "The mail server's password is not set — the system admin puts it in .env as AGENT_MARKET_SMTP_PASSWORD and restarts the service (or leave the username blank if the server asks for no password).",
	"MAIL_CONNECT":        "The mail server could not be reached — check the server name and port, and make sure this machine can reach the server. Nothing has been sent.",
	"MAIL_TLS":            "The encrypted (TLS) connection to the mail server could not be made — check the encryption mode against the port (STARTTLS uses 587, TLS uses 465) and the server's certificate. Nothing has been sent.",
	"MAIL_NO_STARTTLS":    "This mail server does not support STARTTLS — if it uses port 465, choose TLS from the start. Nothing has been sent.",
	"MAIL_CLEAR_AUTH":     "This server uses a username and password, but the connection is not encrypted, so the password was not sent — change the mode to STARTTLS or TLS (or leave the username blank if the server asks for no password). Nothing has been sent.",
	"MAIL_AUTH":           "The mail server did not accept the username or password — check the username on this page and the password in .env (AGENT_MARKET_SMTP_PASSWORD). Nothing has been sent.",
	"MAIL_SENDER":         "The mail server does not allow sending as the sender address that is set — use a sender address this account is allowed to send as. Nothing has been sent.",
	"MAIL_DATA":           "The mail server did not take the whole message — try again, and if it still does not work, check the server's settings. Nothing has been sent.",
}
