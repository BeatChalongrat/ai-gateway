# What the desktop host does in the background

Closing the window hides SRAN Desktop to the tray; the process stays alive
until **Quit**. While it is alive the host (the Electron main process,
`src/main`) does the following on its own, window shown or not:

| What | How often | Where |
| --- | --- | --- |
| Rotates the session | every `sessionRefreshMinutes` | `session-controller.ts` |
| Fires the scheduled tasks that are due | every 60 s | `task-scheduler.ts` |
| Asks the gateway whether a newer desktop version exists | every 6 h | `update-check.ts` |
| Asks for Agent Market notifications | every 60 s | `agent-notify.ts` |

## Agent Market notifications (AM6)

A reminder that falls due, or a round that waits on a question, is shown in
the page as a badge. The page stops polling while its window is hidden, so
the host asks instead and shows an OS notification.

**What is asked.** `GET {chatEdgeUrl}/api/v1/agent-market/notifications?surface=desktop`,
with the signed-in person's session: the same client (`chat-edge-client.ts`)
and the same `Authorization: Bearer` portal token as every other call the
host makes to chat-edge. The answer is the notifications the desktop has not
shown yet.

**How often.** Once about 15 s after start-up, a few seconds after a sign-in
completes, and every 60 s after that, for as long as the process is alive.

**What is shown.** For each notification not yet shown by this process: one
OS notification whose title and body are the server's, exactly as sent. The
host adds nothing and translates nothing. At most 3 per poll; the rest wait
for the next one. After showing it the host tells the server
(`POST …/notifications/{id}/shown`, body `{"surface":"desktop"}`). If that
call fails the notification is still not shown a second time while the app
runs, and the host says "shown" again at a later poll.

**What a click does.** It shows and focuses the main window and loads the
notification's page in it. Nothing is executed by a notification: no round
starts, no file is sent, no reminder changes. It opens a page where a person
acts.

**Where a click can go.** Only to the page's own origin (`pageUrl`'s origin),
never to a browser and never to another site. The notification's `link` is
used only when it is a path under `/chat/agents/` with no scheme, no `//`, no
backslash and no control character, and is still under `/chat/agents/` after
its dot segments are resolved. Anything else opens `/chat/agents`.

**When nothing happens.**

- Not signed in: no request.
- The server answers 404 (Agent Market is off, or an older server): the host
  stops asking until the app is started again.
- Any other failure: the host asks again at the next poll. There is no dialog
  and no error shown; the log has one line when the failures start and one
  when the server answers again.

**What is logged.** Counts and state changes only. Never the token, and never
a notification's title, body or link.

**Settings.** None here: no tray entry, no sound option, no badge count.
Reminders are managed in the page, under the agent's settings.

Tests: `test/am6-agent-notify.test.ts`.
