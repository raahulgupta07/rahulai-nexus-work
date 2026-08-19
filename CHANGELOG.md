# Release Notes

## Version 0.0.543.3 (August 19, 2026)

### Withdraws a change in 0.0.543.2 that could have crossed two installations

**Do not deploy 0.0.543.2.** It changed how the application addresses its
database, in order to fix a real fault: on a machine running two copies of this
product side by side, both databases had been given the same name, so roughly
half of every connection went to the wrong one and was refused. That part of
the diagnosis stands, and the eight hundred failures a day it caused were real.

The correction was wrong, and it was wrong in a worse direction. It assumed the
database container is always called the same thing. Where two copies run
together they are deliberately named differently, and the assumption would have
pointed the second installation at the first one's database — the staging copy
reading and writing live data, with nothing to indicate it. Refused connections
are loud. Quietly using the wrong data is not.

This release restores the previous behaviour exactly. Nothing changes unless an
administrator opts in: naming the database container in the environment file
resolves the ambiguity for that installation, and doing nothing behaves as it
always has. The start-up check still reports plainly when the database name in
use answers to more than one place, so the original fault remains visible to
anyone who has not opted in.

Everything else in 0.0.543.2 is unchanged and carried forward: the directory
sync no longer removes people it did not create, removed people no longer
appear as active members, duplicate records are marked rather than deleted, and
an unreachable database is reported as such rather than as a permissions
refusal.

## Version 0.0.543.2 (August 19, 2026)

### Three faults found by reading the servers' own logs

**Half of the application's database connections were being refused.** Two
database containers had been given the same name on the shared network, and
they do not share a password, so roughly half of every new connection went to
the wrong one and was rejected. Measured from inside the running application:
eleven of twenty connections failed. This had been happening for weeks at
around eight hundred failures a day, and it looked like a password that needed
rotating. It was not. Background work suffered worst — live report activity,
connection indexing, scheduled jobs, the chat and email listeners, and the
privacy redaction policy, which had been quietly falling back to its last known
version. The application now addresses its database by a name that cannot be
ambiguous, and the pre-flight check refuses to pass if that name ever answers
to more than one place again.

**The directory sync had been removing everybody.** It is meant to deactivate
people who have left every directory group. It was removing every member of the
organization who was not currently in one — which included everyone who signs
in with single sign-on, everyone with a local account, and everyone who was
invited. On one live installation this left a single active member out of
twenty-nine, and that one survived only because administrators are protected.
It also explains why the same people kept reappearing and vanishing: they would
sign in, be given access, and lose it again within the hour. A directory may
now only remove people it created. And a sync that finds nobody at all removes
nobody at all, because an empty answer from a directory is far more often a
misconfiguration than a company that has lost all its staff.

**Removed people were still listed as active members.** The Members screen
showed everyone who had ever been a member, marked Active, with a working
Remove button — while the permission check correctly refused them entry. On the
same installation that was twenty-nine names for one actual member. The roster
and the door now agree.

### Also

- Nothing in this release deletes anything. Duplicate records are marked rather
  than removed, so every row, and everything attached to it, survives and can
  be restored.
- When the database cannot be reached, the application now says so, instead of
  telling people they do not have permission. That wrong answer is a large part
  of why the connection problem above went unnoticed for so long.
- A connector that holds each person's own credentials no longer reports having
  no tables as a fault. It is how those connectors are meant to work.
- A read-only audit script for administrators: who holds duplicate records,
  what a repair would change, and whether anything would be at risk.

## Version 0.0.543.1 (August 18, 2026)

### Two faults that made finished work look deleted

A user built a seven-slide deck, refreshed the page, and it was gone. It had
not gone anywhere — every slide was in the database the whole time. Two
separate faults, one on each deployment, produced the same impression, and the
interface turned both of them into an empty page rather than an error.

**One person, two membership rows, and the whole application stops for them.**
Nothing in the database forbade a second membership row for the same person in
the same workspace, and the code that asks "is this person a member?" was
written in a way that raises when it finds two rather than answering yes. That
question runs on nearly every request, so a single duplicate row returned an
error for almost everything that person did — 572 requests in one morning.
What they saw was not an error page. It was "No reports found" and "Connect
your LLM", because a failed request was being drawn as an empty one. Their
reports, their models and their deck were untouched throughout.

**The workspace could change underneath you.** When someone belongs to more
than one workspace and has not explicitly chosen between them — which is
always the case in a private window — the application picked the first in the
list, and the list came back in no particular order. So a report opened in one
workspace could be requested against another after a refresh, where it
correctly does not exist. The list is ordered now, the choice is remembered
from the first moment it is made, and a workspace someone has been removed
from is no longer offered.

**An error is no longer drawn as an empty page.** This is what turned both
faults into "my work is gone". A report whose dashboard cannot be loaded now
says so and offers to try again, instead of rendering as a report that never
had one.

Duplicate memberships are merged and the database now refuses new ones. The
merge keeps everything: notes, agent memory, default model and agent choices
are carried across rather than discarded with the row they happened to sit on,
and group memberships are moved rather than deleted with it.

### Also fixed

- Deleting an agent that owns a test suite no longer fails; the suite and its
  cases are kept as organisation-level content.
- Per-model sampling temperature can be set from the model card.
- New models added to the catalogue by an upgrade now reach organisations that
  use their own API key, with the administrator's choices preserved.
- AppDynamics is available as a connector (beta).
- A turn no longer loses its record of what the report has already queried,
  which made the assistant appear forgetful.
- Slide decks that need a correction during generation no longer report a
  failure in the log after succeeding.

## Version 0.0.543 (August 18, 2026)

### Upstream 0.0.543, ported

Upstream's own note for this release is one line about a new connector. That
covers 27 of the 71 changed files. The rest is four separate pieces of work,
and two of them touch code that runs on every request.

**AppDynamics connector (beta).** Cisco / Splunk AppDynamics via the Controller
REST API: applications, tiers, nodes, the service map, business transactions,
metrics, events, health-rule violations and snapshots. Two sign-in styles —
basic auth with an account-qualified login for locked-down on-prem controllers,
and OAuth API Client credentials for an auditable service identity. It arrives
alongside a mock controller and 26 tests, and shows up in the connector list as
beta. Nothing changes for anyone who does not configure it.

**Per-model sampling temperature.** A model can now be given an explicit
temperature, set from its card and stored against the model rather than the
provider. Leaving it empty is the recommended setting and is not the same as
setting zero: empty sends no temperature at all, so the endpoint's own default
applies. That distinction matters because a growing number of models reject any
non-default temperature outright.

**New catalog models reach existing organizations.** Previously only the
preset providers picked up models added to the catalog by an upgrade; an
organization using its own API key kept whatever list it started with. Now any
provider whose type has catalog entries is synced, with the administrator's
decisions preserved — a model switched off stays off, a model deleted stays
deleted rather than reappearing, and the default model is never re-pointed
except to rescue an organization left with no working default at all. This
deployment is unaffected either way: our OpenRouter provider is a custom
provider, and the catalog has no custom-provider entries to sync from.

**Deleting an agent no longer fails when it has a test suite.** A suite's link
to an agent records where its drafts live, not who owns it. The database was
treating it as ownership and refusing to delete the agent at all. Suites and
their cases now survive, becoming organization-level content. Checked against a
copy of the live database: an agent holding a suite deleted cleanly, all 79
suites survived, and the change reverses.

Also in this release: SQL Server Analysis Services accepts the sign-in-style
field the connection form sends, instead of failing when a saved credential
carries it. Two async crashes during agent deletion are fixed upstream.

## Version 0.0.542.16 (August 18, 2026)

### Five defects found by testing the last release before committing it

Nothing here is a new feature. `0.0.542.15` added the "What this means" section
to dashboards; this release is what a pass of pre-commit testing turned up
about it, plus two defects that were already there and had never been noticed.

**The narrative no longer sits half a screen below a short dashboard.** The
document gave `#root` a minimum height of one full viewport, which padded a
short dashboard out and opened a white gap between it and its explanation —
532px on the shortest one, and 392px on a dashboard carrying no explanation at
all, where the padding could not possibly help. All 19 stored dashboards were
rendered with the rule and without it: 15 came out pixel-identical and the 4
that differed all read better without it. The rule existed to stop inner panels
sized with `h-full` from collapsing; that was measured too, and nothing
collapsed in either mode.

**The MCP artifact app had none of its helper components.** Its HTML asks for
`/libs/artifact-globals.js?v=2`, and the loader that inlines vendored scripts
looked for a file whose name ended in `?v=2`, did not find it, and fell back to
leaving the tag alone — a URL that cannot resolve in the sandboxed frame the
bundle renders in. React, ECharts, Babel and Tailwind all inlined correctly, so
the page booted and looked healthy; every one of the 15 shared components
(`KPICard`, `DataTable`, `useFilters`, `EChart`, `fmt` and the rest) was simply
undefined, and any dashboard touching one died. One query string, one file, no
error message.

**The artifact sandbox shell could not parse.** Removing a stale copy of the
shared components from it in the previous release cut through the middle of the
one function that had to stay, and the file ended mid-expression. Every line in
that block died with it — the message listener, the ready signal to the parent,
the loader teardown, the error handler. Nothing rendered it, so nothing broke;
it has been repaired regardless.

**Clicking the explanation in polish mode no longer selects it.** Polish hands
the element you pick to the model to rewrite, which only makes sense for markup
the model wrote. The explanation is composed on the server from figures already
checked against the data and is rebuilt on every render, so a rewrite would be
discarded — and would hand verified numbers back to a model to restate. The
markup was already marked as off-limits; nothing read the marking, and the
element picker returns any `<section>` on sight, so it was the easiest thing on
the page to select.

### Tests

33 new checks, including the first unit coverage the explanation builder has
ever had: absent, blank and wrong-typed payloads must produce no section rather
than an empty heading or an error, and model-written text must not be able to
close the section or open a script tag. Three of the new guards were confirmed
by re-introducing the defect and watching them fail.

## Version 0.0.542.15 (August 18, 2026)

### A dashboard's explanation is now part of the dashboard

- The "What this means" summary is now written into the dashboard itself,
  below the charts it describes, instead of sitting in a strip above it. It
  travels with the dashboard everywhere it goes.
- Opening a dashboard full screen, sharing it, exporting it as a PDF and the
  picture on its card all now carry the explanation. Every one of them used to
  show the dashboard with its conclusion missing.
- Dashboards built before this release get the section too — nothing needs to
  be regenerated.
- A dashboard that embeds a PDF now shows it when exported or pictured, not
  only on screen.

## Version 0.0.542.11 (August 18, 2026)

### Pages stop jumping while they load

- The grey placeholders that stand in while a page loads are now the size of
  what they are standing in for, so a page settles in place instead of stepping
  down as the real content arrives.
- Dashboards held room for ten cards and then filled with fifteen, so
  everything below dropped by a full row, and each card grew as its title
  found its second line.
- A project's dashboards were drawn as bare thumbnails while loading and then
  grew a title and a byline. A project's report list drew rows taller than the
  rows that replaced them. Both now match.

## Version 0.0.542.10 (August 18, 2026)

### Each slide style now draws its own structure

- A style's structure is drawn for you, not left to chance: ruled grounds,
  margin rules, mastheads, section trackers and stamps, each taken from that
  style's own definition.
- Styles built on restraint no longer arrive as a grid of boxes. Where a style
  calls for plain ruled rows, the boxes become rows.
- A style that deliberately carries no footer, page number or logo keeps none.


## Version 0.0.542.9 (August 18, 2026)

### Naming a slide style now beats guessing at one

- Asking for a deck "in the Atelier style" gives you Atelier. Any of the
  eighty-one can be asked for by name, and the most recent instruction wins if
  you change your mind mid-conversation.
- A style merely mentioned earlier in a conversation is no longer mistaken for
  the one you are asking for.


## Version 0.0.542.8 (August 18, 2026)

### A style you ask for by name is the style you get

- Asking for a deck "in the ledger style" now gives you that style. Your own
  words are read again when the design system is chosen, instead of only the
  brief the assistant wrote for itself.
- The rules each style sets — no drop shadows, no rounded corners — are now
  applied to the finished deck. They were being collected and never used.
- A style is never stripped of its own signature: the ones built ON a gradient
  or on rounded panels keep them.


## Version 0.0.542.7 (August 18, 2026)

### Decks now look like the design system they claim, and can be sent as PDF

A deck used to take a style's colours and typefaces and quietly drop everything
else about it. Ask for the ledger style and you got green type on cream, with
none of the ruled paper, margin rule or posted stamp that make it a ledger.

- The agent now picks a design system for each deck and names it, choosing from
  all eighty-one. Say "make it art deco" or "use the ledger style" and it obeys.
- A style's own structure is now drawn for you — ruled grounds, margin rules,
  mastheads, section trackers, stamps — instead of being left to chance.
- The rules a style sets are now applied to the finished file, so a system that
  forbids drop shadows or rounded corners no longer ships with them.
- Slide decks can be exported as PDF. Unlike PowerPoint, a PDF carries the
  deck's typefaces with it, so it looks the same for whoever you send it to.


## Version 0.0.542.6 (August 18, 2026)

### Slide decks get a design system, and the preview finally matches the file

A generated deck used to invent its own colours and typefaces every time, so two
decks on the same data could arrive looking unrelated. Worse, the preview shown
in chat was drawn with fonts the server did not have — so a deck could look
broken on screen while the file you downloaded was fine, and rebuilding it to
"fix" the layout changed nothing.

- Deck previews and PDF exports now use the typefaces the deck actually asks
  for. A preview describes the file you receive.
- Decks are now built to a named design system — eighty-one of them, covering
  board and strategy decks, finance, research, product and more. One is chosen
  for you from your organisation's branding, the agent you are asking, or the
  words in your request; you never have to pick from a list.
- Saying "make it Art Deco", "boardroom style" or naming any other system
  restyles the deck.
- A deck's design system is remembered with the report, so the same team gets
  the same look each time instead of a fresh invention every week.
- Charts no longer put two different scales on one axis, which previously made
  smaller series render as invisible slivers.

## Version 0.0.542.5 (August 17, 2026)

### The agent stops re-deriving table names it was already given

When a question named specific tables, the agent worked out the right ones and
then, one step later, queried a different database with a similar name. It could
take three attempts and two minutes to answer a question it had already
understood.

- The tables chosen for a question now reach the code that runs it, whatever
  shape they were written in. Previously an inspection step could lose them
  entirely and start guessing.
- A correction is now shown to the next attempt where it will be read, instead
  of at the end of the rejected code.
- The agent is told that tables it was handed are already confirmed, so it no
  longer spends a query proving they exist or tries other prefixes in a loop.

## Version 0.0.542.4 (August 17, 2026)

### A wrong table, a wrong model or a missing sign-in no longer answers quietly

An agent that could not reach your data used to answer anyway. It would report a
permission failure as an empty database, pick one semantic model out of several
that matched, or tell you a table does not exist when the truth was that you had
not connected your own account. Those answers looked ordinary, so there was
nothing to notice.

- A query aimed at the wrong database is now caught before it runs, on every
  connector rather than only some, and the message names the correct table.
- Catching that mistake no longer uses up the attempt that would have fixed it.
- A source that cannot read its schema says so instead of reporting no tables.
- When a name matches more than one table, semantic model or saved entity, the
  agent asks which one you mean and lists them, rather than choosing for you.
- A source that needs your own sign-in now says that, instead of appearing empty.

## Version 0.0.542.3 (August 17, 2026)
- **Questions about a specific table stop failing on the first attempt** — when a connection holds several databases whose names look alike, the AI analyst could attach the right table to the wrong one, wait half a minute for the server to reject it, and only then correct itself. You saw a failed step and a long pause before an answer that was always going to be right. The tables you asked about are now named exactly, so it queries the right one first
- **A mistyped table name is caught here instead of at the far end** — a name that does not exist is now spotted against the connection's own table list in a fraction of a second, and the AI is told the correct full name, rather than waiting 20 to 40 seconds for the database to say no in a message that often did not even name the database
- **Database errors about a missing table now explain themselves** — every kind of connection words that error differently, and only one obscure case had any guidance attached. All of them now say plainly that the table almost certainly exists under a different database or schema, and how to write the full name
- Fixed a failure where the AI's own code crashed on `The truth value of a DataFrame is ambiguous` while checking whether it had any results

## Version 0.0.542.2 (August 17, 2026)
- **The sign-in screen opens almost immediately the first time** — opening the site fresh, on a new machine or after clearing the browser, meant waiting on 4 MB spread across 87 downloads before the login box could appear. It is now roughly 260 KB across three, so the first visit is around fifteen times lighter. Nothing changed for later visits, which were already fast because the browser had kept a copy
- **The login page no longer downloads the rest of the product before you have signed in** — every screen in the application, including reporting and charting code you may never open, was being fetched in the background while you sat on the sign-in form. Pages are now fetched when you go to them, and links you can actually see are still prepared ahead of time
- **Logos, icons and translations stop being re-downloaded on every screen change** — they carried no instruction about how long a browser could keep them, so they were fetched again on each navigation

## Version 0.0.542.1 (August 17, 2026)
- **The agent is told how a Power BI figure is meant to be added up** — a semantic model already records that a price is averaged and a quantity is summed, that a ratio must never be totalled, how a number is formatted, and the order months belong in. None of it reached the AI analyst, so it inferred an aggregation from the column name and was quietly wrong on the ones that look summable but are not
- **Mentioning a table or an entity shows the AI its real columns** — `@` a table and the agent received the internal shape of the column list rather than the column names, so the very act of pointing at something made it harder to reason about
- **A chart the agent draws stays a chart** — it was saved correctly but never linked back to the answer that produced it, so the conversation showed the underlying table of numbers instead, with nothing to indicate a chart existed
- Column descriptions and meanings written by whoever modelled the data now survive the trip from the connected system to the AI, on every connector rather than one

## Version 0.0.542 (August 17, 2026)
- **SQL Server Analysis Services models are understood in far more detail** — the agent now sees the physical tables behind a Tabular model, along with the meaning attached to each field: how it is formatted, what it counts as, which folder it is filed under and how it sorts. Hierarchies and inactive relationships are described too, so questions about a Tabular model can be answered without someone explaining the model first

## Version 0.0.541.3 (August 17, 2026)
- **A vague question gets a question back, instead of an apology** — when the AI analyst needed to ask you what you meant, it failed four times out of five and returned "unable to complete task due to repeated tool validation errors". The turn was even recorded as successful, so nothing anywhere showed it had gone wrong. Asking now works first time
- **Data requests no longer throw away their first attempt** — naming the tables to look at plainly, or calling them by a slightly different name, made the request fail and quietly start again. It cost a wasted round trip and left a failed step in the conversation for something that was never wrong
- **Saving a reusable prompt accepts a plain list of parameters** — the same class of problem, on the training screens
- **Tables written by the agent appear as tables** — roughly one in eight came out as rows of text separated by bars, because the underlying formatting needed a line the AI does not always write. That line is now added where it is missing, and code samples containing bars are left exactly as they were

## Version 0.0.541.2 (August 17, 2026)
- **The AI analyst can search the web** — ask about something outside your data and it looks it up, then opens the pages it found before answering. Available on every workspace, with no account or key of its own, and controlled by the existing Web Fetch setting
- **Handing the AI a file it cannot open now points it at the one that can** — where it mistook an uploaded document for a dashboard it repeated the same failing step until the conversation was abandoned. It is now told which tool reads that file, and the file reader says plainly that it handles uploaded Word, PDF and PowerPoint documents

## Version 0.0.541.1 (August 17, 2026)
- **Everything on the Scheduled screen can now be changed from the screen itself** — each scheduled task and each report refresh carries its own pause, edit and delete, visible without hovering. Deleting a task previously appeared only when the mouse was over its row, so on a tablet there was no way to reach it at all
- **A report refresh can be paused and later resumed without losing the time it was set to** — until now the only way to stop one was to switch it off, which erased the schedule, so anyone who wanted it back had to remember what it used to be and set it again
- **A report refresh can be edited without opening the report** — the time it runs, who is emailed the results, and whether it runs at all, all from the list where you can see it
- **Downloading a chart that no longer has stored results now says so** — it reported an internal error instead, on roughly half the charts on an established installation, because results are cleared from older ones to save space

## Version 0.0.541 (August 17, 2026)
- **You can download everything an agent knows as a single file** — its instructions, its settings and its saved tests, bundled into one zip you can archive, review or hand to a colleague
- **Agent managers and owners can save queries again** — the permission check was stricter than the screen implied, so people who could clearly edit an agent were refused when they tried to save work on it

## Version 0.0.540 (August 16, 2026)
- **Your own applications can now sign in to CityAgent Insights on a colleague's behalf** — register an app once and it can create reports, ask the AI analyst questions and use the connected tools, always under that person's existing permissions and never beyond them. Each app is registered, named and can be revoked at any time

## Version 0.0.539 (August 17, 2026)
- **Power BI files (.pbix) can be used directly as a data source** — point at the file and query what is inside it, with no server or gateway involved
- **monday.com can now be connected** as a data source
- **SharePoint Lists can now be connected** as a data source
- **Tables in dashboards sort, page and export on their own** — every table now offers sortable headings, paging for long results and a "Download CSV" button, right-to-left languages included
- **Dashboard filters now match exactly what the table shows** — a filter and the grid beneath it read the same values, so a filtered total always agrees with the rows you can see
- **Slide decks no longer show their source code** when a preview image could not be produced; the deck still downloads as normal
- **Long conversations keep answering** — the agent now trims what it carries forward more carefully, instead of overrunning the limit and failing partway through a long thread
- **Accepting the same suggested edit twice no longer applies the wrong one** — where two pending changes looked identical, the review screen now refuses rather than guessing, and tells you to refresh
- Idle connections to SQL data sources are closed instead of being held open indefinitely

## Version 0.0.538 (August 14, 2026)
- **A file whose name was written in an older regional encoding opens again** — on Hebrew shares and folders written by Windows the read failed, and it took the rest of the agent's turn down with it, so every later step of that answer failed too. Those names are now recovered where they can be, and always stored safely
- **File listings no longer show garbled names** when a folder and the files inside it were written in different encodings

## Version 0.0.537 (August 14, 2026)
- **A report with a long history opens in about a second instead of tens of seconds** — the conversation loads short previews and fetches full results only where they are actually shown. Result cards, exports, version history and "Added to Dashboard" behave exactly as before

## Version 0.0.536 (August 14, 2026)
- **Qlik Sense on-premises can now be connected** as a data source
- The agent makes better use of its context, so answers start sooner

## Version 0.0.535 (August 14, 2026)
- **An Elasticsearch connection works with a key scoped to specific indexes** — a key without cluster-wide monitoring could not be saved at all. A connection now needs only read access to the indexes it exposes, and one unreadable index pattern no longer empties the whole catalogue

## Version 0.0.534 (August 14, 2026)
- **An agent built on a very large connection no longer comes up with no tables at all** — past roughly 32,700 tables the catalogue copy failed outright and "Reload tables" could not recover it. Opening a connection that size is also much faster

## Version 0.0.533 (August 14, 2026)
- **Salesforce can be connected with a Connected App** — consumer key and secret only, with no username

## Version 0.0.532 (August 14, 2026)
- **A shared conversation link reads like the conversation it came from** — it used to show a column of empty bubbles wherever a silent event sat, page in almost nothing on a busy report, drop diagrams and formulas to raw text, and reduce evaluation runs, clarifying questions and the knowledge panel to grey debug lines
- **You can switch organisation from the sidebar** if you belong to more than one

## Version 0.0.531.3 (August 14, 2026)
- Nothing changes for you in this release. Everything from 0.0.531.2 is unchanged; this build exists only so the release you are running has been checked end to end

## Version 0.0.531.2 (August 13, 2026)
- **When someone leaves, their work stays** — closing an account no longer strands what that person built. Dashboards, scheduled reports, saved queries, folders, the notes on a report, shared instructions and saved prompts, and the agents they set up can all move to a named colleague in one step. A new **Needs an owner** panel on the Members screen lists anyone whose account has been switched off and still owns something, so nothing sits unclaimed and unseen
- **You can hand over your own work, and take a copy with you** — your profile now shows everything you own, lets you pass any of it to a colleague, and lets you nominate a successor in advance so a handover happens on its own if your account is ever closed. You can also download a complete copy of your own work as a single file, and an administrator can download the same copy for somebody who is leaving
- **Conversations stay private** — a handover moves dashboards, scheduled reports and anything shared with other people. It does not move plain chat threads: those stay with the person who had them, and the confirmation screen tells you both numbers before you agree to anything
- **You are told what a handover changes before you agree to it** — including when a dashboard queries your data as the person who owns it, and when the new owner will need to sign in to that data source themselves before an agent can answer again
- **People who receive a scheduled dashboard are told when it changes hands** — one message naming the new owner, confirming it will keep arriving on its usual schedule
- **A handover can be undone** — for thirty days, in one click, putting everything back exactly as it was
- **A closed account is closed everywhere** — an account that has been switched off can no longer be used to reach anything, including through keys it created earlier
- **The handover screens now speak every supported language**

## Version 0.0.531.1 (August 10, 2026)
- **The App Analytics switch is where you would look for it** — the control that shows or hides the App Analytics page sat near the bottom of the General tab, so an administrator who opened Features found nothing about it and reasonably concluded the page was missing. It now appears under Features as well, in a clearly-marked section of its own: unlike everything else on that tab, it applies to every organization on the server, not only yours, so it says so and stays available to super administrators alone

## Version 0.0.531 (August 9, 2026)
- **An evaluation you start from an instruction reports back in the conversation** — the result used to live only in the tab you started it from, and was gone on reload. It now posts into the thread you ran it from, so the conversation is the record
- **Choose which evaluation suites to run** — the suite picker takes more than one, with all suites on by default, and you can point a run at a single suite
- **Your agent selection follows you between devices** — the set of agents you pin is now remembered against your account rather than one browser. Auto stays Auto: leaving nothing pinned still means "any agent you can access", resolved fresh each time, so an agent added tomorrow is in scope without you doing anything
- **An agent's evaluation history stops going missing** — a busy organization could push an agent's own runs off the end of the list, and the panel said it had none. The list is now narrowed to the agent before it is cut short
- **Read-only viewers can see approved instructions** — approved guidance is visible to people who cannot change it, and the review controls appear only for those who can act on them
- **The instruction editor stops erasing what you just saved**, a rejected suggestion folds away instead of repeating itself back at you, and the details footer stays put while a change is under review
- **No Admin button for people who cannot use it** — the sidebar showed an Admin entry to every member, which opened a read-only list. It now appears only for someone holding an administrative permission
- **Groups from your directory read properly** — a group the directory could not name arrived in Settings as a bare identifier, and two groups sharing a display name could cost someone a membership at sign-in. Names now resolve where they can, say so plainly where they cannot, are corrected on upgrade, and a person in two same-named groups keeps both
- **Snowflake connections no longer require a schema** — leave it empty to index the whole database
- **Power BI keeps what it knows about your columns** — a refresh used to reduce them to a name and a type, so measures reached the agent as ordinary untyped columns and hidden keys looked like report fields
- **Sending an invitation shows it is working** and cannot be submitted twice, and opening a shared dashboard keeps its loading state instead of flashing an empty page
- **The server warns at startup when no encryption key is set** — without one, stored credentials cannot be read back after a restart. Nothing about that changed; it is now said out loud instead of discovered later

## Version 0.0.528.14 (August 9, 2026)
- **No release-notes window in your way** — the What's New dialog no longer opens for everyone. The version number stays where it was, at the bottom of the sidebar, as plain text
- **Release notes are an administrator's screen** — the Changelog entry in the account menu is now shown to administrators, who can still open the full history from the version number

## Version 0.0.528.13 (August 9, 2026)
- **Analysis of your uploaded files works again** — a check added in 0.0.528.12 could refuse a correct query against an uploaded spreadsheet or CSV, depending on how the generated code happened to be written. It looked like the question had failed. The check now recognises every valid form

## Version 0.0.528.12 (August 9, 2026)
- **A deleted report is now actually gone** — deleting a report removed it from your lists but left it readable, and editable, to anyone who still had its link. It is now unreachable, and it can no longer be brought back into everyone's list by editing it
- **Your work stays yours across the whole app** — dashboards, charts, text blocks, saved queries, attachments and result grids now all answer the same question about who may see a report, instead of each deciding on its own. A report shared with named people is no longer readable by everyone in the organization through a side door
- **A shared link now shares only what you shared** — the chart and text content behind a report link is served to a visitor only when that report is genuinely public. Organization-only and named-people dashboards no longer answer an anonymous visitor holding the link
- **Asking about something you cannot open tells you nothing about it** — a report you are not allowed to see now answers exactly as a report that does not exist, so ids can no longer be probed for what is really there
- **Form errors no longer repeat what you typed** — a rejected sign-up or settings form used to send your submitted values back in the error, including a password. It now reports which field is wrong and why, and nothing else
- **Stronger browser protections** — the app now tells your browser what it is and is not allowed to do on these pages, which limits what a malicious page or injected script could do with your session
- **Sign-in through an identity provider no longer puts your session in the address bar** — the token used to arrive as part of the URL, where it could be kept in browser history or passed on in a link

## Version 0.0.528.11 (August 9, 2026)
- **"How many rows?" now goes and counts** — asked how big a table was, or which one was largest, the agent used to answer from the table list it already had in view. That list describes what your data looks like, not how much of it there is, so the number could be confidently wrong. Size, date-range and how-many-different questions now read the data itself, even when you have not named a table
- **A table whose size was never measured no longer reports as empty** — when the catalog holds no count for a table, the agent now says so instead of reporting zero rows

## Version 0.0.528.10 (August 8, 2026)
- **The reasoning panel on your own chat opens again** — 0.0.528.9 tightened who may read a turn's plan and set the bar at administrator, so ordinary members were refused on their own conversations. Reading the plan now follows the same rule as the rest of the turn: it belongs to the person whose turn it is

## Version 0.0.528.9 (August 8, 2026)
- **Your conversations stay yours** — a report's chat is now readable and writable only by the person whose report it is. Sharing a dashboard shares the dashboard; the conversation behind it was never part of that and now cannot be reached through it
- **Only you can stop, steer or clear your own prompts** — stopping a running answer, steering it mid-run, and removing a queued prompt are now limited to the person whose turn it is
- **Share notifications open the page you were actually given** — a share notification used to link to the authoring workspace, which no share grants, so the recipient was simply refused. It now opens the shared dashboard or the shared transcript
- **Evaluation history loads again** — the runs list and the agent's own eval tool failed outright against the production database. Both now return
- **Evaluations stay with their own agent** — one agent's test cases and runs could be seen from another agent's screen
- **Reloading a Power BI agent keeps its measures** — a single Reload stripped the markings that identify a measure and hide a join key, so the agent stopped calling your measures by name and re-derived them by hand, disagreeing with your own Power BI reports. Reload now preserves them exactly
- **References you add to knowledge stay added** — a reference showed "Saved" and then vanished, and touching anything else afterwards deleted it for real. Table nodes also read "No rules attached" however many were pinned to them
- **Instruction titles keep the capitalisation you typed** — editing a title from inside a report rewrote it in capitals everywhere else
- **Directory groups show their names** — groups synced from Microsoft Entra could appear in the admin list as raw ID codes. Existing rows are corrected on upgrade
- **Nine languages are complete again** — over a thousand phrases per language were missing and silently fell back to English, mostly across settings, data and agent screens. Arabic, German, Spanish, French, Hebrew, Italian, Portuguese, Russian and Swedish are now fully translated
- **Smaller repairs** — invitations show that they are sending, the instruction editors no longer offer a load mode that did nothing, a rejected edit folds away instead of shouting, and the dashboard viewer no longer flashes an empty screen while it opens

## Version 0.0.528.8 (August 8, 2026)
- **The sidebar groups your reports by when you last used them** — one long list becomes Today, Yesterday, Previous 7 days, Previous 30 days and Older, so a report from this morning is no longer sitting between two from last month
- **Pinned reports sit at the top in their own group** — and the group collapses when you want the space back
- **Pin or unpin from the sidebar** — hover a report and use the pin, without opening it first
- **One name for it everywhere** — what the reports list called adding to favourites is now pinning, matching the sidebar and the report header

## Version 0.0.528.7 (August 8, 2026)
- **A new chat starts on Auto again** — every agent came up individually ticked instead, which quietly fixed the chat to the agents that existed the moment you opened it. An agent added later, or access granted later, was left out. Auto now means what the picker says it does: any agent you can access, decided each time the chat runs

## Version 0.0.528.6 (August 8, 2026)
- **New report opens the chat screen again** — clicking New report, in the sidebar or on the reports page, did nothing at all: no chat box, no error, no sign anything had happened. It now opens the composer, and the workspace button at the top left works again

## Version 0.0.528.5 (August 8, 2026)
- **The provider logo now reaches the sign-in page** — 0.0.528.4 made the logo choice work everywhere except the sign-in button itself, which fell back to a plain lettered square. It now shows the logo you picked

## Version 0.0.528.4 (August 8, 2026)
- **The logo you choose for a provider is now actually used** — picking one saved the choice and then nothing showed it: the providers list kept the original logo, and the sign-in page drew the same generic badge for every provider. Your choice now appears in both places
- **Sign-in buttons tell providers apart** — Google, Microsoft Entra ID, Keycloak and any other provider each show their own logo on the sign-in page instead of one shared icon

## Version 0.0.528.3 (August 8, 2026)
- **Choose the logo shown for a sign-in provider** — a provider's logo could only ever be the one it came with. There is now a picker beside its display name, including a plain option for a provider none of the supplied logos suits

## Version 0.0.528.2 (August 8, 2026)
- **Setting up a sign-in method is now a dialog, not a page that grows** — choosing Configure on an identity provider, or on your directory, opens a focused window with everything in it. Before, the settings unfolded down the page and pushed everything else out of view, and the directory form ran to twenty fields
- **Providers are shown by their own logo** — Google, Microsoft Entra ID, Keycloak, OpenID Connect and your directory each carry their mark instead of a coloured initial. Two providers whose names began with the same letter used to look identical in the list

## Version 0.0.528.1 (August 8, 2026)
- **Sign in to your directory with your username** — the directory sign-in form asks for a username and offers `jsmith` as the example, but only a full email address was ever accepted. Anyone typing the username they actually use was turned away as though the password were wrong, with nothing to suggest otherwise. Usernames now work, email addresses still work, and an administrator can set which directory attribute people type under Settings → Identity Provider

## Version 0.0.528 (August 8, 2026)
- **Monitoring is no longer admin-only** — whoever manages an agent can now open Monitoring for it and see its runs, failures and spend, narrowed to the agents they manage. Administrators keep the organization-wide view. A conversation that also draws on an agent you don't manage stays closed, because opening it would show that agent's queries
- **A read-only viewer sees what an agent actually uses** — the Tables panel listed every table in the connection, including the ones its manager had turned off, and counted them as "2 of 12 active". Those are the manager's working set, not part of the agent. Viewers now see the selected tables only, and the list no longer reorders between pages
- **Custom roles work** — a role built from scratch produced a member who could not open a report or attach a file to a chat, with no checkbox anywhere that would fix it. Those permissions are baseline product usage and are now granted to every member of the organization, whatever their role
- **Evals follow the agent you manage** — an agent owner asking the AI about their evals used to get "no matching evals" and an unexplained error, on an agent whose Evals panel listed them perfectly well. Seeing, running and editing an eval now all need authority over every agent it targets, and eval results are no longer readable by someone who could not have started the run
- **Deleting an instruction is fast again** — an instruction carrying pending suggestions took around seventeen seconds to delete and slowed everything else on the server while it ran. It now takes under a second
- **Accepting a suggestion reaches the live rules** — an agent manager's "Accept" could report success, mark the change accepted, and never publish it, if any organization-wide instruction existed anywhere. Editing an agent's own rules also silently ignored the smart/always toggle
- **Evals live with their agent** — the separate Evals page is gone; evals appear in the Agents explorer under each agent, and organization-wide ones under Global Evals. Existing eval links still work
- **File a report into a project by dragging it** — drag a report onto a project in the sidebar. The report keeps its place in the list and picks up the project's colour
- **Deep Analytics mode has been removed** — the mode picker now offers Chat and Training, and disappears entirely for people without training access rather than showing a one-item menu. Existing Deep conversations, scheduled prompts and triggers are moved to Chat and keep working
- **Adding someone to an agent is one choice, not five checkboxes** — Can query, Can contribute, or Can manage, each including everything above it. People and groups share one search box
- Available in all ten languages

## Version 0.0.526.1 (August 7, 2026)
- **The release history is now an administrator's view** — What's New shows everyone the three most recent releases, and says that is what it is showing. Administrators still see the whole history. The older notes describe ported versions, reversed decisions and fixes for problems that shipped; that is an internal record, not something every member needs
- **Enterprise features stay unlocked, and the checks that prove it now run** — the permanent enterprise grant was answering every question itself, including the ones the test suite asks to exercise seat limits and restricted plans. Twenty-eight checks across eight areas were passing without testing anything, among them every seat-limit check in the directory and single-sign-on provisioning paths. Nothing about a running installation changes: it is unlicensed by design and stays unlimited

## Version 0.0.526 (August 6, 2026)
- **A turn that gets nowhere now says so instead of reporting success** — the agent could burn through its whole planning budget producing blank decisions and no tool calls, then record the run as successful. Three separate paths did this: a decision object with no action, no text and no reasoning was persisted as an empty "Planning" block; a decision with nothing to run replayed the identical prompt every step until the limit; and reaching the step limit fell through to a default of "success". Each now ends the turn as an error with a message saying what happened
- **A failed turn no longer spends more of your quota** — an errored run used to go on to generate a report title, suggest instructions, and score its own answer. It has no answer to score
- **Tool calls survive quotes inside text, including Hebrew and Arabic** — models stream tool arguments as JSON, and in Hebrew and Arabic the double-quote is an in-word abbreviation mark (ארה"ב, מנכ"ל), so any text-heavy argument broke the JSON and the whole call was dropped. Malformed arguments are now repaired where it is safe to do so — prose wrappers, code fences, Python-style quoting, stray line breaks — and only genuinely unrecoverable ones are refused
- **A broken tool call reports the real reason** — unparseable arguments used to be run through validation, which replied "field required" for every field and sent the model chasing a problem it did not have. It now reports the JSON error itself, with the text that failed
- **Requests that cannot succeed are no longer retried** — a prompt over the context limit, or an account out of credit, used to be retried anyway, doubling the wait and the cost before failing the same way
- **Dashboards stop claiming success over a broken render** — a dashboard whose latest version reported render errors was announced as created successfully. It now names the errors and offers to fix them
- **The artifact limit is checked before the work, not after** — an over-budget dashboard call used to run a full generation and only then be discarded
- **Clearer failures when data cannot be assembled** — a missing source file now names the ids that did not resolve, and a missing connection says what to attach

## Version 0.0.525 (August 6, 2026)
- **A new report in a project now names the project's agent instead of showing "Auto"** — the report already carried that agent, but because a fresh report holds exactly the project's defaults, the prompt box collapsed it into a generic "Auto" chip and the agent picker highlighted nothing, so there was no way to tell which agent was answering
- **A single selected agent is named, not shown as a bare icon** — anywhere one agent is in play, the chip says which
- **Starting a report from the command palette keeps it in the project** — the project page promises its agents are copied onto every new report created there, and that entry point was creating a workspace-level report attached to every agent in the org instead

## Version 0.0.524 (August 6, 2026)
- **Rebuilt Custom API connector** — the tool editor now tests one endpoint at a time while you write it, so you find a wrong path before saving rather than after connecting. Each endpoint carries its own approval policy, so a read stays automatic while a write asks first
- **Username-and-password APIs are supported** — Basic Auth is now a first-class option alongside the existing token and header methods
- **The tools page shows what each tool actually calls** — the HTTP method and path next to every endpoint, and a real parameter table when expanded. That table had never appeared for any tool
- **Large and non-text API responses no longer break a turn** — an oversized JSON reply used to exhaust memory; a PDF or image reply was unusable. Both now arrive as files you can open
- **A clearer reason when an API call fails** — the message the service itself returned, instead of a wall of raw response text
- **Saving a connection whose root address returns "not found" now works** — many APIs answer nothing at their base address while every endpoint behind it is fine, and that alone used to block the save. Only a genuinely unreachable host is refused
- **Security — testing an endpoint can no longer send a saved credential anywhere else.** When you test one endpoint of a connection you are editing, the app reuses the stored username, password or token so you don't retype it. The address it was sent to was taken from the form, so a request naming a different address would have received that credential. The address now always comes from the saved connection, and testing an endpoint requires permission on that specific connection rather than on connections in general
- Available in all ten languages

## Version 0.0.523 (August 6, 2026)
- **Editing or removing an AI model no longer fails** — the Save and Delete buttons on a model called into a gap in the code and returned an error every time, so a model could be added but never changed or removed. Both now work, and a model that is currently the default is protected: make another model the default first
- **Redesigned AI model settings** — providers are shown as chips, and clicking a model row opens a card for its details instead of a cramped inline row. Adding a model and adding a provider are separate, clearly labelled actions
- **Cleaner eval runs page** — a compact list you can expand a case at a time to read like a report, and the run comparison now explains what "fixed" and "regressed" are measured against
- **A link to the org-wide evals page now works** — the address it produced had an empty segment in the middle, so opening it directly, or reloading the page, landed nowhere
- **Splunk works on restricted deployments** — where the server blocks wildcard index searches, you can now name the indexes to use instead of relying on automatic discovery
- **Zabbix connector fixes**
- The compare hint is available in all ten languages; the new model-settings labels are English-only for now, as upstream shipped them

## Version 0.0.522 (August 6, 2026)
- **Reading a large scanned document no longer fails** — a scanned page was handed to the AI model as an oversized image the provider refused outright, which ended that turn and every turn after it. Pages read from a file, and pictures you upload yourself, are now sized for the model automatically
- **A cut-off answer says so instead of arriving broken** — when the AI ran out of room mid-way through writing code, the half-written result was used as though it were finished, which surfaced as a puzzling error somewhere else entirely. It is now recognised as truncation and reported as one
- **A tool called with bad arguments is reported as failed** — a call that never ran at all could still be presented as a completed step with an empty result, so the AI carried on as if it had an answer. Two isolated mistakes far apart in a long conversation also no longer count towards the limit meant for a model stuck repeating the same malformed call
- **Saving a table as CSV keeps its name** — a title written in Hebrew, Arabic, or any non-Latin script was stripped down to underscores; the name is now preserved
- **Notifications** — a notifications panel now collects what happened while you were away
- Available in all ten languages

## Version 0.0.521.6 (August 6, 2026)
- **A single sign-on account can no longer be captured by someone who got in first** — when a person signed in through an identity provider, their account here was matched purely on the email address. Anyone who had created a local account with that address beforehand, without ever confirming it was theirs, inherited the sign-on identity and everything it could reach. Linking an existing account to a sign-on identity now requires proof on both sides: the local account must be confirmed, and the identity provider must state that it verified the address
- **An address a provider has not vouched for is treated as unverified** — some providers send a display name or a login name in place of a confirmed email address. Those can still be used to fill in a new account's address, but never as evidence of ownership, so they cannot be used to match an account that already exists
- Providers that are known not to state whether they verified an address, such as Microsoft Entra, can be trusted explicitly by an administrator. The setting is off unless it is switched on, applies only to the providers it names, and cannot relax the requirement that the local account is confirmed

## Version 0.0.521.5 (August 5, 2026)
- **Turning on directory sign-in no longer locks out everyone who does not use it** — with LDAP enabled, anyone whose account was created here rather than in the directory could not sign in at all. The directory was consulted for every sign-in, and "this person is not in the directory" was treated as "wrong password", so a perfectly valid member was refused with no explanation. Directory sign-in is now its own option on the login page, and the email-and-password form no longer consults the directory at all
- **Choose how you sign in** — when directory sign-in is switched on, the login page offers *Continue with LDAP* alongside the existing single sign-on options. The form asks for a username instead of an email address so it is clear which credential is wanted, and you can switch back with one click
- **Leavers stay locked out** — an account the directory has claimed is refused at the password form as well, so removing someone from the directory still removes their access. Account owners keep an emergency password route so a directory problem can never lock an administrator out of their own installation
- Failed sign-ins say no more than they did before: whether an address is unknown to the directory or the password was simply wrong, the answer is identical, so the login page cannot be used to discover who works at your organization

## Version 0.0.521.4 (August 5, 2026)
- **A database problem can no longer switch PII redaction off without saying so** — when the app could not read an organization's settings, it treated that exactly like an organization that had asked for no redaction, and prompts went to the AI model unprotected with only a passing note in the log. Redaction now keeps using the last policy it successfully read, and if it has never read one it says plainly, at error level, that prompts are going out unprotected. Block mode, which lives in the same settings, was affected the same way and is covered too
- **One group name already in use no longer kills the whole directory sync** — if a group arriving from LDAP had a name some other group already had, the sync failed on that one group and rolled back everything else with it: every group, every membership, every hour, indefinitely. Groups that belong to the directory are now matched by name as well as by directory path and reused rather than duplicated, a group belonging to someone else is skipped with an explanation instead of stopping the run, and the rest of the sync completes
- The same protection was added to sign-in group sync, where a name clash could previously interrupt a user's login

## Version 0.0.521.3 (August 5, 2026)
- **The LDAP setup hint now names a file that exists** — Settings → Identity Provider told administrators to configure LDAP in `bow-config.yaml`, a file renamed to `dash-config.yaml` some releases ago, so anyone following the instruction went looking for something that was not there. Corrected in all ten languages
- Removed two leftover strings for a licence-key screen this build does not ship; enterprise features are permanently enabled here and there is no key to enter

## Version 0.0.521.2 (August 4, 2026)
- **Sync history now tells you what a sync actually did** — a run still in progress showed an empty panel when you opened it, which is exactly when you most want to watch it; it now shows the step it is on, how far through it is, and each workspace as it finishes. A finished run shows the number of tables it brought back, the workspace and kind behind every line, and the tenant it read from — all of which the app already knew and was throwing away. A run that missed some workspaces says how many, so a smaller table count is never mistaken for a smaller dataset
- **Each line of a sync log now carries its own time** — the log recorded no timestamps at all, so a sync that stalled on one workspace looked identical to one that moved steadily. Times are recorded as each workspace completes and shown as an offset from the start of the run. Syncs recorded before this release keep no times rather than being given invented ones

## Version 0.0.521.1 (August 4, 2026)
- **A connector that needs your personal sign-in no longer switches the whole agent off** — testing the connection on an agent whose data source asks each person to sign in individually (Power BI, Microsoft Fabric) counted "you have not signed in yet" as "this agent is down", and disabled it for everyone in the organisation. The agent then disappeared from the Agents page entirely, with no message and nothing in the activity log to say why. Connectivity is now reflected only for agents that connect with the organisation's own credentials, where a failure really does affect everybody

## Version 0.0.521 (August 4, 2026)
- **Attaching an image no longer breaks or blinds the AI mid-analysis** — on Anthropic and Bedrock, any conversation carrying an image (an uploaded screenshot, or a scanned page the AI read from a file) failed with a provider error the moment the AI used a tool; on OpenAI, Azure and OpenAI-compatible endpoints such as LiteLLM the model silently lost sight of the image instead and told you it "couldn't see the attachment". Images now stay visible to every provider for the whole conversation
- **The AI remembers what it already did** — every tool result is now part of the conversation the model actually sees, so it stops re-reading files it just opened and re-running work it just finished. A file it pulls from a connection stays usable for the whole report instead of going stale after one step, and those background fetches no longer show up as attachments in your message box
- **Instruction reviews show exactly what changed** — a suggested edit no longer re-displays text that is already live (or duplicates it when you accept), Accept and Reject on a chat card act only on that suggestion instead of everything pending, the AI edits surgically with anchored changes rather than rewriting the whole instruction, and it is told whether you accepted or rejected each suggestion so it stops re-proposing rejected ones
- **Instructions can be filed into folders** without dragging, and an instruction already in a folder can be lifted back to the top level in one click
- **Download an instruction as Markdown** from its detail pane
- An accepted AI suggestion no longer stays stuck as a draft — it was invisible to every context loader and showed as "Inactive" even though you had approved it
- Tables and tools can be referenced by @name in agent instructions, with quoted names for anything containing a space or a dot
- How much of an MCP or custom-API result the agent reads is now a setting instead of a fixed three-record sample

Carries upstream 0.0.519, 0.0.520 and 0.0.521 in full. Upstream's license-key
screen and its license-key storage are deliberately not included: enterprise
features are permanently enabled in this build and there is no key to enter.

## Version 0.0.518.4 (August 4, 2026)
- **The filter panel above a data widget works** — removing a filter left the data filtered: the panel said "No filters applied" while the table underneath still showed the filtered rows and the badge still showed a count. Removing the last condition could never be committed at all, because the Apply button disappeared the moment the condition did. Applying a filter also left the panel sitting open over the result, and adding a new filter blanked the table to zero rows before you had typed anything into it
- **A dashboard inside a project opens instead of 404ing**
- Refining the same learning several times in a session is no longer slower the more suggestions there are

Carries three fixes from upstream 0.0.519. The rest of that release — the tool-result transcript, the MCP result budget, the instruction folder work — has not been ported yet.

## Version 0.0.518.3 (August 4, 2026)
- **Google models work again** — every Gemini model on offer had been retired by Google, so a question sent to one came back with "this model is no longer available" and switching to another Gemini produced the same error; the list is now Gemini 3.6 Flash, Gemini 3.1 Pro and Gemini 3.5 Flash-Lite, and a workspace still holding a retired model is moved off it automatically. Custom models are untouched
- **Claude Opus 5 is available**, replacing Claude 4.6 Sonnet in the model list

Carries the model catalog from upstream 0.0.519 only. The rest of that release — the tool-result transcript, the MCP result budget, the instruction folder work — has not been ported yet.

## Version 0.0.518.2 (August 4, 2026)
- **The home page works again** — the agent picker was missing from the composer, so there was no way to choose which agent answered, and clicking any item in the left sidebar changed the address bar while leaving the previous page on screen. Both had the same cause: one component failed to start, which left the page router in a state it could not recover from. A full page reload always looked fine, which is why it read as everything being broken rather than one thing
- Every page is now checked in a real browser before a release — that it loads without erroring, and that it draws its own content rather than just the sidebar

## Version 0.0.518.1 (August 3, 2026)

Eight upstream releases in one step, plus the file-handling work from 0.0.510.15.

**Exported documents stop losing content.** A report exported to PDF could drop parts of what was on screen, and a slide deck exported as something that was not the deck. Images now make it into the PDF as well.

**Power BI works without giving everyone admin rights.** Reading a semantic model used to need a workspace role, so an organisation that shares models item-by-item — which is the normal arrangement wherever row-level security is in use, because workspace contributors bypass it — got nothing. Relationships, model types and measures are read now, and a per-user connection no longer fails to index because it was being asked to authenticate as the system.

**The agent is told more about your columns.** Primary and foreign keys reach the prompt, along with what each column is for — whether it is a measure, what it returns, whether it is hidden. It also stops claiming it can detect row-level security, which it cannot.

**A query that fails says so.** An error inside a query could be swallowed and the answer built as though nothing had gone wrong. The evidence a query produced is also kept, so the planner can see what it actually read.

**Editing an instruction no longer overwrites someone else's edit.** Two changes to the same instruction now stack. The review screen also stopped showing the "Pending review" header twice.

**Starting from a blank report suggests an agent**, and the counts on the agent overview are now shortcuts into the matching section.

**Custom queries work against PostHog.**

## Version 0.0.510.15 (August 3, 2026)

**A Word file is no longer read as a spreadsheet.** When a report held a format the code generator had no reader for, it was handed the filename and left to work out how to open it — and what it reached for was `pd.read_csv`. On prose and markup that call does not fail. Measured against real files: a rich-text document came back as 157 rows of control words, an email as 6 rows of headers, all of it looking exactly like data. The answer built on top was wrong, and nothing anywhere said so.

Which formats fell into that gap was decided by three separate lists of what to *block*, kept in three files, each maintained by hand. Anything nobody had thought to list was treated as readable. That is now inverted: a format is readable only if a reader is named for it, in one place, and the eight formats that were falling through — Word 97 documents, rich text, OpenDocument text and presentations, PowerPoint 97, bitmaps, TIFFs and Parquet — are either read properly or refused with a sentence explaining where to go instead.

**Parquet files now work.** They were in none of the lists, so they were unreadable everywhere. Nothing needed installing.

**HTML, XML, YAML and email files are read as text**, which is what they are.

**A file that cannot be read says why.** A corrupt or truncated document used to come back as a successful read of nothing — indistinguishable from an unsupported format, or from an organization setting that withholds file content. Three different problems wearing the same blank result, and the only way to tell them apart was to guess. Each now says which one it is.

**Files inherited from a folder keep their reader.** A report whose files all came from the folder it lives in was treated as having no files at all, so the tool that opens them was left out — while the files themselves went on being listed for the model to ask about.

**Files reachable from S3 are now reachable from network shares and both Drives.** The four file connections each kept their own idea of what a readable text file is, and no two agreed: newline-delimited JSON was readable from S3 and opaque from a network directory; XML, SQL and Python files were readable from S3 and network directories and opaque from Google Drive and SharePoint. Same file, same bytes, four answers.

## Version 0.0.510.14 (August 3, 2026)

**The sync button opens the sync history.** It used to open a small menu, and the only thing worth clicking in that menu was a link to the history — so seeing what your agents had done took two clicks, and the first one produced a summary nobody had asked for. That summary was also a second copy of what the history already says: today's counts, the recent runs, and what needs you are all pages of it. One click now.

## Version 0.0.510.13 (August 3, 2026)

**Sync history opens over the page instead of replacing it.** It arrived as a full-window view, so opening it made the Agents screen vanish and nothing said you were somewhere temporary — the only way back was one button in the corner. It is now the same centered panel over a dimmed page that All instructions, Connections and the agent trace already use, which also means Escape and clicking away from it work, as they do everywhere else.

**A sync in the list now reads on two lines.** The panel is the same width as every other one, and a run has five things to say: which agent, how it ended, what started it, how long it took, when. On one line the first casualties were what started it and how long it took — the two that answer "was that me, and is it getting slower". The outcome stays on the first line where it is scanned; the circumstances sit beneath it, quieter.

## Version 0.0.510.12 (August 3, 2026)

**Every sync is now on the record, and the record is a screen.** Until now a sync existed only while it was happening: a progress strip that reported the run in front of you and forgot it fifteen minutes later. Ask whether last Tuesday's sync worked, or whether the same workspace has been failing all week, and there was no answer anywhere — the question was reasonable and the product simply could not hear it. Each attempt is now kept: what started it, how long it took, which workspaces answered and which did not, and what went wrong if something did. A button in the agents toolbar says at a glance whether anything is running, anything needs you, or everything is quiet, and opens the full history behind it. Your runs are yours — a Fabric sync reports what your own Microsoft account could reach, so it is shown to you and to nobody else.

**"Needs you" means it actually needs you.** The list of things asking for attention leaves out our own outages on purpose. Those retry by themselves, and putting them on a list headed "needs you" is how a member gets sent to check a credential that was working perfectly — which is exactly what happened during the interruption described in the previous release. What is left is what a person can genuinely act on: a sync that failed for a reason on your side, or a workspace that has now missed several runs in a row and has probably lost access rather than had a bad minute.

**A notification about a sync now opens that sync.** "Two workspaces did not answer" used to invite you to open the agent and see which — to a page that did not list them. It now opens the run itself, where the workspaces, the error and the log are. A sync that simply worked still opens the agent, because that is where you wanted to go.

**One button syncs everything you can sync, without setting your account on fire.** Each of these reads Microsoft with your own credentials against a limit all of them share, so they are queued and run one after another rather than all at once — starting five together makes all five slower and gets some of them refused. Agents it passed over say why: already syncing, or not connected yet.

**Your Microsoft agents do not run overnight, and now they say so.** They read Fabric and Power BI as you, so they can only sync while you are signed in. Members reasonably assumed a timer existed and reported the agent as broken when nothing happened by morning. The schedule tab now states plainly what runs by itself, what waits for you, and — for the parts that are automatic — how often they are checked and how much of today's budget is left.

**App Analytics is switched on, and switching things on no longer needs a server restart.** The usage and cost dashboard has been finished and shipping since 0.0.489.6, and invisible on any installation whose environment did not happen to name a particular variable — eleven releases of a feature nobody could find. It is now on by default and, more to the point, the switch lives in the product: a super administrator turns it on or off from Settings, for the whole installation, and can put it back to whatever the deployment itself prefers. Turning it off stops the data being served, not merely the menu item being drawn.

**Alerts stopped linking to a page nobody else can open.** An internal rename left one setting being read under a name that no longer existed, inside an error handler broad enough to swallow the mistake. The result was not a crash: automation alerts went out for three releases with links pointing at the machine that sent them, which works only on that machine. Everything else about the alert looked right, which is why nobody caught it.

## Version 0.0.510.11 (August 3, 2026)

**A sync that could not finish said nothing, and the agent guessed why.** For about an hour our own database refused new connections. Any Microsoft Fabric sync running in that window stopped part-way — and the step that records "this sync failed" needed a new connection from the same database that had just refused one, so it failed too, and the failure was discarded. The sync stayed marked as running for good: the progress bar never settled, anything waiting on it waited ten minutes and gave up, and the member was left with an agent that could not see their tables. Asked a question about those tables, the agent was handed an empty list with no explanation attached and told the member to attach or refresh their lakehouse. The lakehouse had been attached for weeks. Recording a failure is now retried, and if it truly cannot be written it is reported rather than dropped; a sync whose process did not survive at all is closed out instead of blocking that connection forever.

**Whose fault it was is now part of what a failure says.** A sync can stop because the source refused us — a credential to check, a permission to grant — or because something on our side was briefly unreachable, which passes on its own and is not the member's to fix. The two need opposite words and opposite handling, and they used to arrive as the same untranslated database message. They are now told apart, worded accordingly, and retried accordingly: ours comes back in minutes, theirs waits for a person, because retrying a genuinely wrong credential every few minutes is how an account gets locked out. Where a sync has left the catalog thin, the agent is told that, told which lakehouses did answer, and told not to send the member off to reconnect something that was never disconnected.

**Twenty workspaces synced when three were wanted.** A member who can reach twenty Fabric workspaces had all twenty crawled on every sign-in and every retry, including the ones they never open. Workspaces can now be chosen per person, from the agent page, and the next sync reads only those. Choosing none means none — it is not quietly widened back to everything — and ticking every box is stored as "all of them", so a workspace granted next month is included rather than silently left out by a choice made today.

**A finished sync now says so.** These take minutes, and nobody watches a progress bar for minutes. When one ends, the result arrives in the notification list: what was built and how long it took, which workspaces did not answer if some did not, and the reason if it failed. A sync that finishes in seconds stays quiet — the member was still looking at it. A sync that fails always reports, however quickly it failed, because the quickest failures are the ones nobody would otherwise hear about. Finding no tables at all is reported as something to look at rather than as success.

## Version 0.0.510.10 (August 3, 2026)

**A reply that explained the code instead of writing it was run as code.** Asked to summarise a Word document, the model wrote three paragraphs of reasoning and then the code, and the answer came back as "Execution error: invalid syntax, line 1" — line 1 being the first line of the explanation. The step that pulls code out of a reply only removed the surrounding markers when the reply began with them, so anything written first survived into the run. It now finds the code wherever it sits in the reply, checks that it is runnable before running it, and where there is genuinely none it says so in terms the model can act on rather than reporting a syntax error against a sentence.

**Small numbers printed as zero.** The last release made large totals print in full, using two decimal places. Two decimal places carry a ten-digit total exactly and turn anything below half a penny into `0.00` — so a conversion rate of 0.0034 was handed back to the model as zero, which reads as "none". That is worse than the shortened form it replaced: a shortened number can be read back, a zero cannot. Both sizes appear in the same table constantly, a total beside its share of the total, so the format now serves both.

**A slow query is no longer scanned twice.** When a query passed its time limit the work already running was kept, so that a retry could collect it rather than start the same scan again. That only ever worked while the retry stayed inside one attempt — in practice each attempt built its own set of connections and could not see the previous one's work, so the abandoned scan kept running on the database and an identical second one was started beside it. The kept work now belongs to the whole request. It is never shared between requests: on a connection that signs in as each person, the same query run by two people can legitimately return different rows.

**A tool that never stopped talking was never stopped.** There is a ceiling on how long any single tool may run. It had never once applied — a tool was only ever cut off for falling silent, so one that kept reporting progress could continue indefinitely. The ceiling now works.

**The same clarifying question was asked twice.** One question arrived as two identical blocks with two Submit buttons. The question was only ever asked once and only one was recorded; the second was drawn on screen and would disappear on a reload. While a tool is starting, the screen paints a placeholder for it, and it was placing that placeholder on whichever block happened to be last rather than on the block the question belonged to — and between the two messages that describe one step, "last" had moved.

**A dashboard reply that described the dashboard was stored as one.** There is a check for a reply that says what it is about to build instead of building it, and it looked for words: `return`, `<`, `function`. All three occur in ordinary English — "I'll return the top 5 banners", "revenue < 1M" — so the check passed the very replies it exists to catch, and they were wrapped as a component and stored. Of five replies of the kind the model actually writes, three got through. The check now looks at the shape of the text rather than the words in it.

**The agent can now decline a request it cannot carry out.** Handed a Word document and no database, the part that writes analysis code was told in its own instructions that such a file cannot be opened from code — and then required to produce code that opens it. Asked for the impossible it produced something, which is how the syntax error above was reached; had it produced the empty result instead, the answer would have been an empty table with no error at all. It can now refuse, and the refusal names reading the document as the way to answer. It refuses only when there is nothing else to work with: one readable spreadsheet or any connected database and the request proceeds as before.

100 new tests.

## Version 0.0.510.9 (August 2, 2026)

**A question about a folder was answered from a database.** A report kept inside a project folder had the folder's files described to the model and reachable by none of its tools. The model could see that seven spreadsheets existed and could not open one of them, so it answered from whatever databases happened to be connected — confidently, in detail, and about an entirely different subject, with nothing on the screen to say which material it had used. Reproduced from a clean install: nought files on the report, seven in the folder, two databases connected, and an answer about sales.

Underneath, five separate parts of the system were each working out for themselves which files a request could read, and they did not agree. The list shown to the model was the most generous of the five. There is now one answer to that question and every part asks it. A report in a folder reads the folder; a message with a file attached reads that file first; a report with its own uploads reads those. Nothing is ever put out of reach — naming a database in the question still uses it — and whichever it used, the answer now carries a line saying so.

That line tells the truth about the whole set. It previously described only the narrowest part, so a question with one file attached, asked inside a folder of six more, read "1 attached file" while the analysis had all seven open.

**Long documents stopped after their first page.** Reading past the first page of a Word or PowerPoint file returned nothing usable: the request moved forward through the file by counting bytes, and those formats are compressed archives, so page two was a slice of the compression rather than the document. Documents now page through their text. A reader can also only report reaching the end when there is genuinely nothing after it, rather than assuming.

**Slow queries were killed and the answer given anyway.** A query that passed the time limit was terminated, its results discarded, and the request carried on and answered from whatever else it had. A limit now marks slow progress rather than ending it, and a query that is merely slow finishes.

**An answer built on less than it needed now says so.** Three things could quietly remove evidence from a request — a query that ran out of time, an inspection cut short, a file that could not be opened — and in every case the result was presented as complete. Four months of a six-month range, added up and labelled as the range, is not a smaller answer; it is a wrong one, and on screen it looks exactly like a right one. Where data is genuinely missing the total is now refused rather than estimated, and the answer names what could not be reached.

A request could also stop early at any of five different points and say nothing about which. Each now records why, and the reader is told in one line.

**Pressing stop reported a failure on a run it had stopped correctly.** The run stopped every time. The reply describing it then failed to be built, and the screen showed an error — so the natural response was to press stop again.

**A slide deck that ran out of room was reported as a mistake.** A thirteen-slide deck failed with a syntax error on line 1. It was in fact 37,720 characters of entirely valid code that stopped mid-sentence on line 881, because the model reached its output limit. Being told to fix the error and keep the deck's structure the same, it rebuilt the deck at the length that had just failed. A deck that was cut off is now identified as one, and the instruction is to produce a shorter one.

**The composer no longer asks which files to use.** A picker offered Auto, this folder, attached only, connected data, and everything. It sat next to the agent picker's own "Auto", and everything it offered is decided by what is already on the report. It is gone, and the choice is made for you. What was chosen is reported under the answer, where it is a statement rather than a question.

140 new tests, including two that fail if the recorded scope and the files actually reachable ever disagree again.

## Version 0.0.510.8 (August 2, 2026)

**Refreshing a dashboard rebuilt it from the wrong files.** A chart built from six monthly spreadsheets failed to refresh, reporting "0 of 1 queries refreshed". The files were fine and the chart was fine. When the agent writes the code behind a chart it is handed a numbered list of the files for that request — the first one, the second one — and the code it writes refers to them by their number. Refreshing rebuilt that list from a different source: every file ever attached to the report, in no particular order, and growing every time anything was uploaded. One report had reached nineteen attachments, three of them Word documents, so the first entry in the list was no longer a spreadsheet at all and the refresh died trying to read one.

The crash was the fortunate outcome. Had the first entry landed on a different spreadsheet, the refresh would have completed, reported success, and shown a different month's numbers with nothing on the screen to suggest anything had changed. Every chart from now on records the exact files it was written against, and a refresh re-reads those files and only those, whatever else has been attached since. The same fault existed on a second path — re-running a query from the query editor — and is fixed there too.

Charts created before this release have no such record, and the order they used cannot be worked out after the fact. They keep refreshing exactly as they did while the report's files are unambiguous. Where the file list has provably moved — the same file name attached more than once, or a document no chart can read sitting in the list — the refresh stops and says which of those it found, instead of quietly producing a number. A refusal that names the problem can be acted on. A wrong number cannot.

**The last three releases showed nothing under their headings.** "What's New" listed 0.0.510.5, .6 and .7 and, opened, each was empty. The notes existed; the reader that turns this file into the list only recognised bulleted lines, and those three releases were written as paragraphs. The same reader had been discarding the opening paragraph of every earlier release too — the sentence that says what the release is about — because it came before the first bullet. Both now appear. A test fails if any published release renders with nothing under it.

16 new tests.

## Version 0.0.510.7 (August 2, 2026)

**Your own private instruction is yours to edit and approve.** A person could write a private instruction — one only they can see, that is loaded into nobody else's work — and then be told they did not have permission to change it. Saving an edit failed outright, and a suggested improvement to it could not be accepted. The permission being asked for protects what the organization *shares*, and a private note shares nothing, so it was the wrong question. It is now asked only where something shared is being written.

Three parts of the product already worked this way and were simply never reached: the editing rules underneath have always allowed an author to edit their own note, and they allow it in a deliberately narrow way — the author can change the wording, title, description, category and type, and cannot make it visible to anyone else or push it into what the organization shares. That boundary is unchanged, and attaching a private note to an agent still requires access to that agent. An instruction shared with other people still needs the agent permission, whoever wrote it.

10 new tests, including one that fails if any future write path forgets the author.

## Version 0.0.510.6 (August 2, 2026)

**Uploads now say how far along they are.** A file being uploaded showed a spinner and nothing else, so a large file on a slow connection looked exactly like a request that had stalled. It could not show more: the way the page was sending files gives the browser no way to know how much has gone up. Uploads now use a method that does, and the file shows a filling ring with a percentage and a byte count while it transfers — in the chat composer and in an agent's Files panel, which had the same silent spinner.

The percentage covers the transfer only. When the last byte arrives the work is not finished — the file still has to be read, a spreadsheet may become one table per sheet, and the agent may re-learn — and none of that reports progress. Rather than let a full bar sit at 100% looking stuck, the display switches at that point to naming what is happening. A bar that reaches the end and then waits is the same false impression as a spinner, stated more confidently.

**One screen showed two different totals.** The Agents header said 16 instructions; the list it opens said 21, with both numbers visible at once. The header counted each instruction once; the list added together two overlapping groups — instructions the agent is currently using, and instructions it is not — and an instruction awaiting review that has never gone live belongs to both. It is now counted once. The individual filters still overlap deliberately: one instruction really can be both awaiting review and not yet live, and each filter answers its own question.

13 new tests.

## Version 0.0.510.5 (August 2, 2026)

Three screens that told people something untrue about work the system had already done correctly.

**Suggested changes you were offered but never allowed to accept.** Approving a suggestion requires permission on the agent the instruction belongs to — the server has always checked that, agent by agent. The screen asked a looser question: does this person have that permission on *any* agent at all. Anyone who owned a single agent of their own therefore saw Accept, Reject and Delete on every suggestion in the organization, including for agents they had never had access to, and the click came back as a refusal quoting a bare internal identifier. The controls now appear only where they will work. Where they will not, the suggestion is still readable — that has not changed — and the panel names the agent whose owner has to approve it, so there is someone to ask rather than a dead end.

**Dashboards listed reports, not dashboards.** A report holding a dashboard, a written report and a slide deck appeared as one card, labelled with whichever of the three happened to sort first; the other two existed and opened perfectly well, but only from inside the report. The page now lists each one separately, with its own name, its own kind, and the report it came from underneath, and opening a card opens that specific one. The All / Dashboards / Docs buttons filter what they say they filter — previously they narrowed by report, so a report holding one of each matched all three and every button returned the same card. Slides can be filtered for now too, and each button carries a count.

**Automations said nothing was scheduled while a schedule was running.** Scheduling a report to re-run and scheduling a prompt to run are two different mechanisms, and the Automations page only ever looked at the second. A report refresh was accepted, saved, registered and fired on time, and the page still showed the empty "nothing scheduled yet" illustration — which invites you to create it again. Refreshes are now listed alongside scheduled prompts, each labelled with what it is, when it next runs, and a warning if it is recorded but has no live job. The list was also quietly restricted to your own schedules with nothing saying so; it now says so.

**A scheduled refresh ran at the wrong time of day.** Scheduled prompts fire in the organization's timezone. Report refreshes did not — they used the server's, which is UTC everywhere we run — so the same "8:00 AM" chosen in the same product meant two different moments, and in Yangon a refresh set for 8 AM ran at 2:30 PM. Refreshes now use the organization timezone as prompts do. Changing that setting also used to leave every existing schedule on its old timezone, because the time zone is fixed when a schedule is registered; all live schedules are now rebuilt when the setting changes.

Also fixed, found while building the above: the check protecting other people's scheduled prompts from being listed was written against one particular spelling of the request rather than against what the request does, so a different spelling returned them. It now gates on the behaviour.

38 new tests.

## Version 0.0.510.4 (August 2, 2026)

Three faults in the agent's Files panel, all reported as "the upload failed" and none of them an upload failure. Every request involved returned success; what was wrong was what the screen did afterwards.

- **A file you had just uploaded said it had not been ingested.** The panel added the file to its list using the reply from the upload itself — but two of the things it displays, *what was done with this file* and *why*, are worked out when the list is fetched, not when the file is stored. They were therefore blank, and a blank reads as "uploaded but not yet loaded into anything; the agent cannot use it", complete with an offer to re-ingest work that had already finished. The table had been built the whole time, and reloading the page proved it. The panel now re-reads the list after an upload, which is what a reload was doing for you by hand.

- **Nothing else on the screen was told.** The agent tree, its file count and its table count are kept by the surrounding page, which loads them once. Uploading, converting, re-ingesting or removing a file changed none of them — so a newly uploaded file was genuinely missing from the tree, not merely mislabelled, until the page was reloaded. Every one of those actions now announces itself and the tree re-reads that one agent.

- **Converting a file gave no sign that anything was happening.** There was a progress indicator, and it was correct — but it lived inside the Convert button, which is only visible while the pointer is resting on that row. Choosing a destination closes the menu and the pointer moves away, so the one thing showing the state disappeared. Meanwhile the conversion re-reads the whole document and can take a while. Progress now sits on the row itself: where the file is going, what is being done to it, and a moving bar, none of it dependent on where the mouse is. The row's own buttons are withdrawn while it runs, because clicking Convert a second time used to start a second conversion.

- **Uploading several files at once triggered a full re-learn for each one.** The upload route documents that the interface should ask for the re-learn only on the last file of a batch, "so the agent learns once per batch instead of once per file". The creation wizard does this; this panel did not, so six files meant six re-learns competing with each other. Fixed, and likely a contributor to the panel feeling slow.

- Thirteen new tests cover all of it, including that the progress indicator is not inside a hover-only element and that every conversion destination has its own progress wording. They were run against the previous code first and eleven of them failed there. One of them also caught a destination — "Skill" — that the first version of this fix had no wording for.

## Version 0.0.510.3 (August 2, 2026)

Six defects — five found by reviewing the previous release rather than by anything failing, and one reported from a live installation.

- **An ordinary member had no "New" button at all.** Members were meant to be able to write instructions for themselves and build an agent from their own uploaded files; both were already permitted by the server, and neither was reachable. Creating an instruction had been wired to the permission that *approves* and *deletes* them, which only administrators hold — and because the menu appears only when at least one of its entries is allowed, that single mistake also took away the "Data Agent" entry members were entitled to. A member can now create an instruction, which is private to them and applies only to agents they can already use, and can create a Data Agent from uploaded files. Connecting a database or BI tool remains an administrator action, deliberately: it reaches shared infrastructure. This was fixed by correcting the two gates, **not** by widening the member role — granting members the manage permission would have handed over bulk deletion, deleting other people's instructions, approval and repository push along with it.

- **A feature switched off with the word "off" was switched on.** These settings are stored as free-form values, and every gate asked whether the stored value was "truthy". In Python the text `"off"` is truthy — so an administrator who disabled a capability by writing `off`, `false`, `no` or `disabled` got it **enabled**. Through the normal toggle this never happened, because a toggle writes a real yes/no; it applied to anything set by API, script or direct database edit. There is now one reader for every on/off switch in the product, it understands the spellings people actually type, and anything it cannot make sense of leaves the capability **off** — an unreadable setting is not consent. It also logs the misconfiguration once, naming the setting and the value, so it can be corrected rather than silently tolerated.

- **Publishing a report checked who you were but not which organisation the report belonged to.** Not exploitable — the only route into it already restricted publishing to the report's owner, and we confirmed that end to end. But the check lived in the route rather than in the code that does the work, so it depended on every future caller remembering. It is now enforced where the work happens.

- **The live-status watcher scanned the whole completions table every two seconds.** It runs per organisation, per worker, for as long as anyone has a tab open, and the column it filters on was not indexed. Adding the right index turns that scan into a lookup that never touches the table at all — measured against a copy of real data. It costs nothing today and progressively more as history accumulates, which is exactly the kind of thing that is invisible until it is not.

- **After a network blip, the activity dots could stay stale.** On reconnect the interface re-checks the reports it is tracking, but it can only ask about a hundred at a time — and it was asking about the hundred it had seen *earliest*, which after a long session are the ones no longer on screen. It now asks about the most recent hundred, and stops accumulating every report id a tab has ever displayed.

- **A test job that could never pass.** The unit-test run was given thirty minutes for work whose fixture overhead alone is closer to forty, so it was killed on essentially every run — and a check that always fails stops being read. It now has enough time and runs two tests at once. Deliberately two and not more: the end-to-end job next to it records that four workers pushed memory high enough for the runner to kill one mid-test, with no traceback to explain it.

## Version 0.0.510.2 (August 2, 2026)

Two ways the new report-activity feature let people see reports they cannot open. Both arrived with upstream v0.0.510 and were found by reviewing it before deploying.

- **The live-status endpoint answered questions about any report in the organisation.** `GET /reports/activity` takes a list of report ids and returns whether each one is running, queued, waiting on someone, or has errored, and when it last changed. It checked only that the reports belonged to your organisation — but the list of ids comes from whoever is asking, not from the server. Anyone with permission to see the reports list could therefore name a report they have no access to and learn whether it is running and when it last did something. The rule that decides which reports you may see now lives in one place and this endpoint uses it; ids you cannot see simply do not come back.

- **The live update stream sent every report's activity to everyone in the organisation.** The dots on the reports list are fed by a stream that was built per organisation rather than per person. The interface hid the ones you had no business seeing, but they were still on the wire — visible to anyone who opened their browser's network tab. Each connection now carries its own list of what that person may see, and the stream sends nothing outside it. If that list cannot be worked out for any reason, the stream sends nothing at all rather than falling back to sending everything.

- **The change is one rule, not two copies.** Both the reports list and the activity endpoint now call the same visibility check. Copying the rule into a second place is how the original problem happened, so it is deliberately defined once.

- Four new tests pin this. They were run against the previous code first and failed there, including the case where a report shared with a *different* person must not become visible to you.

## Version 0.0.510.1 (August 2, 2026)

Upstream v0.0.510 ported onto this fork — 110 upstream commits, 230 files. Seven new database migrations, which for the first time in this project's history needed no re-anchoring: two of them hang directly off the revision this installation was already sitting on.

- **Instructions can be organised into folders, per agent.** Nested folders, drag an instruction from one to another, rename and delete freely. Deleting a folder moves its instructions back out rather than destroying them, and the folder path is passed to the agent as a hint only — it never changes which instructions apply. So this is organisation for people, not a new scoping rule.

- **Microsoft OneNote can be connected as a source**, and an agent can search, grep and read notebook pages including the images embedded in them.

- **An agent can now drive a browser** — navigate, click and type, extract structured data, take a snapshot, and look at the rendered page. Worth knowing before you enable it: this is outbound network access from the agent, on a product that otherwise keeps everything on your own machine.

- **A run that hits an unexpected error no longer dies.** Every iteration of the agent loop now recovers and retries from the last saved point — a database hiccup or a crashed step costs a retry rather than the whole answer. Prior work in the run survives, so it resumes mid-flight instead of starting over. The allowance is an organisation setting (default 2), and when it runs out the run switches to the next model in the fallback order before giving up. Never applied to a user pressing stop, or to an exhausted budget.

- **The report list shows what is happening now** — a live dot for a report someone is running, backed by a per-user record of what you last looked at.

- **Failed scheduled runs can send an email**, and Google profile details can be synced into agent context alongside the Entra ones already supported. Both arrive switched on upstream; check where the email sends from before it sends.

- **Live updates no longer travel over a WebSocket.** Upstream removed that endpoint — it was unauthenticated, and its per-worker connection list silently dropped events for anyone connected to a different worker, so some people simply stopped seeing updates with nothing reporting a fault. Reports now stream from the database instead, which every worker can see.

### What we kept that upstream would have taken away

- Built-in skills stay locked against editing. Upstream reverted that lock; their text is re-seeded from the image on every upgrade, so an edit made through the interface would have vanished at the next release with nothing said.
- A guard asserting that Fabric SQL tokens must not carry the Fabric API scope. That scope breaks Fabric SQL, and upstream's version of the same test no longer checks for it.
- The artifact frame keeps this fork's sandbox settings. Upstream still permits same-origin access from artifact code; we removed it deliberately and solved the messaging problem it caused.
- Two settings pages, two prompt behaviours and the deck design system merged as unions rather than replacements — details in the commit.

### Also

- Our Entra profile-sync panel and upstream's new Google one are now the same component used twice, which removed about 240 lines of duplication without changing what either does.
- Recorded, in the code, why multi-tenant Power BI is deliberately absent from the delegated-token list: it leaves the tenant blank on purpose, and the token mint rejects a blank tenant. The two lists are meant to differ on exactly that one connector. The last time a pair like this disagreed with nothing explaining why, it cost a day.

## Version 0.0.503.11 (August 2, 2026)

- **A browser stuck on the old interface can now recover on its own.** The most stubborn version of "I upgraded and nothing changed" is not a cache at all — it is a service worker, a small program a browser keeps and lets intercept every request for a site. This product does not install one, but a browser that picked one up from an earlier build, or from something else once served on the same address, keeps it. The browser re-fetches that program from time to time to decide whether to replace it, and the rule is simple: if it comes back missing, the worker is removed. Ours never came back missing. Any address that did not match a real file was answered with the application's own opening page and a success code, so the browser received a web page where it expected a program — neither valid nor absent — kept what it already had, and went on serving the old interface indefinitely. A hard refresh does not help, because that clears the cache and not the worker. Requests for a file that is not there now answer *not found*, which is what the browser needs in order to let go. Application addresses are unaffected and still open the app, including ones with a full stop in them such as a report named `q3.final`.

- **A missing part of the interface now says so.** The same fault made any absent script or stylesheet arrive as a web page, so the browser reported a parsing error about an unexpected character — which reads like a corrupted build rather than a file that was never there. The error now names the real problem.

- **The installer put every server into development settings, silently.** It always applied the development configuration on top of the production one, with no option and nothing said about it. That publishes the database on a port of its own and serves the site without encryption, so on a cloud machine whose firewall allows that port the database was reachable from outside using the password in the configuration file. Production is now what you get by default; asking for development takes an explicit flag and prints what it costs. A production install also refuses to start without a domain name, because without one the certificate service quietly issues a certificate for `localhost` — the site reports itself healthy while every browser refuses it.

- **The upgrade instructions now work out which configuration a machine actually uses instead of assuming.** The two configurations store their data in different places, so building with the wrong one hands the application empty storage: it starts, passes its health check and offers a fresh sign-up screen, while every report and account sits untouched somewhere nothing is reading. Nothing reports an error. The written procedure now begins by asking the running system which configuration it was started with, and every later command repeats that answer.

## Version 0.0.503.10 (August 1, 2026)

- **Slide decks now follow a design system instead of being improvised each time.** Decks were readable but plainly machine-made, and the reasons turned out to be specific rather than general. No typeface was ever named, so every deck fell back to the same default sans — pairing a serif for headings with a sans for body is most of what makes a document look like somebody designed it. There was no description of an opening slide at all, so the cover was invented from scratch each run. And there was nothing repeating from slide to slide: no small label above the heading, no explanatory sentence beneath it, no footer, no page numbers — which is what makes a set of slides feel like one document rather than a folder of pictures. All of that is now specified, along with eight described slide layouts (cover, section divider, metrics, chart with insight, actions, process, comparison, two-panel) chosen according to what each slide is doing.

- **Two decks on different subjects no longer look the same.** The six colour palettes already existed but nothing tied the choice to the material, so decks drifted toward whichever came first. The palette is now chosen from the subject and held for the whole deck: a retail review and a customer-contact review arrive as recognisably different documents while following the same structure.

- **Smaller corrections found by looking at generated decks.** A ranked bar chart was drawing its categories bottom-up, so the largest sat at the bottom and the ranking read upside down. The footer was taking its text from the report's internal title, which is a working label somebody typed to find the thing again — one such label reached the footer of every slide in a test deck, and the footer is now written from the data and the question instead. Decks take roughly twice as long to produce than before, which is the cost of the extra composition.

## Version 0.0.503.9 (August 1, 2026)

- **Agents published to everyone were being refused to everyone except administrators.** Publishing an agent is meant to make it available across the organisation, and the permission code has always had a rule at the very top saying exactly that: if this source is published, allow it. That rule needs the source itself in order to look at it, and the check that decides whether somebody may use an agent was passing only its reference number. The rule was skipped every time, and the question fell through to a second check that lists only the sources a person has been individually granted — a list whose own note says, in as many words, that published sources are not in it and the caller has to add them. Nobody added them. The result was precisely inverted on this instance: an ordinary member could use the single source that had *not* been published, and was refused all three that had been. It reads like caution, which is why it lasted; a permission check that wrongly says no looks safe, while quietly taking away what people were meant to have. Both places that ask the question — pinning an agent to a report, and opening the connection behind it — are fixed by the same correction, so a member can once again ask a question and have the assistant choose a published agent for them.

## Version 0.0.503.8 (August 1, 2026)

- **A single mistaken line in generated code no longer costs the whole slide deck.** Building a deck works by writing PowerPoint code and running it, and until now if that code hit an error — one wrong method name was enough — the deck was abandoned and nothing came back. On this instance that had already happened to one deck in seven. It now gets one more attempt: the error the interpreter reported is handed straight back, with an instruction to fix that specific problem and change nothing else about the deck, and the corrected code is re-checked for safety exactly as the first attempt was. If the second attempt fails too, the original error is what gets reported rather than a confusing second-hand one. The particular mistake that caused the failure — treating a shape's outline as though it were its fill — is also now named directly in the instructions given when a deck is written, so it should stop happening rather than being repaired after the fact.

- **Five tests were failing for a reason that had nothing to do with the code they tested.** A stand-in used by the Power BI scanning tests had been written to copy the real component's arguments by hand, and when the real one gained an argument the copy did not. Every tenant then failed silently, the scan returned nothing, and five tests failed on assertions that never explained why — the cause was buried in a warning. The stand-in now accepts whatever it is given.

## Version 0.0.503.7 (August 1, 2026)

- **Slide decks are now checked for text that runs off the slide, and the result is told to you.** The check itself already existed but was switched off, and even when it ran its findings were written to the database and read by nothing — so a deck with a paragraph spilling past the bottom edge was delivered exactly like a perfect one. Three things changed. The check is on: it renders every slide and measures where the text actually lands, which costs about a second per slide against a deck that takes minutes to build. Its findings are now reported in plain words when a deck is finished — "2 slides have text running past the slide edge (slides 3, 5)" — instead of vanishing into a column nobody reads. And, most importantly, the check can no longer stay quiet about its own failure: previously a clean deck and a check that never ran at all (a missing tool, a render that timed out) both came back as an empty list of problems, which meant switching it on could have produced confident silence about decks nobody had actually looked at. It now says which of the two happened, and when it could not check it says so rather than implying the deck is fine.

- **A deck whose text does not fit can now be rebuilt once, automatically.** When the check finds text outside its box, the offending slides and what is wrong with them are handed back and the deck is regenerated — with the instruction to make it fit by shortening, splitting across a slide, or resizing, and explicitly not by dropping any finding. The rebuild is written alongside the original and is only kept if measuring it proves it has strictly fewer problems; otherwise the original deck stands and the reason is recorded. A regenerated deck is not assumed to be a better one. This is off by default: measuring a deck is cheap and safe, whereas rebuilding one costs a second model call in time and money, so turning on the cheap half does not silently opt you into the expensive half.

## Version 0.0.503.6 (August 1, 2026)

- **Nothing was checking that a skill's one-line summary fits the list it is shown in, so the previous release's problem could simply happen again.** The 160-character limit was written directly into the code that builds the list, and there were no tests covering skills at all — which is why all three shipped skills sat over the limit for a week without anyone noticing. The limit is now a single named value that the list builder, the new test and the editor all read from, so it can no longer be changed in one place and quietly left behind in another. Along the way a second copy of the same trimming rule turned up, with its own hard-coded numbers, on the path that advertises instructions rather than skills; it had the same defect and now uses the same value. A new test fails the build if any shipped skill is written too long, if its line comes back trimmed, or if it no longer says when it should be used — and the test also checks that trimming still happens when it should, so a guard that has quietly stopped working cannot pass unnoticed. Finally, writing an over-long summary in the app now records a warning naming the instruction and showing where the text would be cut. It warns rather than refuses: a save that is rejected halfway through an edit is worse than a line that gets shortened.

## Version 0.0.503.5 (August 1, 2026)

- **The built-in skills were being advertised with their explanation cut off mid-sentence.** A skill is offered to the assistant as a single line — its description — and that line is the only thing it reads when deciding whether the skill is worth opening. The list allows 160 characters and trims anything longer. All three shipped skills were written longer than that (198, 216 and 181 characters), so each was presented ending in an ellipsis part-way through the sentence that says when to use it: "…Read befor", "…recursive CT", "…from a GROU". All three have been rewritten to fit, and the shipped-skills version has moved so existing installations pick up the new text on their next sign-in. No rule inside any skill changed, and none of the guidance itself was ever affected — only the one-line summary used to choose between them. To be accurate about the benefit: the skills were being opened before this change as well, so this is a correctness fix to the selection line rather than a repair of something that was failing outright.

## Version 0.0.503.4 (August 1, 2026)

- **A shared report could give someone data they are not allowed to see.** When the assistant picks its own agent, the choice is stored on the report. If that report was then opened by a colleague with narrower access, the connection was still being opened for them, because the check used to confirm access was the one that decides which agents a person can *see* in the list, not the stricter one that decides which they may actually *use*. On this instance the two disagreed for three of four agents. The connection is now opened only after the same check the agent picker itself rejects with, so a person asking a question on someone else's report gets exactly the agents they are entitled to, and the log says plainly when one was withheld. Introduced by the 0.0.503.1 fix in this fork; stock v0.0.503 is not affected because it never opened these connections at all.

## Version 0.0.503.3 (August 1, 2026)

- **Two error messages that reported failures which had not happened.** Whenever the assistant chose its own agent, the log recorded "mid-run client construction failed" for a connection that had in fact been opened, and "focus-on-use: commit failed" for a change that had in fact been saved. Both came from a logging helper being called from outside the function it is defined in, which raised after the real work was already done and was then reported as that work failing. Nothing was broken by it, but anyone reading the logs would have chased a fault that did not exist, and it made the "no errors on startup" check permanently untrue. Present in stock v0.0.503 as well; reported upstream.

## Version 0.0.503.2 (August 1, 2026)

- **No wasted first attempt when the assistant picks its own agent.** After 0.0.503.1 the question was answered correctly, but the first query still failed and the chat showed a red step before the retry worked: the assistant routinely decides which agent to use and runs the first query in the same breath, and the connection was only being opened at the start of the next step. It is now opened the moment the agent is chosen, so the first query is the one that succeeds.

## Version 0.0.503.1 (August 1, 2026)

- **Asking a question without picking an agent now works.** The new automatic agent context found the right agent and then could not query it: every attempt came back "No active tables matched the requested patterns", and after three tries the answer was that the task could not be completed. Choosing an agent, or a project, avoided it — which is exactly the step the feature exists to remove. The cause was that focusing an agent deliberately does not attach it to the report, while the code that opens the database connection only looked at attached agents, so the assistant could see the agent's tables and never had a connection to query them. It now opens connections for everything it can see, re-checking access rather than trusting the stored list. Reproduced on a stock v0.0.503 image as well, so this is not specific to this build; the fix is being reported upstream.

## Version 0.0.503 (August 1, 2026)

Upstream v0.0.503, ported onto this fork.

- **The assistant finds the right agent itself.** Until now someone had to pick an agent before asking a question. It can now search your agents by name, description and your own recent usage, and set the report's context on its own. If you *have* picked agents — including by choosing a project — that choice stands: the assistant must ask before widening beyond it, and does so with an Allow/Deny prompt rather than quietly adding sources.
- **Shorter answers with less narration.** The system prompts were rewritten to be smaller and faster, and to end on findings rather than a description of the work. The rules this fork added — treating a data-quality warning as a stop sign, refusing confidence over an unexplained discontinuity, naming the column an aggregate came from, and taking qualifiers from the data rather than assumption — are all retained on top.
- **Research steps collapse into one live line.** The chain of tool activity in a chat now shows as a single status line you can expand to the full sequence, instead of a growing wall of steps.
- **A report can be shared with a group.** Sharing accepted named people only; it now accepts a group, so access follows directory or workspace group membership instead of a hand-maintained list.
- **Relative dates stay relative.** Generated query code is saved and re-run on dashboard refresh and on schedules, so a question about "yesterday" that was frozen into a literal date returned permanently stale numbers on every later run. The code generator is now told to derive such windows at execution time.
- **Better failure text on connected sources.** An expired sign-in on a file source produced a raw provider error in the chat; it now says which source needs reconnecting and what to do.

## Version 0.0.502.11 (August 1, 2026)

- **A turn that says it is building something now has to build it.** Asked for a slide deck, the agent replied "Building a four-slide dark-navy CEO deck from the existing banner, trend, product, and channel data.", ran no tools at all, created nothing, and recorded the turn as a success — so the message said work was under way while none was. It happened intermittently; the same request sent again produced the deck. The answer and the turn contradicted each other and nothing compared them. Now, when the agent finishes on its very first decision without having called a single tool, and its reply announces that something is being built, it is sent back to plan again rather than allowed to finish. Bounded by the existing retry limit, and narrow by design: finishing after real work, or an answer that merely describes an artifact, is untouched.

## Version 0.0.502.10 (August 1, 2026)

- **Chart text is no longer invisible on a slide.** python-pptx starts every chart's text black and does not inherit the slide background, so on the dark deck theme the chart title rendered as an empty gap — on every chart, in every deck. The generator is now told to retheme chart text, and the worked example it copies from does so itself.
- **Charts use the deck's own colours.** A pie or doughnut left at the default rendered in Office blue, red, green and purple next to a deck built from an entirely different palette.
- **Labels are not cut short where they fit.** The example the generator follows capped every category label at 20 characters, so full names were truncated in side panels with room to spare.
- **Decks stop showing database column names.** Titles, axis labels, series names and KPI captions were taking raw column names straight from the query — a slide read "Revenue = net_amount", and footers cited `fact_sales.net_amount`.
- **The summary panel above a dashboard now describes the whole dashboard.** Two faults compounded: the code picking the time column took the leftmost one that parsed, choosing `year` over `year_month` on a monthly result, which broke the chronological ordering it relies on; and the row cap handed the summariser the most recent 60 rows of 718 without saying so. The panel then opened with "In October 2025..." above a dashboard covering three years. The finest time column now wins, and a truncated block declares how much it is hiding.
- **The headline is checked like everything else.** Findings had every figure verified against the data; the headline was published as written, on the assumption it carried no figures. It routinely does. It is now held to the same standard and dropped if it fails.
- **A rejected summary is retried once, and says when it gave up.** Findings citing an unverifiable figure were discarded silently and finally — one dashboard kept a single bullet out of five and reported nothing unusual. The summariser now gets one more attempt with the specific sentences that failed, and any findings still dropped are stated in the panel.

## Version 0.0.502.9 (July 31, 2026)

- **A super admin's password can no longer be changed from inside the app.** Not by themselves, and not by another super admin. There is no account above a super admin to put a mistake right, and an installation with no mail server has no reset link either — so a password changed in error, or by somebody sitting at an unlocked session, would lock out the only privileged account permanently. The Password section explains this rather than disappearing, and both password routes refuse it outright, so the explanation is a courtesy and not the rule. Changing it now requires direct database access, which is the point.
- One consequence closed along with it: a super admin could previously have been given a password with "require a change at next sign-in", and would then have been stuck for good — the forced-change gate refuses every page except the change-password screen, which is exactly the screen a super admin is not allowed to use.

## Version 0.0.502.8 (July 31, 2026)

- **A super admin can set a local account's password.** Members gained a Set password action, alongside a Sign-in column that says where each account actually authenticates. Until now a person who forgot their password had no way back in at all: the sign-in page hides Forgot password when no mail server is configured, and there was no administrative reset — the only remedy was hashing a password by hand and writing it into the database. The new dialog can also require a change at next sign-in, which is on by default, so a password the administrator knows survives exactly one use.
- **You can change your own password.** A Password section in the profile modal asks for the current password before accepting a new one. It refuses accounts whose password lives in a directory and says where to change it instead.
- **Setting a password now proves something.** `PATCH /users/me` accepted a bare new password and asked for nothing else, so anyone holding a session — a borrowed laptop, a copied token — could permanently take an account over without knowing the old password. That field is gone; both password operations run through routes that demand either the current password or super-admin authority, and a super admin cannot use the administrative route on their own account.
- **Accounts that sign in elsewhere are left alone.** SSO, LDAP and SCIM accounts are shown as such and refused by both password routes, because the password they authenticate with is not stored here. Directory accounts could not previously be told apart from local ones at all — every account carries a stored password, including the random one generated when a directory provisions someone, so the directory identity is now recorded when it signs in.

## Version 0.0.502.5 (July 31, 2026)

- **You can choose a project before asking the first question.** The folder control only appeared once a report existed, and a report is not created until you send — so on the home screen there was nothing to file and the control hid itself, leaving no way to say where a report should go. It now appears there too: the choice is held while you type and applied as the report is created, which also brings the project's default agents with it.
- **The project menu no longer opens off the bottom of the screen.** On a report page the composer sits against the bottom of the window, and the menu is placed above it — unless it happened to be short enough to technically fit below, which is exactly the case when you have one project and the report is not yet filed in it. A one-row menu cleared the edge by two pixels, so it stayed below, and the page clips rather than scrolls, so it simply vanished. The menu now opens upward wherever the composer is pinned to the bottom, and keeps opening downward on the home screen where there is room.

## Version 0.0.502.1 (July 31, 2026)

- **A Power BI model shared with you directly is now found on its own.** Power BI lists only the workspaces you hold a role in, and offers no way at all to ask which models have been shared with you — so a model shared item-by-item, which is the normal arrangement wherever row-level security is used, appeared in nothing the product read. The reports and dashboards you can open, however, each name the model they are built on, and that listing IS available. Those are now read as an index of the models the workspace listing cannot see.
- **A model the connector cannot read is now reported, with the one thing that would fix it.** Every refusal used to be discarded, so a dashboard you have access to and a dashboard that does not exist looked exactly alike: a table that simply was not there. Refusals are now kept and sorted by what actually resolves them — a model you can open as a report but not query needs Build permission granting, which takes an admin a minute; a model built over a Fabric Lakehouse or Warehouse cannot be read through Power BI at all and belongs on the Fabric connector, where no permission grant would ever have helped. Where the cause is genuinely unclear, it says so and quotes Power BI's own words rather than guessing.
- **A Power BI model you reach through two tenants no longer loses one of its copies.** Tables from every tenant were merged into a single list keyed on the model and table name alone, with nothing to say which tenant they came from — so where two tenants held a model of the same name, the second one crawled silently overwrote the first, before the merge was ever written. `Usage Metrics Report` is built into every Power BI tenant, so anyone signed into more than one was reliably losing a table. Colliding names are now qualified with the tenant they came from — every claimant, so which tenant "wins" no longer depends on the order they were discovered in — and names claimed by only one tenant are left exactly as they were, so nothing is renamed for the sake of it.
- **A semantic model shared with you directly can now be indexed on a first sign-in.** Power BI lists only the workspaces you hold a role in, so a model shared with you item-by-item — the normal arrangement under row-level security, where people are deliberately kept out of the workspace so the security rules actually apply — appears in no listing at all. The product already goes looking for such models, but it looks among the models it has already indexed, so on a first sign-in there is nothing to look from and the model can never get in. A connection can now be given those model IDs directly. Access is unchanged: each one is checked against your own permissions and dropped if you cannot query it.
- **Models skipped because of a rate limit are now reported instead of quietly dropped.** Checking a directly-shared model costs a query, and Power BI limits those, so beyond a fixed number the rest were left out of the catalogue with only a line in a log to say so — a short catalogue looks exactly like a complete one. They are now listed with the reason alongside the other models that were found but could not be read.

## Version 0.0.502 (July 31, 2026)

- **A shared dashboard now works for the person you shared it with.** Until now, opening a dashboard built on per-user data — anything behind a personal sign-in or a row-level-security policy — showed the viewer an empty frame or, worse, someone else's numbers. The saved snapshot is deliberately withheld from anyone but its owner, so the viewer was being handed nothing and the page rendered it as a broken artifact. A viewer now gets a **Run** gate instead: the dashboard's queries re-execute under their own identity and the result is stored against them alone. One person's run can never change what the owner or another viewer sees.
- **Whose credentials a viewer's run uses is now the report owner's choice** — the viewer's own (each person sees their own slice) or the creator's (everyone sees the same figures). Set per report.
- **A refreshed dashboard clears every viewer's cached copy**, so nobody keeps reading a stale slice of a report that has since changed.
- **Slide exports and previews respect the same rule** — a viewer can no longer pull a rendered PPTX or preview image containing data the dashboard itself would have withheld from them.
- **Editing an instruction now targets the exact passage you meant.** Long instructions were edited by matching text that could appear more than once; the tool now anchors on surrounding context and refuses an ambiguous match instead of guessing.
- **Power BI: a connection whose service account can see nothing is no longer rejected as broken.** In a fully row-level-secured tenant the shared account legitimately indexes zero models, and the connection test failed on that alone — even though the members using it could query perfectly well. Models discovered by users' own sign-ins now count.
- **Power BI: the permission-cache flush no longer stalls sign-in.** It is rate-limited, and a retry loop could hold an interactive sign-in or reload for up to a minute on a call that does not need to succeed.

This release carries upstream 498, and completes 499, 501 and 502. Upstream 500 was never published. Upstream's three alembic **merge** revisions for this release are deliberately not ported — they reconcile a branch that does not exist in this tree, and one of them depends on a revision we do not have; `svr0001` is re-pointed onto our own chain instead, which keeps a single migration head. The reasoning is recorded in the migration file itself.

## Version 0.0.501 (July 31, 2026)

- **Sign in with Snowflake** — a Snowflake connection can now authenticate each person as themselves instead of sharing one warehouse account. An admin creates a Snowflake OAuth security integration, saves its client ID and secret on the connection, and each member signs in once; every query then runs under that person's own Snowflake role, so the warehouse's own row and column policies apply per person rather than to a single shared login. Where the connection pins a role, the sign-in requests that role explicitly so the token authorizes exactly what the query asks for.
- **Per-user auth modes are now defaulted from one rule** — the create form and the edit form disagreed about which sign-in methods a per-user connection allows, and the data-source form set none at all, which silently disabled the sign-in route for connections created that way. All three now derive the default from the same place.
- **Google sign-in on BigQuery no longer fails with `invalid_scope`** — the request was carrying a Microsoft scope Google rejects outright.
- **The pending-review badge and the "+" buttons now match what you are allowed to do** — a member without review rights was shown a pending-changes count they could not open, and "New instruction" and every tree "+" were offered on agents where the create would be refused. Resolved suggestions also stop being re-rebased on every load, so the count settles instead of drifting.

This release carries upstream 501. The port is **selective**: upstream 498 and 500 are not in this build, so the viewer-dashboard changes 501 makes to the artifact frame are deliberately held back — they depend on a component 498 introduces. Upstream 499 was already delivered in 0.0.494.12. The version number tracks the upstream release ported; work of our own on top of it takes a `.N` suffix.

## Version 0.0.497.1 (July 31, 2026)

- **Custom queries (beta)** — an admin with connection-manage rights writes SQL on a connection, and the product re-runs it on a schedule into an encrypted local copy that agents query instead of the source. A legacy Oracle or SQL Server box stops seeing an agent's exploratory bursts entirely. Agents get a real SQL engine over the cached result — joins, CTEs and window functions work regardless of what the source supports — and are told how fresh the data is so they can say so. Activation is per agent (off by default for new ones), and the cached copy is encrypted at rest. Available for PostgreSQL, MySQL/MariaDB, SQLite, SQL Server, Oracle, Snowflake, BigQuery and Microsoft Fabric; off by default under **Custom queries** in AI settings.
- **Row-level security on custom queries** — a cached copy holds every row the connection's credential could see, so a policy can filter it per person against their synced profile attributes (department, office), their groups, or their roles, with per-group and per-role grants and a "sees everything" escape hatch. Enforcement is structural rather than a filter bolted onto generated SQL: each request gets a private catalog containing only the rows that person may read. An unresolved identity sees nothing rather than everything, and **Preview as** shows exactly what a chosen member would get before the policy is saved. Policy changes are recorded in the audit log with both the old and new rule.
- **A burst of agent queries no longer arrives at one source all at once** — each connection now has a concurrency cap (default 4, editable per org and overridable per connection), so a fragile on-prem database queues rather than being handed every parallel scan at once.
- **A timed-out query is now cancelled at the source** — previously the product stopped waiting while the statement kept running on the database. The trace records whether the source actually stopped, so "we gave up" and "it stopped" stay distinguishable.
- **Connection pools are dropped when a connection changes or is deleted** — editing a host or deleting a connection no longer leaves authenticated sessions open against the old target until they age out.
- **Generated code can no longer read a hardcoded file path** through pandas/numpy/pyarrow/duckdb. Reading an uploaded file through the same functions stays legal, because its path arrives as a value rather than a string the model typed out.
- **Table usage stats are matched by row rather than by name** — a custom query named `album` and a source table named `Album` were collapsing into one bucket, so one relation displayed the other's usage count, and usage is an input the planner ranks tables by.
- **Databricks connector updated** to pick up a patched Thrift (CVE remediation), and Sybase SQL Anywhere gained an extraction source.

Ported from upstream 0.0.496/0.0.497 plus the acceleration parts of 0.0.502; the `.1` records that this is a selective port rather than a straight upstream release (upstream 0.0.498-0.0.501 are not included). Microsoft Fabric and Sybase acceleration ship unverified against a live engine upstream, which is why the whole feature stays behind the beta switch.

## Version 0.0.495 (July 31, 2026)

- **The Agents page stops waiting on work it never displays** — a build snapshots every instruction in the workspace, so the table recording them grows as (builds x instructions), and the pending-review check rediscovered which of those rows were real changes on every page load by scanning the whole accumulated draft history. One workspace scanned 1,040,940 rows to find 260 actual changes, and the cost grew with editing history rather than with anything on screen. Each row now records at write time whether it is a real change, so the sweep reads the changes themselves.
- **Pending dots no longer cost a document comparison each** — deciding whether a suggestion still applies means re-aligning it against text that has moved on, which is quadratic in the length of the instruction: a 15,000-character instruction took over three seconds on its own. The badges and the list now answer from equality alone, which is exact wherever equality settles it and optimistic only for a suggestion whose base has drifted; opening the instruction runs the authoritative comparison and the dot clears itself. Comparisons also run off the request loop and share one cache per batch, and are capped so a very long instruction degrades to a single whole-text change instead of stalling the page.
- **The "All instructions" badge counts each instruction once** — a proposed instruction the live build was not yet carrying was counted both as pending and as not-live, so the badge reported more instructions than the workspace contains: 220 against a real 139.
- **The agent picker loads once, not three times** — the picker, the mention menu and the selection watcher each fetched the same agent list on every page, and again after creating a report. They now share one request and a short freshness window, and connecting an agent forces a refresh.
- **The agent list stops counting a catalog nobody asked it to count** — that list computed an aggregate over every table in the workspace on each call, which was 81% of the response time, for a number none of its consumers render. It also issued a separate credential lookup per connection; both are now batched, taking one measured workspace from 546 queries to 6.
- **...including for connections that sign in per user** — that list also counted each signed-in member's own visible tables with a query per connection, which on a workspace where every connection uses per-user sign-in is a query for every agent in the picker. Those counts are skipped there too; the places that do show a table count are unchanged.

## Version 0.0.494.19 (July 31, 2026)

- **Power BI works for people who are kept out of the workspace** — under row-level security end users are deliberately given access to a semantic model rather than a role in its workspace, because Contributor and above bypass RLS. Every query was still addressed to the workspace-scoped endpoint, which requires that role, so those users got a permission error on every question while the same model answered fine at the tenant level. Queries now fall back to the tenant-level endpoint on a permission error and remember the result per workspace, so the retry is paid once rather than on every query.
- **Models shared with you directly are no longer missing from the catalog** — the workspace listing only returns workspaces you hold a role in, so a model shared with you item-level appeared in no listing at all and silently dropped out of your catalog. Known models the listing missed are now probed and kept if you can genuinely query them, with the number probed bounded and reported rather than quietly truncated.
- **The connection test stops failing people who actually have access** — having no workspace role is the normal shape under row-level security, but the test reported it as a failure. It now verifies query access against the models you can reach before reporting a permission problem, and says what to ask an admin for.
- **The truncation guard follows the same route as the query it checks** — its row-count probe built the workspace-scoped address itself and returns nothing on a permission error, so for exactly these users it would have gone silently blind and let undeclared truncation back through.

## Version 0.0.494.18 (July 31, 2026)

- **Wide cells no longer blow up the context** — a result whose single cell held a multi-megabyte payload put roughly 1.1 million tokens into one observation. The row budget bounded how many rows a preview showed but nothing bounded how wide one value could be, and the first row was admitted regardless of size, so it escaped the budget entirely. Cell values are now clamped before any byte accounting, in the tool previews, the query context section and the statistics block, with the clipping stated in the preview note rather than left silent.
- **Charts are built from the data, then refined by the model** — the visualization used to come entirely from one model reply, so a single bad field (a column name that does not exist, a value that arrived as a list, a placeholder echoed back) produced a chart type with nothing renderable in it: an empty chart beside a data tab full of rows. The chart is now derived from the result set itself, which can only reference columns that exist, and the model's answer is layered on one validated field at a time. A bad field costs that field, never the whole chart.
- **The agent can read across a project** — reading and searching reports now covers reports in projects you can view, not only your own, and files inherited from a project resolve like uploads.
- **Scheduled refreshes stop when there is nothing left to refresh** — archiving a report hides the conversation but does not unpublish its artifact, so a shared dashboard keeps being served and its refresh must keep running. A refresh is now skipped and unscheduled only when the report is archived AND its artifact is visible to nobody, decided when the job fires so every ordering converges on the same outcome.
- **Picking a model turns the router off** — choosing a model on a message, or pinning one to the conversation, now disables automatic routing for that run instead of letting the planner switch away from the choice. Queued turns resolve their model the same way streaming turns do.
- **Instruction pickers show every instruction** — the mention picker and the agent panels paged through the full set instead of stopping at the newest 50, which had silently made the rest unmentionable, and rows carry a body preview so a titled instruction shows its opening line.

## Version 0.0.494.17 (July 31, 2026)

- **All instructions** — a new view across every agent at once, opened from the button beside the instruction count on the Agents page. The tree browses one agent at a time; this lists the org's whole instruction set, filterable by agent, state and free text, and it is the only place instructions the live build is not carrying are visible.
- **Instruction changelog** — a second tab showing every change to the org's instructions, newest first. Each entry is one build with the net effect it had — how many instructions it added, altered and removed — so a change that touched hundreds reads as a single row. Expand an entry to see exactly which instructions it touched. Filter by agent, by who made the change, and by source (a person, the agent, a git sync, or a rollback). Builds with no net effect are hidden by default.
- Both views are addressable: the URL carries the open tab and state filter, so a link reproduces the exact view rather than describing where to click.
- Opening an instruction from a list now shows its opening lines immediately instead of a blank pane while the full text loads.

## Version 0.0.494.16 (July 31, 2026)

- **Projects** — shared folders that group reports. A project is private to its owner until it is shared, carries its own description, colour and project-local instructions, and can hold default agents that new reports inside it start with. Files added to a project are inherited live by every report in it, including ones created before the file was added.
- Reports can be moved between projects from the chip in the message box, or in bulk from the project page; the sidebar lists your projects above recent reports and tints each report's icon with its project colour.
- A project is a sharing boundary: anyone who can see the project can read its reports and fork them, while only the report's owner can add turns to it. Reports opened this way show a read-only bar with a Fork to edit action.
- The agent knows which project it is working in — project instructions and a listing of sibling reports are put in front of the model so it can build on related work with read_report instead of repeating it.
- Inside a project the agent picker's "Auto" means the project's default agents rather than every agent in the organisation.

## Version 0.0.494.15 (July 31, 2026)
- **Instructions without a title read as "Untitled" in the knowledge tree** — the row label fell back to the instruction body, which the tree no longer loads, so every body-titled rule lost its label. It falls back to the preview the light row does carry.

## Version 0.0.494.14 (July 31, 2026)
- **The knowledge tree loads every instruction in a group** — each group asked for one capped page of 200 while its badge counted the whole set, so a group past that number showed a partial list under a number that disagreed with it, which reads as instructions going missing. Groups and the Pending changes view now page the light list to completion.
- **The report agent panel no longer stops at 200** — its two instruction lists page instead. They keep the full row rather than the light one, because these rows render the author chip and an inline body excerpt that the light projection does not carry; paging is what removes the truncation, not the lighter row.
- **A metadata-only save can no longer blank an instruction body** — the detail pane re-read its draft from the tree cache after saving, and both save paths sent the whole body on every update. With a body-less tree row that would have written the instruction back empty. The body is now omitted from an update unless it was actually loaded, and a refreshed tree row is merged into the open detail rather than replacing it.

## Version 0.0.494.13 (July 31, 2026)
- **An instruction list can now return more than a page** — every list, tree and picker asked for one capped page of 200 and rendered it as though it were the whole set, so an organisation past that number simply stopped seeing its oldest instructions, with nothing on screen to say a page had been cut. Rows now load through a light projection (`GET /instructions?view=light`) that drops the instruction body — which is 81% of a row, carried three times over — and the light page may run to 2000. A request for more full rows than the cap is refused rather than quietly trimmed, because silently returning 200 of the 1000 asked for is the same failure in a new place.
- **"Select all" now selects all** — the bulk update and delete paths asked for 10,000 rows in one request, which the endpoint rejects outright, so the list of ids came back empty and the action reported success having changed nothing. They page through the light list instead, and a failed page raises instead of returning a short list that looks complete.
- **Instructions the live build is not carrying can be counted** — the list has always meant "what is in the live build", so an instruction that stopped reaching the agent became unreachable through the API as well: no view could show it and no count included it. `live=false` returns exactly that set, and the counts carry `not_live` alongside the totals.
- Known and tracked: the knowledge tree and the report agent panel still request a single capped page. The endpoint now serves the whole set; those two surfaces adopt it next.

## Version 0.0.494.12 (July 31, 2026)
- **A tool approval that is answered now takes effect** — pressing Allow or Deny appeared to do nothing and the waiting run never woke. The poll read the confirmation's status after rolling back its session, and a rollback expires every loaded row, so the first read raised `DetachedInstanceError` and was swallowed as a failed poll. The status is now read while the row is still attached, and the run resumes on the answer instead of waiting for a timeout.
- **Denying a tool once no longer denies it forever** — an expired confirmation was reported as a decision rather than as "no decision yet", so the next call inherited the last refusal instead of asking again. Expiry now means unanswered, which is what it is.
- **An approval says who gave it** — the decision carries the name of the person who allowed or denied the call, so a shared report shows "Allowed by …" rather than an anonymous state change. Translated into all ten languages.

## Version 0.0.494.11 (July 30, 2026)
- **One switch above all the agents** — Auto learn was a decision made agent by agent, which left two questions unanswerable. Whether anything at all has fallen behind is a question about the whole set, and asking it one agent at a time means never asking it. And what the automation costs is a total, not a per-agent number: twelve agents at four runs each is forty-eight model calls a day nobody agreed to. There is now a single switch and a single daily budget for the organisation, with each agent still able to opt out. The organisation's switch wins — turning it off stops everything at once instead of requiring a visit to every agent.
- **The budget is shared, counted as it is spent, and says when it is reached** — a limit that stops quietly cannot be told apart from an automation that has broken.
- **Checking now does not mean ignoring the budget** — the button runs the scheduled pass early, with every guard it normally applies.
- **The training panel no longer shows another agent's run** — opening a second agent left the previous one's training on screen under the new name, so Power BI reported reading sixty-three tables, which is Microsoft Fabric's schema. A run over an hour was also written as minutes and seconds, so seven and a half hours read as "451:07".

## Version 0.0.494.10 (July 30, 2026)
- **The training panel no longer shows another agent's run** — opening a second agent left the previous one's training on screen under the new name, so Power BI reported reading sixty-three tables, which is Microsoft Fabric's schema, alongside an elapsed time from a run hours earlier. Everything else on the page was reset when the agent changed; this was missed.
- **A long run is no longer reported as if it were short** — an elapsed time over an hour was written as minutes and seconds, so seven and a half hours read as "451:07", which anyone would take for seven and a half minutes.

## Version 0.0.494.9 (July 30, 2026)
- **Auto learn** — one switch on each agent. With it on, the agent looks after itself: it reads any file it has never read, and rewrites its overview when its tables no longer match what it was last taught. With it off it still tells you when it has fallen behind and waits to be asked, because noticing costs nothing.
- **Reading a file is what adds instructions** — a document nobody sorted contributes no rules, no knowledge and no table; it is attached and inert. The pass that reads it is therefore also the pass that gives the agent new instructions, and it runs before the overview is rewritten, so the overview describes the tables that reading those files produced rather than the ones from before.
- **It acts only where somebody asked** — a scheduler existing is not permission. Agents without Auto learn are left entirely alone, a limited number are handled per pass so a large installation cannot enqueue unbounded work, and anything left over is reported rather than quietly skipped.
- **The Train button no longer stays disabled after the run has finished** — training is asked for and answered in one request, and a real run takes minutes, so the button read "Training…" and refused clicks long after the panel beside it had reported the run complete. It now follows the progress tracker, which is the thing that knows first.

## Version 0.0.494.8 (July 30, 2026)
- **The Train button no longer stays disabled after the run has finished** — training is asked for and answered in one request, and a real run takes minutes, so the button read "Training…" and refused clicks long after the panel beside it had reported the run complete. It now follows the progress tracker, which is the thing that knows first.

## Version 0.0.494.7 (July 30, 2026)
- **Only one thing reports a training run now** — with the run panel open, the progress strip in the middle of the page and the panel beside it were both describing the same run, and had already drifted apart: the strip sat frozen at the first of four steps while the panel reported the run finished. Two readings of one event, with nothing to say which was right. The panel holds everything the strip did and more, so the strip is what shows when the panel is closed rather than a second opinion, and the same applies to the out-of-date notice.

## Version 0.0.494.6 (July 30, 2026)
- **Training now has a panel beside the agent, not just a bar that disappears** — the four stages were already shown while a run was live, then collapsed and left nothing behind: no account of what the run produced, and a failure that looked exactly like a slow success. The panel stays, says what was read and what was rewritten, shows a failure as a failure along with the fact that the previous overview is untouched, and can be reopened later without training again.

## Version 0.0.494.5 (July 30, 2026)
- **An agent now notices when its own description has gone out of date** — the overview an agent applies to every question names its tables and explains how to use them. Until now nothing recorded what that description was written against, so when a table was removed or a column appeared, the data moved and the description did not. The agent kept following a briefing about data that had changed, and nothing said so — a wrong answer built on a stale briefing looks exactly like a right one. Each training now records the tables and columns it read, and the agent page shows a notice, naming what changed, when they no longer match.
- **Noticing is free, so it is on; re-learning is not, so it is not** — detecting the difference is a comparison of two things already stored, with no model involved. Rewriting the overview costs a model call every time the data moves, which on a busy connection would be all day. Every agent can therefore say when it has fallen behind, and none start spending without being asked.
- **Row counts deliberately do not count as a change** — the overview describes what the data is, not how much of it there is. Re-learning because a table grew would fire constantly, change the text not at all, and teach people that the notice is not worth reading.
- **Training is available on every agent, and means the right thing on each** — on a shared connection it writes one overview for the organisation; on Microsoft Fabric and Power BI, where each person signs in with their own account and sees their own tables, it writes an overview private to them. The button had been hidden from exactly those people, whose view nobody else can teach.
- **Pressing Train now shows the stages immediately** — the progress it reports was already there, but only noticed on a five-second cycle, so the seconds right after the click showed nothing at all.

## Version 0.0.494.4 (July 30, 2026)
- **The agent page now has a refresh** — its counts were read once when the agent was opened and never again, so uploading a file or reloading tables somewhere else left the header insisting on "0 tables, 0 files, 0 instructions" over an agent that visibly had them. Nothing was wrong with the agent; the page had simply stopped looking.
- **An agent can now be trained from its own page** — reading its tables and rewriting the overview instruction was reachable from exactly one place, the Save & Learn button on the Tables tab. An agent whose table selection had never been re-saved therefore could not be taught at all from the page showing "No primary instruction", which is where anyone wanting to fix that is standing. The button runs in the foreground and reports what happened, because it costs a model call and a silent one invites a second press.
- **Removed files no longer keep their storage forever** — removing a file hides it and leaves the contents on disk so a mistake can be undone, which is right, but nothing ever reclaimed that space, so it grew with every removal for the life of the installation. Contents are now cleared once a file has been removed for thirty days. A file the product has no record of at all is deliberately left alone: it looks like litter, but the same thing appears when a file is written a moment before it is recorded, and deleting on that basis turns a small bug elsewhere into lost data.

## Version 0.0.494.3 (July 30, 2026)
- **Removing the last file left its table behind** — every earlier removal pruned correctly, but the final one did not: the schema refresh deliberately refuses to remove everything, because for a database an empty result usually means the connection broke rather than that the data is gone. It cannot tell those two apart, and a file source with no files left is the second one. Removing the last file now retires its tables directly — and only in that case, where emptiness is a fact just written rather than a result that might be a failure.
- **A retired table stayed on the list** — marking it inactive was not enough, because the tables list shows inactive and removed rows alike. A table whose file is gone is now removed outright, along with the usage and feedback records describing it.
- **Converting a file now asks before replacing what it produced before** — converting is normally a correction, so replacing the earlier filing is the right default, but the thing being replaced is the passages the agent reads or the rules written from the document, and neither is visible from the row being clicked. It asks, and names what goes. It stays silent when there is nothing to replace, or when "keep the current filing" is ticked — a warning that fires when nothing is at stake is how people learn to dismiss the ones that matter.

## Version 0.0.494.2 (July 30, 2026)
- **Removing a file from an agent now asks first** — that action used to detach a file and leave everything built from it in place, so acting on a single click was survivable. Now that it also withdraws the table built from the file, a single click destroys something the user cannot see: the tables live on a different tab. It asks before removing, and names what goes with the file — the table, or the passages the agent reads from a document.

## Version 0.0.494.1 (July 30, 2026)
- **Removing a file left the table built from it behind** — uploading a spreadsheet or CSV turns it into a table the agent can query. Removing the file took it off the list and stopped there: the table stayed, still active, still answering. The file appeared to be gone while the agent went on reporting from data that had been withdrawn, and nothing anywhere said so. Removing a file now withdraws its table too, and says that is what it did. Spreadsheets are covered as well, which needed a record of the tables each one produced — a workbook is split into a table per sheet, and nothing previously connected those tables back to the file they came from.
- **Every uploaded document was sorted by an AI whose reasons were thrown away** — each file is read on upload and filed as data, a rule, a procedure, or reference material. That decision came with a confidence and a one-line reason, both of which were written to a log nobody reads. All that remained on screen was a coloured badge, so a well-founded call and a coin-flip looked exactly alike, and a wrong one could not be spotted. The reason is now kept and shown beside the badge, along with how sure the AI was and whether it read the file's contents or judged it by shape alone.
- **A file's filing could not be changed** — once a document had been sorted there was no way to disagree. The panel offered nothing, and neither did the interface behind it; re-running the sorter simply asked the same question again and usually got the same answer. Files can now be converted to a rule, a procedure, reference material or a table. The conversion rewrites the document properly — definitions become stated rules, a walk-through becomes ordered steps — rather than pasting it in raw.
- **Where a document goes decides when the agent sees it, so the choice is now described that way** — a rule is in front of the agent whenever it might matter; a procedure is fetched when a question matches it; reference material is searched only if the agent thinks to look. A set of metric definitions filed as reference reading is the quiet failure: the agent answers without them, confidently, and nothing reports a problem. Each option now says when the agent will read it, instead of naming a category.
- **Converting a file left its previous filing in place** — so a correction added a second opinion rather than replacing the first, and converting twice stacked another copy. A conversion now retires what the file produced before, with the option to keep both for a document that genuinely serves as two purposes.
- **Clicking a Word or PowerPoint file showed nothing** — the panel offered a download and the message that there was no preview for this type, while the text of that same document had already been extracted, cleaned and stored as the passages the agent reads. That text is now shown, so the document the agent is reasoning from can be checked without leaving the page.

## Version 0.0.494 (July 30, 2026)
- **What this release brings in from upstream** — versions 0.0.491, 0.0.492 and 0.0.494 in full, together with the part of 0.0.493 that 0.0.494 cannot run without. The remainder of 0.0.493, and versions 0.0.495, 0.0.497 and 0.0.498, are not included yet and are being taken in later, deliberately, one at a time. (Upstream has no 0.0.496.)
- **A tool connection could hand back data that the product then quietly discarded** — when an agent calls out to a connected tool or API, the result is saved as a file so the rest of the answer can be built from it. That file records who asked for it, and the product was looking for the person under a name the agent never uses, so it found nobody. Because a file must have an owner, saving it failed every single time it happened inside an agent's run. Nothing was reported: a table came back as a short preview that could not be worked with, and anything that was not a table produced no file at all. The person simply got less than they asked for, with no indication why.
- **The agent could try to query a tool connection as though it were a database** — a connected tool is reached by calling the tool, not by writing a query against it. It was nonetheless being offered to the code-writing step in the same list as real databases, described as something you can query. So whenever the data needed was not already to hand, the agent wrote a query against the tool connection, using a method that does not exist on it, and the attempt failed. The error named the code rather than the list it had been given, so it read as the agent's mistake. Tool connections are now kept out of that list, while remaining fully available through the tool itself.
- **A tool's answer was treated as unreadable whenever it arrived wrapped** — services rarely return a bare list of records; they wrap them alongside paging and type information. The product only recognised a table when it was the outermost thing in the response, so an ordinary wrapped reply — a hundred and fifty contacts sitting under a heading — was filed as unreadable text. It was stored as a raw blob instead of a table, and every later step, including the preview and any code written against it, had to guess at its shape. The rows are now found inside the wrapper.
- **A report can now refresh when somebody opens it** — previously the only choice was a repeating schedule, which either runs when nobody is looking or leaves the page stale between runs. Setting it up asks one question with three answers — never, on a schedule, or when opened — rather than two separate settings that could contradict each other.
- **Two people opening the same report seconds apart could each trigger a full rerun** — the guard meant to collapse a crowd of viewers into a single rerun was measuring against the clock rather than against the data. Two viewers a second apart could land either side of an invisible boundary and both run the report; the same guard would then refuse a genuinely needed rerun for the remainder of that period. It now keys on the data the viewers actually read, so everyone looking at the same stale page shares one rerun, and the next real refresh is never held up.
- **Approving a tool part-way through an answer could go unnoticed** — where a tool asks permission before it runs, the approval was remembered only by the part of the product that received the click, which is usually not the part waiting for it. The wait is now recorded where every part can see it, so a click is acted on wherever it lands.
- **Which tools an agent may use can now be chosen per connection** — a connected service often exposes far more than an agent needs, and the whole catalogue was offered.
- **Reading a file from a connected document library could fail and then loop** — the agent listed files on a SharePoint connection, was given their identifiers, and asked to read one back without naming which connection it came from. The product looked only among the files attached to the conversation, did not find it, and reported that the identifier was not a file in this conversation. The agent read that as a bad identifier, listed the files again, received the same one, and repeated. The identifier was always correct; only its source was unstated. Where the agent has exactly one file connection, that is now used. Access is unchanged — the person must still be permitted to reach it.
- **Two ServiceNow settings had no test covering them anywhere** — one turns off certificate checking to reach an internal service, the other reads the structure of the data instead of asking the system to describe itself. Both fail silently when broken: the first keeps working while doing the opposite of what was asked, and the second is only noticed when a specific installation cannot be read at all. Both are now covered.
- **Groundwork for describing connected tools to the model directly** — present but switched off, so nothing changes for anyone yet.

## Version 0.0.490.18 (July 29, 2026)
- **An answer could state a figure that appears nowhere in the data it was drawn from** — the written summary beside a dashboard could quote a total, a count or an average that matched none of the figures the question actually produced, because those numbers were carried over from an earlier stage of the model's own working rather than read back from the finished result. Seen repeatedly: one answer named a peak of 49 billion directly beside its own table showing 48. Every figure in an answer is now checked against the data that answer was built on, and anything that cannot be justified is removed rather than published. The check that already protected the summary panel above a dashboard now protects the written answer as well.
- **A long number on a dashboard card could still be cut off** — the previous release fixed this for the product's own cards, but a dashboard's cards are written fresh for each question, and those were still being given a fixed size with no room to shrink. Dashboards now use one shared card that fits whatever it is given, whether that is two characters or thirty, and the release checks look for any text that does not fit its box.
- **The product could warn about a problem that was not there** — the check that looks for figures behaving oddly could mistake a calendar label for a measurement. Where a result carried the month both as a date and as a plain number, December followed by January read as a collapse from 12 to 1, and the product raised a data-quality warning about it. A column that simply restates the time period is now recognised as a label and left alone. A warning that is wrong teaches people to ignore the real ones.
- **One question could leave two documents behind** — where the product wrote a document and then revised it while still answering, both the draft and the revision were kept and both appeared current, so the abandoned one could be opened by mistake. A revision made while answering now replaces the document it is revising. Revising a document from an earlier question still keeps the previous version, which is what version history is for.
- **The limit on how long the product may spend examining data before answering could not be changed** — it behaved correctly but was not offered anywhere, so there was no supported way to adjust it. It is now an ordinary setting alongside the other limits.

## Version 0.0.490.17 (July 29, 2026)
- **A dashboard could show a total that was too low, with nothing saying so** — a result is kept in two sizes: a small copy for the on-screen preview and a wider copy for building a dashboard from. Three different parts of the product decided for themselves which copy to read, so a dashboard could be built and drawn from the first thousand rows of a much larger result and simply add up less than it should. Worse, when the product noticed a result was too big and correctly summarised it first, that summary existed only for as long as the dashboard was being written — everything that drew the dashboard afterwards went back to the original thousand rows, which did not even have the same columns. There is now one place that decides which data an artifact is built on and drawn from, so every part of the product sees the same thing, a dashboard can no longer be built on a partial result, and anything that was reduced says so on its face.
- **The same dashboard could show one number on screen and a different number in its PDF** — the same cause as above, seen from two sides. Both now read through the one place, so they cannot disagree.
- **The product did not notice when one of its own numbers was obviously wrong** — a monthly chart had one month sitting at about a sixth of the months on either side of it, while that same month had the most records of the year. A figure collapsing while the volume behind it rises is not a business result, it is a broken number, and the underlying cause was that most records for that month had nothing in the column being added up. It was drawn, written about, and published with the words "confidence is high". The product now checks the shape of its own results — a value that moves sharply while the volume behind it does not — raises it plainly, and will not claim confidence over something it cannot explain.
- **The product could quietly change its mind about which figure it was reporting** — where more than one column could reasonably answer a question, one answer used one and a later answer in the same conversation used another, without saying so, and the totals differed. It now states which it used and stays with it.
- **The download button on a dashboard did nothing at all** — dashboards are drawn inside a deliberately locked-down frame, and that lock also blocks downloads. Nothing appeared and no error was shown, so the button simply looked broken. Downloads are now permitted from that frame; the lock is otherwise unchanged.
- **A PDF placed inside a dashboard or report could not be displayed** — the frame that draws artifacts is intentionally treated as a stranger to the rest of the site, so it was not allowed to fetch the file. The page around it now reads the file and hands the contents in directly, which needs no such permission.
- **Large numbers were written in full in Word and PowerPoint exports** — a chart axis that reads 4.3B on screen was printing every digit in an exported file, because each part of the product had its own idea of how to write a number. There is now one definition, shared by the screen and by every export.
- **A currency symbol could be shown that the data never specified** — figures were printed with a dollar sign regardless of what currency they were actually in. A unit is now only ever shown when the data supplies it; where it is unknown the number is shown plainly, because a wrong unit is worse than none.
- **A large figure could be cut off by the card holding it** — the last digits of a long number were simply not drawn. Numbers now fit the space they are given.
- **A chart could show the same label twice** — where two different groups happen to share a name, both bars were labelled identically and looked like a mistake. Repeated labels are now qualified so each one identifies what it actually is.
- **One request could produce two dashboards** — when the product improved on its own work during a single answer, it left the earlier attempt behind as a second, separate dashboard that a person could open by mistake. A request now produces one dashboard, and an improvement replaces it rather than sitting beside it.
- **Export options were offered that could not work** — a slide deck offered a PDF download that always failed, because the rule about which formats suit which artifact was written down in two places and they had drifted apart. It is now stated once, and the options shown are the options that work.
- **A single question could spend an unbounded amount of time looking around before answering** — one question spent over four minutes, most of it exploring rather than answering. There is now a limit on time spent looking before an answer is given.

## Version 0.0.490.16 (July 29, 2026)
- **Every dashboard showed "Dashboard failed to render — React is not defined"** — dashboards, charts and slides are drawn inside a locked-down frame, and the security work in an earlier release tightened that lock so the frame can no longer be treated as part of the site around it. Two of the drawing libraries were being requested in a way that asks the server for explicit cross-site permission first; the server grants none, because these files are our own and are served from the same place as everything else. The browser therefore refused those two files and nothing could be drawn. The request now goes out the ordinary way, as it already did for the other three libraries, which is why only these two ever failed. The lock itself is unchanged — loosening it would undo the security fix, so it is now also guarded by a test. Exports to PDF, Word and PowerPoint were never affected: those are drawn on the server, which is why the fault survived a full release of testing.

## Version 0.0.490.15 (July 29, 2026)
- **Signing in to Outlook, SharePoint and OneDrive could never finish** — when somebody clicks "Sign in with Microsoft", the product tells Microsoft where to send them back afterwards. It built that return address from a setting whose shipped default is a placeholder that no browser can reach, so a person would authenticate successfully at Microsoft and then land nowhere. The return address is now taken from the address the person is actually using, so it is correct on a laptop, on a company domain, and after a domain change, with nothing to configure. It is also worked out once at the start of sign-in and carried through the rest of it, because Microsoft compares the two copies character by character and rejects the sign-in if they differ by so much as a slash — while saying nothing about which part was wrong.
- **The sign-in security cookie was not marked secure on encrypted sites** — the product decided whether to mark it by reading that same placeholder setting, which always answered "not encrypted". It now reads the connection the browser actually used. A failed sign-in also used to send people back to the placeholder address instead of the site they came from.
- **Large numbers lost their digits in the agent's own working notes** — a query result is printed back to the agent so it can read what it found, and Python shortens long numbers, turning a total of 2,332,757,360 into 2.332757e+09. Amounts in Kyat routinely run to ten digits, so this affected ordinary questions rather than unusual ones, and answers came back saying "see chart" or quoting a figure rebuilt from a rounded column. Printed figures now keep every digit. Only the printing changed — stored data and every number the product returns were already correct.
- **Heavy use by several people at once produced database errors** — a quota check made from a background worker opened its own short-lived connection handler over the shared pool, which then failed once that handler was discarded. Nothing visible broke, but the errors were real and grew with the number of people working at the same time. The check is now handed back to the connection the rest of the application uses. Verified by repeating the same three-person test: four errors before, none after.
- **The sample company's chart rule encouraged the agent to skip the numbers** — it asked for a chart without saying the figures must still be written out, and answers sometimes replaced a value with "see chart". It now states that a chart accompanies the numbers and never replaces them, and that figures must be quoted from the exact column rather than rebuilt from a rounded one.
- **The libraries that run inside every dashboard are now part of the product** — the charting, layout and JavaScript-translation libraries used to be fetched from the public internet each time the product was built, from addresses that did not all name a fixed version, with nothing checking what came back. Two builds a month apart could therefore contain different code, and one of these libraries had already moved to a new major version without anyone noticing. They are now stored with the product, each recorded with a fingerprint that is checked, so every build contains exactly the same reviewed code, the build no longer needs the internet, and a change can be seen rather than discovered.
- **A leftover build folder could hide those libraries** — the product looked for them in two places and accepted the first folder that was not completely empty. An abandoned folder from an earlier build containing a single unrelated file therefore won, and dashboard and PDF export failed with a message blaming a missing download, which was not the problem. It now accepts a folder only if it actually holds the libraries.

## Version 0.0.490.14 (July 29, 2026)
- **A staff member could take over somebody else's automation** — a report can have automations attached to it, and each one has an identifier that the product publishes openly in report data. When somebody changed, deleted or reset one of those automations, the product checked that they were allowed to touch the report named in the request, and then acted on whatever automation identifier was sent — without ever checking the two belonged together. So anyone holding an identifier from another report, in another company on the same installation, could repoint that automation at themselves and have it run work in the owner's name. The automation must now belong both to the report the person is allowed to touch and to their own company, and a request that fails either test is answered as though the automation simply does not exist, so nothing is confirmed to a guesser.
- **Private notes were readable by every other member** — a note marked private is deliberately published into the shared set of active rules, because a rule only its author can see cannot wait for somebody else to approve it. Two ordinary requests then returned that shared set in full, including everyone's private text, to any member. The same text was reachable a second way through the comparison view, and a private note could also surface in another person's list of pending changes. All four routes now leave out notes that belong to somebody else. The filter is deliberately not applied where the product publishes or restores a set of rules, because hiding a note there would quietly drop it out of the set it belongs to.
- **One company could take over sign-in for the whole installation** — the product picked the staff directory that authenticates sign-in by scanning every company's own settings and using the first one it found. Any company administrator can write those settings. On an installation hosting more than one company, one of them could point sign-in at a directory they control and then sign in as anybody, because a directory that approves any password makes the product hand over the account for whatever address was typed. The installation must now name the company whose directory it trusts; where there is only one company — which is every installation of this product today — nothing changes at all. Where there is more than one and none has been named, no company directory is used and the reason is written to the log.
- **Dashboards ran with the same privileges as the page around them** — a dashboard is code the product writes from your data and runs in your browser, inside a frame that is supposed to isolate it. Two settings on that frame cancelled each other out and left it with no isolation from the application at all, which put the signed-in session within its reach. The frame is now genuinely isolated. The one thing that had to change with it is how the page hands the dashboard its data, which is why this had gone unfixed.
- **Exports could be used to reach inside the network** — producing a PDF of a dashboard or document runs a real browser over that same generated code, on the server, with nothing stopping it making requests. Anything it could reach — an internal service, a cloud provider's credential endpoint — could be fetched and drawn into the page, and the page comes back as the PDF. All four places that render this way now refuse every outbound request, and they start the browser with its own protection turned on where the machine allows it, falling back and saying so where it does not. A dashboard that points at a picture somewhere on the internet will show that picture missing in the exported file; that is the same door being closed.
- **An uploaded logo could carry a program** — logos may be uploaded in a format that is technically a document rather than a picture, and the product served it back unchanged. The check for anything dangerous was a list of five phrases, applied only to the first part of the file, on a file allowed to be more than twice that long — so padding it was enough to walk past. The product now understands the file and allows only the parts that draw: shapes, colour, gradients and text. Anything else, including anything that would not open cleanly, is refused.
- **Generated code was checked on the server but not on your laptop** — when work is set to run on your own machine, the safety check that inspects generated code before it runs was in the branch used only when your machine is unavailable. Code sent to a laptop was never inspected. The check now runs before the work is sent anywhere, and the description in the code that claimed otherwise has been corrected.
- **A sign-in service switched off in a Kubernetes installation stayed on** — turning one off in the installation settings had no effect whatever, because of how the template read the word "false". Off was not merely ignored; it could not be expressed. Verified by rendering the real template both ways: off now means off, and leaving the setting out still means on.

## Version 0.0.490.13 (July 28, 2026)
- **Somebody who already had an account but no workspace stayed in no workspace** — when a single sign-on service recognises an address that already exists here, the product correctly reuses that account instead of creating a second one. It stopped there. Anyone who happened to belong to no workspace — because they were removed from one, or were invited before any existed — signed in perfectly and landed in an empty product with nothing to ask about. They are now placed in a workspace like anyone else, and only when the sign-in service is one an administrator has trusted to admit new people. Existing members are unaffected.
- **Accounts created through single sign-on now use people's real names** — everyone who signed in through Keycloak, Entra or any other single sign-on service was named after the first part of their email address, so a company of two hundred arrived as two hundred accounts called things like `emp001`. Their real names were being sent by the sign-in service in the very same message the product already reads to find their email address, and were simply stepped over. The product now uses the name it was given, falling back to the email address only when the service genuinely sends no name. If somebody already has an account here with a name they chose, signing in through the directory never overwrites it; an empty one is filled in.

## Version 0.0.490.12 (July 28, 2026)
- **A whole office could not sign in** — the sign-in limit counted every attempt against the address it came from, and it counted a correct sign-in exactly the same as a wrong password. Everyone in one office, or on one company network, or on one remote-desktop server, arrives from a single address as far as the product is concerned. Tested with a directory of two hundred staff all signing in correctly through one address: twenty were let in and one hundred and eighty were turned away, for five minutes at a time. A successful sign-in now hands its attempt back, so the limit counts failures, which is what it was always for. It is deliberately given back one at a time rather than cleared, so nobody holding one working account can wipe their own failed guesses. The limits can now also be set per installation, because the right number depends on how many people share an address and no built-in figure can know that.
- **People signing in from the company directory bypassed the licensed user limit** — the limit was applied when the product synchronised groups from the directory, and not applied at all when somebody simply signed in, which is how nearly everyone actually arrives. A company licensed for fifty could gain any number. Measured: two hundred directory accounts admitted themselves past the limit without a single check running. Sign-in now checks, and refuses with a clear message rather than creating an account that belongs nowhere. The note in the code claiming that every route enforced this has been corrected, and there is now a test that fails if it ever becomes untrue again.
- **New accounts from the directory now use people's real names** — everyone arrived named after the first part of their email address, so a directory of two hundred people produced two hundred accounts called things like `staff001`. Their real names were in the directory the whole time and were simply never read. The product also asked for the wrong field by default: it looked for a name field that the most common directory software does not have at all, so even a correctly configured server returned nothing. It now reads the field that is always present, and an installation using Active Directory can still choose a different one.
- **Someone who already had an account but no workspace stayed in no workspace** — when the directory recognises an address that already exists here, the product correctly reuses that account instead of making a second one. But it stopped there. Anybody who happened to belong to no workspace — because they were removed from one, or signed up before any existed — signed in perfectly and landed in an empty product with nothing to ask about. They are now placed in a workspace like anyone else. Existing members are unaffected, and a name someone has already chosen is never overwritten by the directory; an empty one is filled in.

## Version 0.0.490.11 (July 28, 2026)
- **The product name can now be changed from Settings** — the name shown on the sign-in page, in the browser tab, in the page footer and throughout the interface is now an administrator setting rather than something fixed when the software was built. It can be changed at any time and takes effect immediately, with no rebuild and no restart. A logo and a browser icon can be uploaded, and an accent colour set. The name is served on the sign-in page too, which is shown before anyone has signed in and before any workspace is known, so the setting deliberately belongs to the whole installation rather than to one workspace. Every screen falls back to the packaged name if the setting has not been touched or cannot be read.

## Version 0.0.490.10 (July 28, 2026)
- **A production deployment would have installed a stranger's software instead of this product** — the deployment file intended for a real server, the one with the certificate and the public web address, did not build this product at all. It downloaded a finished copy of the open-source project this was built from, from that project's public account, and it re-downloaded it on every single restart. None of this product's work existed in what it installed: not the name, not the sign-in changes, none of the corrections of the past months. It would have started cleanly, reported itself healthy, run the other project's database changes against this product's database, and served an installation with nothing in it. It now builds from this source and can never again fetch anything from outside. **A server already installed this way holds no data of its own** — check what is actually running before upgrading it. The deployment file also still offered a published-in-public password as its fallback and named the database after the original project; both are gone.
- **The product no longer introduces itself to other people's systems under someone else's name** — a handful of places identified this software to the outside world by the name of the open-source project it was built from, and the most consequential of them were not on any screen we control. Every connection to a customer's SQL Server or Redshift announced itself under that name in *their* server's session list, where their own database administrator reads it. Every email carried it in a hidden header that stays in the recipient's copy permanently, and could be sent from an address at that project's domain. The connection offered to tools like Claude Desktop advertised it as the name of the server. All of these now carry this product's name.
- **Files we write into a connected repository now go into a folder named after this product** — anything published to a customer's own repository landed in a top-level folder named for the original project, visible in every one of their code reviews. New files now go to a folder named `dash`. **Nothing already published moves:** a file that is already in the old folder keeps being updated there, because its location is recorded against the instruction it came from, and moving it would both leave a duplicate behind and invite someone to delete the old folder — which would file away the instructions inside it. Only genuinely new files use the new folder, and a repository connected from now on never sees the old one.
- **Automated publishing that pointed at another project's accounts is switched off** — three build routines inherited with the code were still configured to publish finished images and installation packages into the original project's public accounts. They could not have succeeded, but they should never have been aimed there. They now point at this product's own names and will only ever run when someone starts them by hand.
- **Charts inside reports keep working while their labelling is renamed** — the markings that let you open the "where did this number come from" panel on a hand-built dashboard tile have been renamed. Reports built from now on use the new markings; every report already saved continues to work exactly as before, because both are still read.

## Version 0.0.490.9 (July 28, 2026)
- **The product's own name now runs through the installation** — the settings, containers and configuration file still carried the initials of the open-source project this was built from. They now carry this product's. The environment settings that were named `BOW_…` are now named `DASH_…`, the two containers are `dash-app` and `dash-postgres`, and the configuration file is `dash-config.yaml`. **Nothing needs to be renamed to upgrade:** the old names are still accepted, in both directions, and the application records in its log which old names it fell back to so a half-renamed machine is visible rather than merely lucky. The database user and database name are deliberately unchanged, because renaming those on an existing installation is the one step that cannot be undone.
- **An installation could quietly start with a brand-new encryption key** — if the key could not be found, one was generated and start-up carried on with no error. That is correct on a genuinely new installation and destroys every stored connector password, Microsoft sign-in, directory bind and single-sign-on secret on any other, and from inside that moment the two are indistinguishable. It now says so, unmissably, in both of the two places it could happen; and the start-up check that refuses to run without a key now recognises both the old and the new name, so a machine that renames one but not the other stops rather than starts up wrong.
- **Losing the configuration file no longer passes silently** — the file is supplied from outside the application, so a renamed or missing one is easy to end up with, and the result was a quiet fall back to built-in defaults: an installation would simply lose its configured directory, mail and single-sign-on settings without a word. Either filename is now accepted.

## Version 0.0.490.8 (July 28, 2026)
- **The sign-in limit now holds when attempts arrive at the same moment** — the limit shipped in the previous release stopped someone trying passwords one after another, but not someone sending them all at once, which is how it is actually done. The count was read, increased, and written back as three separate steps, so attempts arriving together all read the same figure before any of them had been recorded, and each concluded it was the first. Tested against a thousand accounts: forty simultaneous attempts on one address were all let through, and the counter finished at fifteen rather than forty. The count is now made by the database in a single step, so nothing can be lost, and the same fix closes the same flaw in two quieter places — the point where a fresh five-minute period begins, and two first attempts on the same address arriving together.
- **A failed count could quietly grant the attempt** — the limiter is deliberately built to let people in if it cannot count, because being unable to count is not evidence that anything is wrong and locking everyone out over a database hiccup would be worse. But that safety net also swallowed a fault in reading back the time, on one of the two database engines this runs against, and the limit then never applied there at all. The values are now read back in a defined form, so the limit behaves identically wherever it runs.

## Version 0.0.490.7 (July 28, 2026)
- **Signing in with your company account now puts you in the workspace** — single sign-on and the directory would create the account and then leave the person nowhere: signed in, but a member of nothing, with no agents, no data and no way to ask for any. Proving who you are, being allowed an account, and being placed in a workspace are three separate things, and only the first two had ever been built. A person admitted this way now arrives with a membership and a role in the same step that creates them. Where a company has more than one workspace, single sign-on refuses to guess which one rather than putting someone in the wrong one.
- **One page that answers "how do people get in"** — the setting was a list of allowed email addresses, which is only one of the three ways in and not the one most companies use. The page now shows all three — single sign-on, the company directory, and an administrator creating the account — and says for each whether it is set up and whether it creates accounts on its own or only accepts invitations. The role that new arrivals are given is set once, in one place, for all of them. The email-domain list is still there, under Advanced, and opens by itself if you are using it.
- **The product now records when people sign in** — every attempt to store a sign-in time had been failing since the day it shipped, silently, because the value was written in a form the database rejects and the failure was discarded. The administrator's own last sign-in read as never, after months of daily use. Sign-in and last-seen times are now recorded, and a failure to record one says so instead of vanishing.
- **Guessing a password is now rate limited** — the sign-in and registration forms accepted attempts as fast as the network allowed. Both are now limited, counted in the database so that every one of the application's workers shares the same count rather than each keeping its own. (Attempts sent all at the same moment could still slip past this; that is fixed in the following release.) The per-account limit is deliberately wide, so nobody can lock a colleague out of their own account by failing their sign-in on purpose, and a successful sign-in clears only that account's count.
- **A long job now reports its progress in one language** — four different parts of the product tracked long-running work and each had invented its own words for the same handful of states, to the point where a single response said one thing at the top and a different word for the same thing inside it. Nothing said which spelling to expect, so a check written against one screen was silently never true on the next — and a status test that never fires does not look broken, it looks like the job never finishes. Every report now uses the same words. Nothing already stored was rewritten, so older records keep working.
- **A second scan could start while the first was still learning** — the guard against running two scans of the same connection at once only recognised the earliest stage of a scan, so during the longest part of the run it stopped guarding, which is exactly when someone is most likely to press the button again.
- **Per-workspace progress was never actually saved** — the counters advanced while every workspace in the list stayed at "waiting" forever, so a scan appeared to be both progressing and not.
- **Technical detail is no longer exposed in production** — an error in a live deployment could return internal diagnostic information intended for development.

## Version 0.0.490.6 (July 28, 2026)
- **Connecting Power BI no longer ends with the window vanishing** — signing in ran the whole scan of every Microsoft tenant you can reach inside the sign-in request, so the button spun for as long as that took and then the window closed the moment it finished, with no summary and no sign that the agent was still learning your data behind it. Sign-in now returns as soon as your account is stored and the scan runs in the background, reporting each tenant as it lands. Microsoft Fabric already worked this way; both now behave the same.
- **Sync progress is visible to every browser tab, not one in four** — progress was held in the memory of a single application worker while the browser's request for it was answered by whichever worker was free, so most checks came back saying nothing was running even while a sync was in full flight. The window would then conclude it had finished and stop showing anything. Progress is now shared, so what you see is what is actually happening.
- **The sign-in window stays open and tells you what you got** — it ends on a summary naming how many tables came from how many workspaces, instead of closing itself. While the sync runs it lists each workspace as it is read, and the button to leave says plainly that closing does not stop it.
- **Some workspaces answering and some not is now its own outcome** — reaching three of your four workspaces is a working agent, not a failure, and it is reported that way: which one did not answer, what that means for your answers, and an explicit note that nothing you already had was removed. "Try again" re-runs the scan without asking you to sign in again.
- **Sync state now lives on the agent, not only in the sign-in window** — the agent list, the agent page and the data picker in chat all show whether an agent is syncing, ready, partly connected or expired, so closing the window no longer means losing sight of the work. An agent with nothing to report shows nothing at all.
- **Microsoft errors now say what to do about them** — a refused sign-in showed Microsoft's raw text, which carries a directory name, a trace identifier and a timestamp, and tells the reader nothing they can act on. Errors are now translated: what happened, who to ask, and the alternative where there is one — an account without a Power BI licence is told that Fabric lakehouses do not need it. The original is still recorded for support.
- **A sign-in warns before it expires instead of failing quietly** — a Microsoft sign-in lasts about ninety days, and the first sign that it had lapsed used to be a question that came back with nothing. The remaining time is now shown in the final week, and an expired sign-in says so plainly and offers to reconnect, rather than showing an agent as ready.
- **Connecting Microsoft data says what you need beforehand: nothing** — the form used to ask for a tenant identifier that these connectors no longer need. It now explains that there is nothing to prepare, no administrator approval to arrange, and what each of the two Microsoft options actually reaches.

## Version 0.0.490.5 (July 27, 2026)
- **An exported PDF now contains its charts** — a chart in a document was drawn by the browser and existed nowhere else, so the server-made PDF printed a line saying the chart had been left out and told the reader to open the app. That was the reason the PDF button in the document editor still went through the browser's own print dialog: printing was the only export that kept the charts. It also meant the work that stops a wide table breaking its own figures across lines never reached anyone who owned a document, because an owner's document opens in the editor. Charts are now drawn on the server and placed in the PDF as real pictures, tables and single-figure cards come through as proper tables, and the editor's PDF button uses that same path — so one export finally has both the charts and the readable tables. Printing from the browser still works for capturing the page exactly as the screen shows it.
- **Exporting from the editor saves your work first** — a PDF or Word file is built by the server from the saved document, so unsaved edits would have been quietly missing from the download. Both buttons now save before exporting, and stop with an explanation if that save fails rather than handing over the previous version. A document nobody has edited is not saved again, so exporting does not create a new version of it.

## Version 0.0.490.4 (July 27, 2026)
- **Store names on an exported chart are no longer cut off** — the picture placed in a Word file was drawn at a fixed size, and the chart engine lays the plot area out first and writes the axis labels outside it, so long angled names ran past the edge of the picture and were simply clipped off: "Ocean - Yangon 74" arrived as "an - Yangon 74". On screen this never showed, because the chart sits in a panel wider than a printed page. The labels are now measured as part of the picture, so the plot shrinks to make room for them instead of the names being trimmed.
- **A wide table in a Word export no longer breaks the numbers it is showing** — a table the author wrote is kept in full, every column, because it is the document's own content rather than a preview of a dataset. But at ordinary body size a table that wide had to wrap inside each cell, and a wrapped cell does not merely look cramped: it splits the value itself, so a revenue figure printed as "17,726,3" above "84", a city as "Mandala" above "y". Wide tables now step down in size as columns are added and ask Word to widen a column rather than break what is in it. Column headings may still wrap, deliberately — a long heading forcing its column wide is what squeezed the figures in the first place.

## Version 0.0.490.3 (July 27, 2026)
- **The person who wrote a document can now export it to Word** — the Word button was only ever shown on the read-only view, and a document opens in edit mode for whoever owns it, so the one person guaranteed never to see it was its author. The button now appears in both states. Markdown and PDF stay where they are: both are produced by the browser itself, from the text on screen, so they already work on unsaved edits — a Word file cannot be made that way and has to be built on the server from the saved document. So if there are unsaved changes the button saves them first and then downloads, and if that save fails it stops and says so rather than quietly handing over the previous version. Exporting a document nobody has touched does not create a new version of it.

## Version 0.0.490.2 (July 27, 2026)
- **Charts now appear in a Word export instead of vanishing** — a chart inside a document was drawn by the browser and existed nowhere else, so the exported .docx quietly dropped it and the reader got a document with a hole where the evidence should have been. Charts are now drawn on the server with the same charting engine the app uses and placed in the file as real pictures; a table or a single-figure card becomes a proper Word table. A chart that cannot be drawn for any reason leaves a short note in its place — the export never fails because of one picture.
- **A wide table in a Word export is shown as a readable preview, and says so** — a table with many columns was written out in full, and on a page that width every cell wrapped to roughly one character per line: a four-page report became eighteen pages of unreadable columns. The export now shows the first rows and only the columns that actually carry data for those rows, at a size that fits the page, with a line underneath stating exactly how much is shown — "showing 12 of 51 rows and 6 of 16 columns" — so a preview can never be mistaken for the whole dataset.
- **Values no longer disappear from an exported PDF** — in a wide table, a cell whose text wrapped onto two lines still looked correct on the page, but the value was split in the file itself, so searching or copying it returned nothing. Tables are now measured against the real page width and stepped down in size until each cell fits on one line; where even the smallest readable size will not fit, the table wraps as before rather than being cut off at the edge of the page.
- **A dashboard can be exported to PDF** — the export button was there but refused everything except documents. Dashboards now render through the same pipeline the app already uses for previews and come back as a proper PDF.
- **Slide checks stopped reporting problems that were not there** — the new overflow check measured text against the box it was given and flagged anything larger, but a text box is allowed to grow, and on a well-made deck growing is normal. Real decks came back with several warnings each and none of them were faults. A box is now reported only when the grown text actually lands on top of another element or runs off the slide.
- **Insights describe the period they were drawn from** — a summary could be written from the most recent complete quarter while reading as though it described today, and could attach a qualifier to a figure that the data did not support. Findings now name the period they cover and are held to what the figures actually show.
- **A dashboard built on a large result is completed rather than refused** — results are capped before they are stored, and a dashboard built on a capped copy would be quietly wrong, so it was refused outright. The underlying query is still on file, so it is simply run again and the complete result either used whole or summed into groups that fit — and what was done is stated on the artifact. Where neither is possible the refusal stands, so this can only ever turn a refusal into a correct dashboard.
- **Turning that recovery off now works** — the switch was documented and read, but the setting behind it was never declared, so the value was looked up, not found, and the default kept applying. Setting it off had no effect at all. It is now a real switch, still on by default. The re-read is also bounded: a result far past the size any dashboard can carry is abandoned instead of loaded, so one very large query cannot exhaust the server's memory. The limit can be raised where there is memory to spare.
- **A question left running in the background can no longer be dropped without a trace** — work handed off to run in the background was started and then not held onto, so it could be discarded partway through with nothing written down and no request left to fail. The answer simply stayed "in progress" forever. Those handoffs are now held for as long as they run.

## Version 0.0.490.1 (July 27, 2026)
- **Generated decks can now be checked for text that runs over the slide** — a deck is built by writing PowerPoint instructions directly, and those instructions have no way to measure text: a box is given a height by estimate, and if the writing turns out longer than the estimate it simply spills, over whatever sits below it and past the bottom of the slide. Nothing failed when that happened — the deck was written, reported as successful, and delivered with the middle of a paragraph hidden under a card. The existing retry only ever caught decks that failed to build at all, so this class of fault reached people unseen. The finished deck is now laid out and measured the way a viewer would see it, and any text box that renders far taller than the room it was given, or any shape sitting off the edge of the slide, is recorded against the exact slide and shape. For now this is recorded only: the deck is delivered as before, and correcting the slide automatically comes next. Off by default on this release.

## Version 0.0.490 (July 27, 2026)
- **Testing a SharePoint connection and signing in to OneDrive no longer wait on a full drive walk** — neither delay was the connection check itself. The pre-save "Test" counted the source by listing every file in it, and the OneDrive sign-in built the signing-in person's catalogue while the browser sat on the redirect, so signing in took as long as a complete walk of their drive — folder by folder, one request at a time, with a fresh connection opened for each. If the service throttled halfway through, the wait ended in an empty catalogue and nothing else. Now the connection test is bounded — it checks the token, the site, and one small page of the folder you scoped it to, and reports how long each of those steps took, so a slow test says *which* part is slow. The pre-save file count stops at 200 and shows "200+". The catalogue is built in the background after sign-in, which returns immediately, with live progress ("listing folders 34 of 120") on the connection card. The walk itself is much faster too: one reused connection instead of a new one per folder, and sibling folders read at the same time. SharePoint and OneDrive also gained the Indexing and Max Files settings the file connectors already had, so a very large library can skip cataloguing entirely and be read live instead.
- **An organization could reach a state where none of its instructions would load** — every instruction reads against one build marked as the live one, and although the rule was "only ever one", nothing in the database enforced it. Two things promoting a build at the same moment (someone saving an instruction while a training session or a sync promoted its own) could leave two marked live, and from that moment the instructions list, the counts, and the agent's own instruction context all failed outright. Any organization already in that state is repaired on upgrade — the newest build stays live and the rest are stepped down — and the database now refuses to hold a second one, so it cannot happen again.

## Version 0.0.489.11 (July 27, 2026)
- **Security: the PDF reader is updated** — the library used to read text out of uploaded PDFs was three years old and carries two vulnerabilities rated high, both of the kind where a deliberately malformed file can make the reader do something other than read. Anyone who can upload a PDF could reach it. The library is now on a release where both are fixed. The text it produces is unchanged: the same document, read before and after, gives byte-for-byte the same result.
- **Exporting a document to Word always failed** — the button was there, the permission check ran, and then the request returned an error, on every installation, since the feature shipped. The library that writes Word files was correctly listed as required but was missing from the file that decides what actually gets installed, and the installer trusts that file without checking it against the list. So the component was never present. The same gap quietly downgraded uploaded Word documents: the part of the system that reads a `.docx` to decide what it is could not open one, and fell back to treating it as an unknown file. Both work now, and the two lists are back in agreement.

## Version 0.0.489.10 (July 27, 2026)
- **Administrators can switch off the agents that came with the workspace** — Settings now has a Built-in agents panel listing Microsoft Fabric, Power BI and City Mart Retail, each with its own switch and a "Turn all off" for when something needs shutting down quickly. An agent switched off disappears from everyone's agent list and from the picker in chat, and stops being given to the AI at all; nothing is deleted, and switching it back on restores it exactly as it was. Agents your own people created are never listed here and cannot be affected by it. The panel does not appear on workspaces that were set up without the built-in agents. These are the same switches that already existed on each agent's own page, reading and writing the same setting, so the two screens can never disagree about whether an agent is on.

## Version 0.0.489.9 (July 27, 2026)
- **Accepting a suggested change to your own agent did nothing** — the person who created an agent, and who manages it, could review a proposed change to its instructions and press Accept, and nothing happened: the change stayed marked as pending review, and pressing Accept again simply added another pending entry. One organization accumulated ten. The cause was in how a change is published. Publishing carries every other instruction in the organization forward untouched, which is intended, but the permission check was reading that carried-forward content as though the person had written it — so as soon as a second agent existed anywhere, an agent's own manager was judged against agents they have nothing to do with, and was refused every time. The check now looks only at what the change actually alters. Writing an instruction that applies to the whole organization, rather than to one agent, still requires an administrator.

## Version 0.0.489.8 (July 27, 2026)
- **Members were offered a database connector they are not allowed to use** — opening up agent creation in the previous release opened both entries under New, not just the intended one. "Agent", which connects a database, warehouse or BI tool, is an administrator action and the server refuses it for members; only "Data Agent", which builds a private agent from files you upload, is theirs. The two are now separate: members see Data Agent and nothing else, administrators see both, and the connection buttons elsewhere on the page stay administrator-only.

## Version 0.0.489.7 (July 27, 2026)
- **Ordinary members could not add their own data** — a member saw instructions and reports but no way to create a data agent, and with no agent there was nothing to build a dashboard from, so the product read as view-only to everyone who was not an administrator. Two separate causes, either of which was enough on its own. The permission that lets a member build a private agent from files they upload was listed in the permission registry but never actually written to the member role: that role is created by an older database step which carries its own copy of the list, and the copy was not updated when the permission was added. Separately, every button that starts agent creation was shown only to holders of the administrator-level "connect a database" permission, so even a member who had the right permission was never offered the option. Members now get the permission on upgrade, and the upload path appears for them; connecting to a database or warehouse remains administrator-only, as before. A check now fails the build if the registry and the seeded role ever disagree again.

## Version 0.0.489.6 (July 27, 2026)
- **A brand-new installation reported two fatal errors on its very first start** — the application runs four server processes, and on an empty database all four tried to create the scheduler's bookkeeping table at the same moment. One succeeded; two were killed by the resulting conflict and printed a full error report ending in "Application startup failed. Exiting." They were restarted automatically a few seconds later and everything worked from then on, because the table existed by that point — but a first-time installer's first look at the log was two fatal-looking failures, and for those seconds the application served on half its processes. The table is now created by one process at a time, so a first start is silent. Only ever affected the first start against an empty database; an existing installation was never impacted.

## Version 0.0.489.5 (July 27, 2026)
- **A browser could stay on the old interface forever after an upgrade** — one machine kept showing the previous version while a different browser, on the same address, showed the new one immediately. The cause is a service worker: a small script a website can install to answer requests from its own local cache. This application has never installed one, but anything else previously served from the same address can, and it keeps running afterwards, intercepting every request. A hard-refresh does not help, because that bypasses the browser's cache and not the worker. The application now removes any such worker it finds on its own address when it starts, clears what it cached, and reloads once — so an affected browser repairs itself on the next visit instead of needing someone talked through developer tools.
- **Rolling back could never work on a first upgrade** — the rollback command looked for its target by name, deriving that name from the version currently running. But the saved image is named after the release it was saved *from*, so the name it searched for only exists after a second upgrade. Anyone rolling back their first upgrade was told no rollback image existed, while the correct one sat right there. It now identifies the target by reading the version out of each saved image rather than trusting the name, reports which version it is about to restore, and refuses only when every candidate is the version already running.
- **The upgrade guide covers reverse proxies** — the entry page must never be cached and the versioned assets must always be, and a proxy that gets this backwards produces exactly the frozen-interface symptom above with no error anywhere. The guide now gives the rule, the one-line check for what a proxy is really returning, and the settings for Caddy, nginx and Cloudflare.

## Version 0.0.489.4 (July 27, 2026)
- **Upgrading is now one command** — moving a running installation to a new release was an eight-step checklist, and four of those steps failed silently: skipping the database dump, skipping the rollback image tag, skipping the cache-buster that makes the new interface actually ship, and skipping the check that the build took at all. None of them produce an error; each one looks exactly like success until the day it matters. Two working images were lost this way. `./upgrade.sh` now performs the whole sequence and treats each of those four as a hard gate, stopping rather than continuing past a failed check. `./preflight.sh` reports the state of an installation without changing anything — version from all three places it can disagree, configuration, health, migration position, disk and backups — which is also the right thing to attach to a support request.
- **A fresh installation no longer starts by guessing** — there was no example configuration file, so a new install began by reading source code to find out which values were required. There is one now, and it opens with the setting that cannot be recovered if it is wrong: the encryption key that protects every stored credential is generated once and must never change afterwards. Left empty, the application does not fail — it quietly mints a new key at every restart, so each restart orphans what the previous run encrypted, with nothing to indicate anything is wrong.
- **The What's New window no longer links out to another project's releases** — the footer carried a link to the public releases page of the open-source project this product is built on. Anyone who followed it saw a different name and version numbers that never match ours. The changelog you are reading now is complete on its own, so the link is gone rather than replaced.

## Version 0.0.489.3 (July 27, 2026)
- **A dashboard can no longer be built on part of its data** — query results are capped so a table preview stays readable and the model's context stays affordable, but a dashboard was being built from that same shortened copy. Because the rows are cut in the query's own order, a month-ordered result quietly lost its most recent months: one dashboard reported net sales of 56.4B against a true 98.9B and covered ten of seventeen months with nothing to say so. Building from incomplete data is now refused outright, and the agent is told to summarise the data first rather than discovering the problem after it has already drawn the charts.
- **Charts get a wider allowance than tables** — a table on screen is unreadable past a few hundred rows while a chart is comfortable with tens of thousands, so these now have separate limits. A dashboard whose data fits the larger allowance is built from all of it and needs no summarising at all; in testing this halved the time to build one.
- **A dashboard that does not compile is never saved** — generated dashboards were stored without anyone checking they worked, so a broken one reached the screen as "Dashboard failed to render". Each dashboard is now compiled before it is stored, using the same compiler the browser uses; the error is handed back for correction, and if it still fails nothing is saved and the reason is reported honestly.
- **Every dashboard now explains itself** — a new panel above the tiles gives the headline and a few findings in plain language: which way the numbers are moving, what is concentrated where, and anything that stands out. Every figure in it is checked against the dashboard's own data before it is saved, and any figure that cannot be traced back is dropped rather than shown. This follows a summary during testing that reported an average order value of 11,499 when the true figure was 11,488.57 — right-looking, confidently stated, and traceable to nothing.

## Version 0.0.489.2 (July 26, 2026)
- **Members could see administration screens** — the Settings area showed a member the People and Members pages, which list every person in the organization with their email, role, linked sign-in providers and join date, and the sidebar offered them an "Admin" entry that opened it. Those pages are administration screens and now require the settings permission, as do the two endpoints behind them, so hiding the tab is backed by the server refusing the request rather than by the menu alone. Members no longer have a Settings area at all.
- **Local Runtime moved into Account Settings** — pairing your own computer is a personal action, not an organization setting, so it now sits in Account Settings beside your profile, usage and appearance rather than in the Settings area. The old address still works.

## Version 0.0.489.1 (July 26, 2026)
- **Power BI could answer with a confidently wrong number** — Power BI's query service silently returns only part of a large table: it stops at 100,000 rows, and sooner than that when rows are wide, without any warning that the result is incomplete. A question that pulled a table and then did the arithmetic here was therefore working from a fraction of the data. Asked for the top 5 promotion codes by benefit amount, the agent answered 518,000 for the wrong code while the true figure was 78,558,000 — the sum was calculated perfectly, over 16% of the table. Partial results are now detected and refused rather than used, and the agent asks Power BI to do the totalling and ranking, which is both correct and roughly a third faster. This does not affect connectors that read from a database directly, such as Microsoft Fabric, which return every row.
- **Analysis run on your own device could use a cut-off result** — when the local runtime executes analysis on your laptop, results above its size cap were trimmed on the way through. The trimming was already reported, but the helper on the laptop ignored the notice and used the shortened data anyway. It now stops with an explanation instead. Requires the updated helper.
- **A question answered twice showed both answers as current** — when the agent recomputed the same thing during a single turn, each attempt was kept as an equally valid result, so a chat could end up displaying several contradictory totals under one title with nothing to say which was the live one. Earlier attempts are now marked as replaced, and the agent is told plainly not to reconcile or average them. Nothing is deleted — the earlier attempts stay visible so the change can still be explained.
- **The answer sometimes left out the numbers** — a completed analysis could be summarised as "the analysis is done" with the figures only in the table beside it. The guidance against inventing data was being read as a reason to omit real results too. Those are now clearly separated: computed figures belong in the answer, invented ones never do.
- **A failing edit reported the wrong reason** — when editing a document or note failed, the real explanation (for example, that the text to replace was not found) was discarded and replaced by an internal message about output formats, which the agent could not act on. The true reason now survives.
- **A dashboard could be built on part of its data** — a saved analysis holds up to a set number of rows, and a dashboard built from it treated that portion as the whole dataset, reporting totals that were short and covering fewer months than they appeared to. Partial data is now declared, and anything built from it says so instead of presenting the shortfall as a total.
- **Exported PDFs printed placeholder text where charts belong** — a document exported to PDF showed raw markers instead of its charts. Charts still cannot be drawn in a server-side PDF, but the export now says so in place of each one rather than printing internal text.

## Version 0.0.489 (July 26, 2026)
- **New connector: Priority ERP** — connect a Priority Software installation (cloud or on-premise) and query its forms like any other data source. Three ways to sign in: a Personal Access Token (recommended, and the only one that works everywhere), a dedicated API user, or — on-premise only — "Sign in with Priority" so each member reaches exactly the forms their own Priority account allows. The connector catalogs *forms* rather than tables, keeps each field's Priority title so the agent sees the names your staff use rather than internal codes, and follows subforms as joins. Priority's cloud service allows 100 calls a minute per user, so a matching rate limit is set by default and can be raised for on-premise installations, which have no such ceiling.
- **New chat channel: Google Chat** — ask the agent questions from a Google Chat space and get the answer back in the thread. Events arrive over your own Google Cloud project, so no public URL is required.
- **Slack now connects without a public URL** — new Slack setups use Socket Mode by default, an outbound connection from the app to Slack, so there is nothing to expose to the internet and no signing secret to manage. Existing Slack connections keep working exactly as they are. Slack conversations also gained suggested prompts, a native "is thinking…" indicator, and proper threading.
- **Chat replies could be dropped, and the ✅ could appear before the answer** — on Slack, Teams, WhatsApp and Google Chat, the task that delivered a message could be collected by the system before it finished sending, and the "done" reaction was applied without waiting for the reply to land. Both are fixed across all four channels.
- **Quota exhaustion is now told apart from rate limiting** — both usually arrive as the same "429" from the provider, but they need opposite responses: a rate limit clears in seconds and is worth retrying, while an exhausted quota or empty credit balance will not clear during the run and needs a different model. The two are now distinguished, so a run that hits a spent balance switches models instead of retrying into the same wall. Your organization's own monthly spend limits are deliberately excluded — those mean *you* have hit your budget, and quietly moving the work to another model would only spend it somewhere else.
- **Word and PowerPoint files are no longer rejected for being short** — the check that spots an unreadable scanned PDF was also applied to Word and PowerPoint, whose text is read exactly. A genuinely brief document, such as a one-line memo, was treated as a failed read.
- **Reloading tables can no longer shrink the shared table list to one person's view** — when a reload runs with a member's own sign-in rather than the organization's credentials, it sees only the tables that member can reach. Such a reload now only ever *adds* to the shared list and never removes from it. This applies to the Microsoft Fabric and Power BI per-member sign-in connectors, where every reload runs as the member.
- **Faster sign-in sync on Fabric and Power BI** — revoking access to many tables at once no longer issues one database query per table.
- Documents can now be converted for preview inside the application, and several Microsoft Graph, Entra sign-in and MCP identity fixes ship alongside.

## Version 0.0.486.13 (July 26, 2026)
- **Microsoft Fabric and Power BI agents showed "No primary instruction" even though they had one** — those two connectors sign each member in with their own Microsoft account, so each member's Learn produces their own overview built from the tables they can see. The agent page was looking for a single shared overview, which those agents deliberately never have, so it reported nothing. The page now shows you your own overview, labelled so it is clear other members see theirs rather than yours.
- **Re-learning an agent could not repair a missing overview** — if an agent had no primary instruction, every subsequent Learn refreshed the text but never pointed the agent at it, so the page kept saying there was none. Learn now fills a missing primary. An overview you chose yourself is never overwritten.
- **A private overview could be published to the whole organization by accident** — when an agent had no primary, the system promoted the most likely instruction it could find, and a member's private overview was exactly the kind it preferred. Private instructions are now never promoted, and choosing one by hand is refused with an explanation.
- **One member's private overview could be shown to another member** during the agent setup step. It is now scoped to its owner.
- **Built-in connector skills are no longer promoted to primary** — they are generic advice for a connector type and say nothing about your data, so they made a poor description of the agent.

## Version 0.0.486.12 (July 26, 2026)
- **New Access page: decide what your members can reach** — shared folders, API keys and the MCP server can now each be set to one of three states from Settings ▸ Access. **On** means members use it normally. **Coming soon** leaves it visible but not usable, with a "Coming soon" label, so people can see it is planned instead of asking whether it exists. **Off** removes it from the interface entirely. All three start switched off, so a new installation exposes none of them until you decide otherwise.
- **Switching something off never destroys it** — API keys, MCP tokens and paired computers are all kept while a feature is off and work again the moment you switch it back on. Anyone who already has an API key can still revoke it while the feature is off, because taking a key away should never be blocked.
- **API keys could not be restricted before** — every member could create one and there was no setting anywhere to prevent it. They are now off unless you turn them on.
- **The switch is the real lock, not just a hidden menu** — turning a feature off also stops it working over the API, so a member cannot reach it by other means simply because a tab is no longer displayed.

## Version 0.0.486.11 (July 26, 2026)
- **A lakehouse that doesn't answer no longer disappears from your agent** — when your Microsoft account was re-checked, every lakehouse was contacted in turn and anything that failed to reply was quietly skipped. The tables in it were then treated as tables you had lost access to, and removed. A single slow or unavailable lakehouse could therefore empty a large part of your agent until the next fully successful check, with nothing to explain where the tables went. Tables are now only removed when the lakehouse holding them actually answered and no longer lists them, which is the only case that genuinely means your access changed.
- **Signing in to Microsoft Fabric is faster** — your lakehouses were contacted strictly one after another, so the wait was the sum of all of them. They are now contacted at the same time, and the wait is closer to the slowest single one. On an account with four lakehouses this part of sign-in went from about six and a half seconds to three.
- **Built-in Fabric guidance no longer refers to one customer's tables** — the three guides that ship with the app used real table and column names from the system they were written on. Since every person signs in with their own Microsoft account and sees a different set of tables, those examples meant nothing to anyone else. Every example now uses neutral placeholder names meant to be read as "your table here".

## Version 0.0.486.10 (July 26, 2026)
- **Questions that span two Fabric lakehouses now run as one query** — the connector refused any question that touched more than one lakehouse and fell back to pulling every table into memory and joining them there, which on the larger tables meant millions of rows moved for a result of a few dozen. Fabric can in fact join across lakehouses that live in the same workspace, so those questions are now sent to Fabric as a single query and come back in seconds. Lakehouses in genuinely different workspaces still can't be joined in one query — that is a Fabric limit, not ours — but the message now tells you which of your lakehouses *can* be joined together instead of only saying no.
- **The assistant now ships with built-in Fabric know-how** — three short guides, written from Microsoft's own documentation, covering the ways Fabric's SQL differs from the SQL most people expect: text comparisons are case-sensitive by default, the keys declared on a table are not enforced and so don't prove a join is valid, and a number of familiar commands and data types simply aren't available. The assistant reads a guide only when the question calls for it, so they cost nothing on unrelated work. They arrive automatically once a Fabric connection is set up, are marked "Built-in" in the knowledge area, and can be archived if you don't want one used.
- **A wrong join between two product codes has been removed, and the check that missed it added** — the automatically written summary of your Fabric data claimed two product-code columns could be joined. They share no values whatsoever: different code formats, in different workspaces, so the join was not merely wrong but impossible. Because that summary is included with every question, the claim was steering answers. Claims like it are now measured against the actual data before being stored, and one that turns out to have no overlap is labelled as such rather than passed on as fact.
- **A generated summary can no longer arrive as raw code** — the step that tidies up the assistant's written summary of a data source only trimmed formatting marks from the beginning of the text, so one left at the end caused the whole summary to be stored as unreadable machine output. Marks at both ends are now removed.
- **Reference material is available during deep analysis and training** — the assistant was shown the list of available guides in every mode but could only open one while in chat, so during a long analysis it could see what existed and not read it. It can now open them wherever they are listed.

## Version 0.0.486.9 (July 26, 2026)
- **Live updates in chat can no longer go missing** — the messages that push each new answer block into your browser were started as background work that the system only held on to loosely, so one could be discarded part-way through and simply never arrive. Nothing was logged when it happened, which is why it read as an occasional glitch rather than a fault. Those updates are now held until they finish. The same protection covers the notifications sent out to chat channels.
- **Answers sent to a chat channel no longer arrive empty** — when an answer was delivered to Slack or Teams, an internal filter meant to hide setup steps was written in a way that quietly discarded every ordinary step as well. On top of that, the delivery could start reading the answer before it had finished being written down. Both are fixed, so a delivered answer now contains what it should.
- **Signing in with a company account keeps your access token fresh** — the token stored when you connect a Microsoft or Google account was only ever written the first time you signed in, so anything that used it later (such as reading your job title and department from Entra ID) was working from a long-expired token. It is now refreshed on every sign-in.
- **Fixed an upgrade step that could not run on some installations** — a database change shipped in the previous release used a command that one supported database engine does not accept, so upgrading on that engine stopped at that point. The step now adapts to the engine in use.

## Version 0.0.486.8 (July 26, 2026)
- **Administrators can now switch Local Runtime off for the whole organisation** — until now the only way to remove the feature was an environment variable, which meant editing a file and redeploying; nobody inside the product could do it. It is now an ordinary setting that only an administrator can change. Turning it off stops all local execution immediately and hides the feature from members. Computers that were already paired are kept exactly as they are, so turning it back on restores them rather than making everyone pair again.
- **The MCP configuration now names this product** — the snippet you copy into your MCP client identified the server as "bagofwords", the name of the upstream open-source project, so it appeared in your client's server list under a product name you had never seen. It now reads "cityagent-insights".

## Version 0.0.486.7 (July 26, 2026)
- **"Run analysis on my computer" now starts switched off** — pairing your laptop and agreeing to run analyses on it are two different decisions, but the switch was on from the moment a device was paired, so a newly connected computer began executing work before anyone had seen the option. New devices now start off and you turn it on when you are ready. Computers already paired keep whatever setting they have — nothing is switched off behind your back.

## Version 0.0.486.6 (July 26, 2026)
- **Every settings tab is visible again** — the row of tabs across the top of Settings was a few pixels wider than the space it had, so the last tab was sliced off by the edge of the page. It was cut mid-word, and because it was the tab you were on, the one page you were looking at was the one whose name you could not read. Three of the longest tab names are now shorter — People, PII and Identity — and the spacing between them is slightly tighter, which leaves the whole row comfortably inside the page with room to spare for future tabs. Each page still shows its full name as the heading, so nothing is lost.
- **Running analyses on your own computer is now off unless it is switched on** — Local Runtime is disabled by default. Its settings tab, download links and pairing flow stay hidden until an administrator turns it on, so an organisation that doesn't use it is never shown a feature it didn't ask for.

## Version 0.0.486.5 (July 26, 2026)
- **Questions about files whose names contain spaces now work** — tables from a folder shared off your own computer take their names from the file names, so many of them contain spaces. When the analysis wrote one of those names into SQL without quotes, the database read the first word as the whole name and the question failed with "Table with name ... does not exist" — a file called "AWS Console Login events.csv" was looked up as "AWS". The rule for quoting these names is now stated up front, alongside the folder's table list, so the name is used whole.

## Version 0.0.486.4 (July 26, 2026)
- **Data previews no longer report a failure when they actually worked** — when the platform inspected your data, it kept a record of every attempt it made, including ones it had already corrected and moved past. The preview step then treated the presence of any such record as proof that it had failed. So a preview whose first attempt needed a correction was marked failed and given the discarded first error as its reason, even though the corrected attempt had run and its results were sitting right there in the output. Almost half of all previews were affected. The step now reports what actually happened, and the record of earlier attempts is kept for diagnosis only. Previews that genuinely fail are still reported as failures, unchanged. The same fault affected CSV exports and has been fixed there too.

## Version 0.0.486.3 (July 25, 2026)
- **Automatic model fallback now takes over when a model's endpoint cannot be reached** — with fallback switched on, an unreachable model was the one outage it never covered. The provider libraries report a dead endpoint, a wrong address and an expired certificate with the same three-word message, and the detail that distinguishes them was being dropped before the failure was assessed, so the platform filed it as "unrecognised" and surfaced a hard error to the user instead of moving the request to the next model in the list. Unreachable endpoints are now recognised, so the request is served by the next model and the substitution is disclosed in the chat as usual. Genuine key and permission problems still stop the run rather than quietly retrying elsewhere.
- **Analyses that read a folder shared from your device now show the queries they ran** — the query list came back empty for anything read directly from a shared folder, because those queries run on your machine and were never reported back. They now appear alongside server queries, so a folder analysis shows the same working detail as any other.
- **Better guidance when a folder analysis comes back empty** — an analysis that read your shared folder and returned nothing was told to check the table names of a completely unrelated connection, which sent the next attempt looking in the wrong place. It is now told what actually happened, and unrelated table names are no longer offered.

## Version 0.0.486.2 (July 25, 2026)
- **Analyses that used to fail two or three times before working now succeed on the first attempt** — after the model wrote the analysis code, a clean-up step trimmed anything that followed it by searching the text for the line that hands back the result. It stopped at the *first* one it found, which is usually inside a small helper near the top of the code, so the rest of the function — including the database queries — was thrown away before it ever ran. The run then failed with "returned None or an empty DataFrame". Whether a question worked came down to what the model happened to name one variable, which is why a retry sometimes succeeded for no visible reason. The clean-up now reads the generated code properly and keeps the whole function, so nothing inside it can be cut. Verified against the exact Power BI questions that were failing: both now succeed on the first attempt.
- **A run that is genuinely stuck now stops and says so, instead of retrying to no purpose** — if two attempts in a row produce identical code *and* an identical error, further attempts cannot add anything, so the run stops immediately and reports that it is stuck. An exact repeat like this usually means the cause is not in the generated code at all, so this also surfaces problems that would otherwise be silently burned through as retries. Attempts that produce genuinely different code are unaffected and still retry as before.
- **Error messages now describe what actually happened instead of guessing** — when an analysis returned no data, the message always told the model to check its table name, even when no query had been run at all, or when the queries had already succeeded and only the result was lost. It now reports what was observed: either that no query ran, or that the queries succeeded and the connection, credentials and SQL are all fine so the fault is elsewhere. The table-name hint appears only where it can actually apply.
- **Analyses that run on your own device now show which queries they used** — with the Local Runtime helper connected, the query list for a locally-executed analysis came back empty, because the queries ran on your machine and were never reported back. They are now included, so a local run shows the same detail as one that ran on the server.
- **The macOS helper app has been rebuilt** — the downloadable CityAgent Helper now includes the recent local-folder fixes: folders added or removed from the app are picked up without a restart, and un-sharing your last folder correctly clears it from the app instead of leaving it listed. Reading PDFs from a shared folder is now built into the app rather than depending on a Python package being present on your Mac. The helper also reports its version to the server, so a stale copy left running is now visible.

## Version 0.0.486.1 (July 25, 2026)
- **Power BI questions actually run now** — signing in to Power BI with your own Microsoft account loaded the dataset list correctly, but every question against it failed with an Azure AD error about a missing client secret. The query path was handed your stored sign-in record instead of a live access token, so it fell back to app-only authentication that this connector doesn't use. It now mints a fresh token from your sign-in, the same way Microsoft Fabric already did. Verified live: a real row count returned from a Power BI dataset, with Fabric unchanged.

## Version 0.0.486 (July 25, 2026)
Upstream release v0.0.486 ported onto the CityAgent Insights feature stack. All fork features (Local Runtime, local-folder attachments and chips, per-user Microsoft connectors, seeded agents, SSO/LDAP management, per-user instructions) verified unaffected.

- **A failed model no longer ends the run — it falls back to the next one** *(from v0.0.486)* — when the active model dies mid-run on a rate limit, a provider overload, or a network error, the run now retries on the next model in a configured **fallback order** and carries on for the rest of that run instead of stopping. The substitution is always disclosed in the chat: the step shows which model was dropped, which one took over, and why. A model that just failed is short-circuited for a cooldown so the same dead endpoint isn't tried again on every step. The order is set on the **LLM** settings page (up to 10 models), the feature is **off by default**, and it requires an enterprise license — routing and fallback are separate: routing picks the best model up front, fallback only reacts to a failure.
- **The agent can now read long dashboard code without burning the context window** *(from v0.0.486)* — `read_artifact` on a large artifact used to dump the whole file into the prompt. It now returns a line-numbered **outline** first, and the agent pulls exactly the part it needs with a line range (`offset`/`limit`) or a pattern search (`grep_pattern`, with surrounding context lines). Short artifacts are returned in full as before.
- **Tableau reloads reuse the indexed catalog instead of re-reading every datasource** *(from v0.0.486)* — the Tableau connector made two metadata calls for **every** published datasource on **every** refresh, so a site with hundreds of datasources paid a long crawl on each interactive "Reload tables". Tableau now uses the same incremental discovery already shipped for Power BI and Fabric: the cheap identity-scoped listing decides what you can see, known datasources are rebuilt from the stored catalog (with names, projects and paths refreshed so renames and moves still propagate), and only **new** datasources pay the per-datasource cost. Vanished datasources drop out, previously-unreadable ones are retried live, and scheduled/background reindexing keeps full introspection so field-level changes are still detected.
- **Closing a half-filled connection form now asks first** *(from v0.0.486)* — clicking outside the Add Connection dialog after typing credentials silently threw the form away. It now warns before discarding, so a long connector setup isn't lost to a stray click.

## Version 0.0.485.7 (July 25, 2026)
- **Local folders now carry documents, not just data files** — a connected folder lists its PDFs, Word docs, PowerPoints and text files alongside CSV/Excel tables. Attach the folder and ask about a document: the agent reads it through your paired helper, with the text extracted on your own device — the file is never uploaded. Folder rows show "N docs" instead of a misleading "no data files".
- **The helper download page now offers a build per operating system** — the Local Runtime settings page shows one card per platform (macOS and Windows) with its own download and setup hint, instead of a single macOS-only link.
- **A retried question no longer queries the wrong folders** — when a step failed and the agent retried, any mention of a folder inside the error text it echoed back was mistaken for a real folder selection, so the retry could be sent to your helper with a garbled folder list. Only genuine folder references count now.

## Version 0.0.485.6 (July 25, 2026)
- **Folder and file chips now actually show on chat messages** — the chat page renders messages with its own template, which never had the chips; they are now built directly into it. Every message shows a green chip per connected folder and a blue chip per uploaded file, live and after reload.

## Version 0.0.485.5 (July 25, 2026)
- **Attachment chips appear instantly on sent messages** — the folder and file chips now render on your message the moment you press send, not only after a page reload. The live message copy previously dropped the attachment info the server was already storing.

## Version 0.0.485.4 (July 25, 2026)
- **Uploaded files now show on chat messages** — every message displays a blue chip for each uploaded file it was asked against (documents were previously only tracked at the report level, so bubbles had nothing to show). Works from the landing page and inside reports.

## Version 0.0.485.3 (July 25, 2026)
- **Compact attach menu that can never clip** — the paperclip menu is now a ~290px Micro List: one-line folder rows (full path on hover), search on top, every folder in one scrolling list. It opens downward on the landing page and flips upward in chat automatically — Upload files is always visible on any screen size.

## Version 0.0.485.2 (July 25, 2026)
- **Attach menu always fits the screen** — the paperclip menu is now hard-capped to the window height: Upload files, search and the Connect button stay pinned and visible on any screen; only the folder list scrolls. Fixes the clipped menu where "Upload files" disappeared off the top.
- **Attachments visible in the chat** — every message shows what it was asked against: green chips for connected folders ("queried on your device"), blue chips for uploaded files, on old conversations too.
- **Folders truly stay with the conversation** — the attached folder follows from the landing page into the report, survives reloads, and is only detached when you remove its chip yourself.
- **The app announces new versions** — after a redeploy, open tabs show a "new version available" prompt with a one-click reload; no more stale pages.

## Version 0.0.485.1 (July 25, 2026)
- **Power BI (User Sign-in) and (Multi-Tenant) repeat sign-ins now take seconds, not minutes** — the multi-tenant scan reuses the saved catalog: each sign-in still asks Microsoft which datasets the person can see (permissions stay live, checked every time), but already-known datasets are rebuilt from the stored definitions and only newly-appeared ones are read in full. First-ever sign-in is unchanged, and any problem reading the saved catalog silently falls back to the previous full scan. Applies to both multi-tenant Power BI connectors; the original Power BI connector and Microsoft Fabric are untouched.

## Version 0.0.485 (July 25, 2026)
Upstream releases v0.0.483 → v0.0.485 ported onto the CityAgent Insights feature stack. All fork features (Local Runtime, per-user Microsoft connectors, seeded agents, SSO/LDAP management) verified unaffected.

- **Power BI indexing is dramatically faster** *(from v0.0.485)* — reloading a Power BI or Fabric connection now re-introspects only **new** datasets; already-indexed model tables are rebuilt from the stored catalog (upstream measured a 500-model tenant dropping from ~9 minutes to ~5 seconds). A manual Reload also no longer crawls the tenant twice: the catalog fetched with your own sign-in is reused for your personal table overlay in the same request — and it is only ever reused when it was fetched with **your** identity, never another user's. Scheduled background re-indexing still does a full crawl so column-level changes are always picked up. Internal `RowNumber-…` helper columns are now filtered out of every model table.
- **SharePoint, OneDrive and Google Drive can read documents** *(from v0.0.483)* — PDF, Word and PowerPoint files opened from drive connectors now return their extracted text to the agent (previously opaque bytes). Scanned/image-only PDFs fall back to raw bytes so vision can render them. Google-native Docs/Sheets/Slides export to PDF for original-file attachment. Extraction runs through the same hardened text layer as file uploads, including this fork's OOXML tag-scrub.
- **LLM judge cost guard** *(from v0.0.483)* — background quality scoring now runs only when the organization has a genuinely separate small model configured; it is never silently billed to the org's only (large) model.
- **Configurable agent limits** *(from v0.0.484)* — Settings gains **Max agent steps** (planner loop cap, 1–500) and an editable **Limit code retries** (codegen attempts, 1–10). The unused "Limit analysis steps" setting is removed, and settings sync now refreshes names/descriptions and prunes removed settings for existing organizations without touching stored values.
- **Honest catalog copy for non-SQL connectors** *(from v0.0.484)* — connection cards and indexing results now speak the connector's language: "Files" for drives, "messages" for mail, "model tables" for Power BI (applied to all three Power BI connector variants in this fork), "Collections" for document stores, "Tools" for MCP. Per-user catalogs (OneDrive, Google Drive, mail) explain that each user's items are indexed at sign-in instead of reporting "Discovered 0 tables".

## Version 0.0.482.2 (July 25, 2026)
- **Local Runtime (run agent analysis on your own computer)** — a paired **CityAgent Helper** on the user's laptop can now execute the agent's generated Python locally: the cloud still plans and writes the code, but the data crunching happens on the device. Pairing lives in **Settings → Local Runtime** (pair code, live online status, a "run on my computer" toggle, unpair). Jobs travel through a DB-backed queue (multi-worker safe, no websockets); credentials never leave the server — the helper proxies warehouse queries back through a server data-proxy endpoint with RBAC and a write-block. Any doubt (helper offline, unsupported job, timeout) falls back to the normal server sandbox so chat never breaks. Flag `HYBRID_LOCAL_RUNTIME`.
- **"Computed on your device" provenance badge** — every data step now shows where it actually ran: a green **Computed on your device** pill (with device name and elapsed time) when the local helper executed it, grey **Ran on server** otherwise. Provenance rides the existing step payload — no new tables, and users without a paired device see no change at all.
- **Attach a local folder in chat** — the paperclip menu gains **Attach local folder**: the helper scans shared folders (schema metadata only — table names, columns, row counts; file contents never uploaded) and the agent then queries those CSV/Parquet files **in place on the laptop** via DuckDB. Folder attachments are sticky across the conversation like data sources, work from the landing-page composer too (the folder rides into the new report's first message), and folder-reading steps are **forced local** — if the device is offline the agent says exactly which folder is unreachable and how to fix it, instead of silently answering from the wrong data. Flag `HYBRID_LOCAL_FOLDER_ATTACH`, migration `ca07lrfolders01`.
- **Helper apps for macOS and Windows** — the macOS menu-bar app (pair dialog, ●/⚡ status, Pause) is downloadable from Settings; the Windows tray app (`helper_app_win.py` + one-command PyInstaller build) is code-complete with full Windows compatibility fixes in the shared helper core (no `os.uname`, guarded chmod, UTF-8 console, POSIX paths for DuckDB). The settings page shows per-OS download cards and detects a missing build honestly (a real ranged-GET check, since the SPA catch-all would otherwise fake a 200).
- **Reliability fixes from live end-to-end testing** — CSV export of folder data no longer fails with "no device context" (the export tool now carries the same usage context as analysis steps, and a CSV produced on the laptop is materialized server-side from the returned dataframe); a retry that echoed a previous error message into the generated code can no longer garble the folder name sent to the helper (folder detection is anchored to real `ds_clients["local:…"]` references); and reports whose only data is an attached local folder no longer abort with "No active tables matched". Verified end-to-end on a real device: a 2,400-row local sales folder analyzed on-device in ~0.7s with results matching the raw file exactly.

## Version 0.0.482.1 (July 24, 2026)
- **Ready-to-use agents on every new installation** — the first admin signup now seeds three public agents automatically: **Microsoft Fabric** and **Power BI** (zero-config, each member connects their own Microsoft account via device code) and **City Mart Retail** (a full sample retail warehouse with 11 tables, teaching instructions and conversation starters). Once the admin saves the AI model key, any seeded agent still missing its overview learns itself in the background.
- **Onboarding now fits a pre-seeded workspace** — the welcome flow names the three ready agents and asks the admin for exactly one thing: the model key. Members never see setup screens they can't finish; they land straight on the workspace.
- **Learn controls on every agent** — the "Learn agent after saving" toggle and a new **Learn now** button appear on all connectors, not just the Microsoft ones. An explicit learn now publishes the overview immediately, makes it the agent's primary instruction, and de-duplicates conversation starters.
- **Faster uploads** — creating a Data Agent from files returns in under a second; the AI learning runs in the background and the wizard shows the draft as soon as it's ready.
- **Sign-in lifecycle for per-user connectors** — connection panels show signed-in / refreshed / expiry dates with a 90-day lifebar, a one-click **Reconnect** appears when the token expires, admins get a "Connected users" roster (no tokens ever shown), and Disconnect is scoped to the right credential store everywhere.
- **Single Sign-On made explicit** — Keycloak, Generic OIDC, Google and Entra ID are always listed with enable toggles; a provider can be switched on before it's configured, and its login button then explains "not available yet — ask your admin to finish setup" instead of failing mid-redirect.

## Version 0.0.482 (July 22, 2026)
- **Many-file agents no longer flood the context window** — the `<files>` section used to inline every attached file's full preview (sample rows, PDF text) into the planner prompt on every reasoning step, and the code-generation prompt carried the same previews twice; an agent whose file library holds dozens of files made every new chat pay ~7k tokens per wide spreadsheet with no cap and no trimming. Files now render at two tiers: files the user @-mentioned or attached with the current message keep their **full preview**, while files snapshotted from an agent's library render as a **one-line index** (type, row/column counts, sheet names, column headers) that the agent expands on demand with `read_file` / `inspect_data` — access is unchanged, only the eager prompt cost is gone. Remaining user uploads stay rich newest-first within a shared token budget (small chats with up to 3 files are untouched), the coder's `<excel_files>` block is now a stable index→file mapping instead of a second full copy, the Context Browser finally shows a `files` line item in the section-size breakdown, and the context trimmer can cut the files section as a backstop before it would ever touch conversation history or schemas. In a live test, a chat with six wide CSVs dropped from ~41k to ~1.6k tokens of file context with the model still answering file questions correctly from the index.

## Version 0.0.481 (July 22, 2026)
- **SAP BusinessObjects and SAP BW connectors (on-prem BI)** — two new data sources bring the on-prem SAP semantic layer into agents. **SAP BusinessObjects** connects over the `/biprws` RESTful Web Service SDK: it auto-discovers universes and their dimensions and measures, runs universe queries, and authenticates each end user by username/password (secEnterprise / LDAP / Active Directory / SAP) or by **trusted authentication** — a shared secret that logs a named user on **without their password**, the SSO-agnostic way to keep universe row/object security per user. **SAP BW / BW4HANA** connects over the XMLA web service (`/sap/bw/xml/soap/xmla`) and reuses the existing XMLA engine: it discovers InfoProviders and BEx queries as cubes (characteristics and key figures) and executes MDX — no proprietary RFC SDK and no OData "one structure" limit — with per-user Basic auth so BW analysis authorizations apply. Both index and query like any other source and reuse the platform's per-user credential storage; both show under **BI & analytics** with the SAP icon. Complements the existing SAP HANA (SQL) and SAP Datasphere (OData) connectors.

## Version 0.0.480 (July 21, 2026)
- **Forward per-user identity to MCP servers (#750)** — MCP connections can now map each signed-in user's identity and membership attributes into every tool call, both as outbound **HTTP headers** and as a **metadata object** merged into the tool arguments (`custom_metadata` by default), configured per-field in the connection's Advanced panel. Fields marked **locked** are injected server-side, always win over model-supplied values, and are stripped from the model-facing tool schema — so sensitive identity (e.g. `user_email`) can never be invented or overridden by the model; fields marked **ai** surface as defaults the model may fill. Also fixes a routing bug where an external MCP tool whose name collided with a BOW built-in (`create_report`, `get_context`, …) was silently intercepted and run in-process: a configured MCP/custom-API connection is now always called over its own wire, with genuine loopback self-calls detected and handled without deadlocking. MCP tool failures now render amber instead of red, and call durations round to whole seconds.

## Version 0.0.479 (July 21, 2026)
- **Word documents with tables no longer extract as raw XML** — the DOCX text scraper's regex matched `<w:tbl>`/`<w:tr>`/`<w:tc>` as text openers, so any docx containing a table (statements, invoices, most business docs) came back with WordprocessingML markup interleaved into its text everywhere extraction is used: `read_file`, keyword indexing, and content search. The tag match is now anchored, works with any XML namespace prefix (non-Microsoft generators emit e.g. `<ns0:t>`), and single-file **flat OPC / "Word 2003 XML"** documents saved with a `.docx` name — previously an unreadable dead end because they aren't zip archives — now extract their text too.

## Version 0.0.478 (July 21, 2026)
- **Garbled PDFs are now read as images instead of glyph soup** — PDFs with a broken/missing font-to-Unicode map (common in bank statements and other legacy-system exports) render perfectly on screen but extract as unreadable symbol salad, and `read_file` was returning that garbage as a successful text read. A new shape check (`doc_text_looks_garbled`) catches these extractions — letter-sparse, word-free text that no real document produces — and automatically re-reads the pages as images for a vision model, the same fallback scanned PDFs already use; prose in any script and numeric tables pass through untouched. The agent also gets an explicit `as_images` option on `read_file` so it can force an image read whenever text comes back mojibake'd (with the tool description now telling it to do exactly that instead of trusting the soup), and on non-vision models the garbled text is kept but flagged with a warning rather than presented as faithful. Garbled extractions no longer poison the keyword index or content search on Files & Directories and S3 sources — such files are indexed by name only — and the read cache is versioned so stale pre-fix text entries can't be served back.

## Version 0.0.477 (July 21, 2026)
- **Salesforce JWT Bearer OAuth + full object discovery (#751)** — the Salesforce connector authenticates a Connected App via the OAuth 2.0 JWT Bearer flow (consumer key + certificate private key + username, no stored password or interactive login), and indexes every queryable standard and custom (`__c`) object instead of a fixed five. Sandbox and My-Domain logins now route correctly (`sandbox` → `test.salesforce.com`), reference fields become foreign keys in the schema, and SOQL results are capped at 10,000 rows.

## Version 0.0.476 (July 20, 2026)
- **Sync job info from Microsoft Entra ID into user context** — a new **Entra ID Profile Sync** section on Settings → Identity Providers lets an admin pull each signed-in user's Microsoft Graph `/me` profile (job title, department, company, office, and more) into their per-org context on login. The admin picks exactly which attributes are included via checkboxes that show **live sample values from their own profile**, and can add or remove attributes freely. Synced attributes are rendered to the agent inside the existing `<user_profile>` context block (treated as context, not instructions) and shown read-only under **Directory profile** in Account Settings. It's per-org and opt-in (stored in org settings, no bow-config change), and uses only the default-granted delegated **`User.Read`** scope — no admin consent required; the one lifecycle field that needs elevated access (`employeeLeaveDateTime`) is excluded from the allowlist. The SCIM and LDAP sections on the same page are now collapsible (collapsed by default).

## Version 0.0.475 (July 20, 2026)
- **SAP Datasphere connector (semantic layer)** — a new **SAP Datasphere** data source queries the Datasphere semantic layer over the OData Consumption API. It auto-discovers every consumption-exposed analytic model the caller can access via the catalog API, reads each model's `$metadata` to split columns into measures and dimensions, and runs server-side-aggregated analytical queries (measures aggregate over the dimensions the agent selects — no SQL, DAX, or MDX). Auth is dual-mode: a **Technical User** OAuth client (client credentials) drives discovery, indexing, and shared queries, while an optional per-user **Interactive** sign-in (authorization code) lets each user query as themselves so Datasphere's Data Access Controls (row-level security) apply — a DAC-protected model returns no rows to the technical user but full rows to an authorized user's own token. This is distinct from, and complementary to, the existing **SAP HANA** connector, which reaches Datasphere's raw SQL/Open-SQL views (flat tables) over the HANA SQL port rather than the governed analytic models.

## Version 0.0.474 (July 20, 2026)
- **PII is now masked in the live chat, not only after a refresh** — when PII protection is on, the message you just typed was rendered optimistically with its raw text and only flipped to `[REDACTED_…]` once the page was reloaded (the streaming path bypasses the display serializers that mask persisted rows). The completion stream's first event (`completion.started`) now carries the display-redacted prompt, and the report view patches the optimistic bubble in place the instant it arrives — so the masked value shows live, mid-stream, with no reload and no change to the send/stream flow. Enterprise-gated like the rest of PII protection; a no-op when the feature is off.

## Version 0.0.473 (July 20, 2026)
- **Image generation (OpenAI `gpt-image-1`)** — the agent can now generate images from a prompt via a new `generate_image` tool, backed by the OpenAI Images API. The result is stored as a file and can be embedded in dashboards. Image-generation is a new model capability (`supports_image_generation`) with `gpt-image-1` added to the preset catalog; admins can also mark any model as an image model via a new **Image gen** toggle in Settings → LLM (persisted across catalog re-syncs). Image models are excluded from the chat/agent model pickers and can never be set as the org's default or small-default model.
- **Embed images and PDFs in artifacts** — `create_artifact` / `edit_artifact` accept file ids (generated images, or uploaded images/PDFs) and render them on the dashboard canvas via a new `<BowFile>` component: images show inline, PDFs render inline in an in-sandbox pdf.js viewer (falling back to an "Open PDF" card where the viewer can't load), and annotations can be overlaid on either.
- **Generated images appear inline in the conversation** — the `generate_image` tool renders a spinner while running and the finished image inline in the chat. The image is associated with the report (so the agent can reference it on later turns via `<files>`, read it with `read_file`, and embed it with `create_artifact`/`edit_artifact`), but tagged to the completion so it stays out of the user's composer attachment tray. A context digest keeps its `file_id` visible in conversation history so "now put that image in a dashboard" works across turns.
- **Signed, revocable file serving for embeds** — embedded files are served via short-lived, file-scoped capability tokens (`/files/{id}/embed?token=…`) minted fresh at render time, instead of inlining bytes or exposing the session endpoint. This also makes embedded images/PDFs render on **published/shared reports** (`/r/{id}`) for non-authenticated viewers, scoped to files actually embedded in that report's artifacts.

## Version 0.0.472 (July 20, 2026)
- **Auto model router is now Enterprise-only** — the org router (Settings → LLM) is gated by a new `model_routing` license feature. On community/unlicensed builds the toggle stays visible but shows a locked **ENTERPRISE** badge and is disabled, enabling it via the API returns 402, and setting per-model routing guidance is rejected. Enforcement is layered: the completion resolver only routes when the license is active (fails closed, so a config left over from an active license can't keep routing), while turning the toggle **off** is always allowed so a lapsed license can't strand an org with routing stuck on. Community behavior is unchanged — the resolved default model always runs.
- **The answer's model badge reflects the model that actually ran** — a routed request starts on the small model, so its completion was stamped (and badged in the report view) with the small model even after the planner escalated and the stronger model did the work. The escalated model is now persisted onto the completion, so the icon, tooltip and provider glyph show the model that produced the answer.

## Version 0.0.471 (July 20, 2026)
- **PII protection for prompts sent to the LLM (Enterprise)** — a new Settings → PII Protection page lets admins redact personal data before it ever reaches a model. Prebuilt detectors (email, credit card, US SSN, phone, IPv4, IBAN, AWS key) ship out of the box, each holding multiple regex patterns under one switch; admins can add custom rules, edit replacement tokens, and set each rule to **Replace** (swap with a token) or **Block** (refuse the request). Redaction runs at the single LLM chokepoint, so it covers the whole assembled prompt — the user's message, instructions, schema samples, data previews and uploaded file text — across every agent. A live "test your rules" box previews redaction on sample text. Enterprise-gated: the feature is hidden and inert on community builds.
- **PII masked in the rendered UI, not just the model** — when protection is on, matched values are also redacted in what the app shows: the chat message/prompt and the table/widget cells render `[REDACTED_…]`, while the **stored** data stays real so analysis, step reuse and exports keep working on the true values. Masking happens only at the serialization boundary, so every surface (chat, inline previews, full tables, queries, report summary) is covered.
- **Fix Postgres CI: per-user connection credentials no longer reject timezone-aware timestamps** — `user_connection_credentials.last_used_at` / `expires_at` are naive columns; a timezone-aware datetime was accepted by SQLite but rejected by asyncpg on Postgres ("can't subtract offset-naive and offset-aware datetimes"), failing the Power BI overlay repro tests. The model now normalizes any aware datetime to naive UTC, so every caller is safe regardless of Postgres or SQLite.

## Version 0.0.470 (July 19, 2026)
- **Report diagrams no longer fail to render over unquoted labels** — a `mermaid` flowchart whose node label contained punctuation (e.g. `E[revenue SUM(Invoice.Total)]`) aborted the whole parse and showed the "DIAGRAM FAILED TO RENDER" source box. Doc diagrams now auto-repair on a render failure: unquoted flowchart node labels are quoted and the diagram is retried once before falling back to source, so existing reports render without re-generation. The planner is also instructed to quote such labels going forward. Edge labels and non-flowchart diagrams are left untouched, and the repair is display-only (the stored source is unchanged).
- **Instructions render Mermaid diagrams** — a ```mermaid block written in an instruction previously showed as a raw code block. It now renders as a diagram in the instruction view (everywhere `InstructionText` displays markdown — knowledge explorer, report side panels, agent flyouts), reusing the same renderer as reports so the unquoted-label auto-repair applies there too. Other fenced code (```sql, ```python, …) still shows as code.

## Version 0.0.469 (July 18, 2026)
- **Saving an LLM provider no longer 500s** — adding or updating a provider in Settings → LLM failed with a server error whenever the organization already had more than one model flagged as the default (or small default): the existence check used a query that raised on multiple rows. It now tolerates duplicates, and the same guard was applied to the default-model lookup on the completion path.
- **Context-window inputs accept real token counts** — the per-model context-window field snapped to 1,000-token steps, so the catalog's own defaults (1,000,000, 200,000, 1,047,576, …) were flagged "not a valid value." The field now accepts any whole number.

## Version 0.0.468 (July 18, 2026)
- **OneDrive, SharePoint & Outlook Mail connectors fixed end to end (#710)** — "Test credentials" for the Microsoft Graph file connectors no longer fails with a spurious "No access_token…" (the pre-save test was dropping the credentials for these clients); Outlook Mail's "Sign in with Microsoft" works (its per-user OAuth was unsupported and missing the `Mail.Read` scope); and a file tool addressed by a source's **name** instead of its internal id now resolves to that connection instead of the agent reporting the source as "disconnected" — a wrong identifier reads as an invalid selection, and reconnecting is only suggested after an actual token check.
- **Mail-native agent tools for Outlook (#710)** — a mailbox now exposes `list_emails` / `read_email` / `search_email` instead of the file tools, so the planner stops treating a mailbox as "files" and reliably opens a message after searching. Drive/SharePoint agents keep the file tools; a mixed agent gets both, each scoped to its own connection.
- **Provider icons and tidier tool rows (#710)** — file, email and MCP tool-call rows in the conversation now show the source's brand icon (OneDrive, SharePoint, Gmail, Notion, …) via a shared resolver, and the "Parameters" block moved inside each file tool row's collapsible section, so a collapsed row is just its header line and expanding reveals the results and parameters together.

## Version 0.0.467 (July 18, 2026)
- **Session events in the conversation** — out-of-band actions taken on a report now leave a trace the agent can see and (for some) a subtle gray strip in the timeline. Thumbs up/down, uploading or removing a file, switching the model, changing the agent's data-source scope, and sharing the conversation or an artifact are recorded as lightweight events, interleaved chronologically. The agent reads them on its next turn — so it stops rebuilding a stale picture of the world (e.g. re-suggesting a rejected instruction, or not knowing why the last answer was thumbed down). Events never start an agent run; they're a passive ledger. Feedback and instruction rejections survive context compaction (aggregated into the rolling summary) while transient events fall away, and a burst of events can't push real conversation turns out of the agent's window.

## Version 0.0.466 (July 18, 2026)
- **Auto model router** — a new org setting (Settings → LLM, off by default) that routes each request to the cheapest capable model. When a user picks no model, the run starts on the small model and the agent escalates to a stronger one only when the task needs it, via a `route_model` tool whose choices are the models you give routing guidance to; the escalation propagates to code generation too. Explicit per-message picks and report-pinned models always bypass routing. In a 10-question analytics benchmark on the demo dataset (65 model calls, small=GPT-4o mini vs default=GPT-4o), the router handled every question on the small model at held quality (LLM-judge 4/5 across the board) for a measured **~95% lower LLM cost** ($0.09 vs $2.04); real-world savings scale with your traffic mix and how often escalation is actually needed.
- **Realized routing savings on the cost console** — completions made under the router are credited against the model that would otherwise have run, so the LLM Usage Cost card and `/monitoring/cost` show a "Saved by auto-routing" KPI (dollars saved, share of calls routed) over any date range.
- **Edit per-model pricing** — admins can set a model's input/output price per million tokens inline in Settings → LLM (Cost column). Corrects preset rates or prices a self-hosted model, and feeds both the cost console and the router savings math.

## Version 0.0.465 (July 18, 2026)
- **Per-user agent memory (#703)** — the agent can now remember durable facts about you across sessions (preferences, writing style, analyses you liked) via a new `update_user_memory` tool, injected into each conversation as `<user_memory>` and subordinate to org instructions. It's scoped per user and organization, bounded and always-injected, available in chat/deep only, and viewable/editable in the profile's "Instructions & Memory" tab.

## Version 0.0.464 (July 17, 2026)
- **Rolling context compaction for Agent v2 (#689)** — long conversations no longer hit the context ceiling: the agent compacts older turns into a rolling summary automatically when the window fills (and on demand), keeps answering past the watermark, and the report chat shows a subtle "compacted" divider where the fold happened. Compaction is visible live via a `context.compacted` SSE and the context-usage estimate refreshes immediately.
- **Queue and steer prompts during a run (#690)** — typing while the agent is working no longer means waiting: **Queue** holds the prompt (shown as removable chips under the thinking indicator) and runs it when the current completion finishes, while **Steer** injects it into the running completion at the next observation point — hard-interrupting in-flight planning — with a visible acknowledgment on the message once the agent picks it up. Queued prompts stay out of the model's conversation window until they actually run.
- **Multi-pick clarifying questions (#693)** — the clarify tool supports select-all-that-apply: the agent can ask one question with multiple selectable options, the answer card renders checkboxes with a single confirm, and the selections rehydrate correctly on page refresh.
- **Conversation cost and tokens in the trace view (#694)** — TraceModal's header now shows the conversation's total LLM tokens and cost, and each turn's summary strip shows per-turn planner tokens — with full token and cost breakdowns sourced from usage events when licensed.
- **Fix empty review for NEW instructions pending approval (#695)** — reviewing a newly suggested instruction no longer opens an empty diff: the review payload for instructions that never had a published version now carries the proposed content instead of nothing.
- **Anti-overfit guard for learned instructions (#696)** — `create_instruction`/`edit_instruction` pass through a generality gate that rejects one-off, prompt-parroting rules before they pollute the instruction base, backed by an objective bait/control overfitting benchmark with a deterministic DB-based scorer (provider errors count as failed trials, not behavioral outcomes).
- **Smarter instruction loading with an on-demand catalog (#697)** — intelligent instructions are matched by coverage-based scoring (light stemming, title/label/table-name boosts) instead of brittle exact-word overlap, zero-score instructions fill remaining capacity instead of silently vanishing, and over-capacity ones become a compact catalog the planner can pull from via a new `read_instruction` tool (scoped to the report's data sources and the user's table access); `search_instructions` gains a compact chat mode.
- **Collapsible table-search results in chat** — the "Searched …" table results in the conversation now collapse to a single line by default, with each found table shown in the header with its data source icon (deduped, "+N more" past six); clicking the line expands the detailed per-table list as before.

## Version 0.0.463 (July 17, 2026)
- **Guided agent creation in training mode** — asking for an agent without saying what it should cover now starts a short, friendly interview: the assistant inspects the connection, then asks with clickable choices (schema or name-prefix groups with table counts, tool verb groups for MCP connections, plus "Everything") before creating anything. `create_agent` enforces it: on a large catalog with no selection it returns a `needs_selection` menu instead of silently creating a near-empty agent; an explicit `use_defaults` input covers the "everything" choice.

## Version 0.0.462 (July 17, 2026)
- **Per-model context window override (#680)** — admins can set a model's real context-window size in Settings → LLM and it now survives catalog re-syncs. Deployments that serve a model with a smaller window (e.g. AWS Bedrock capped at 100k) no longer fail mid-analysis with "context length exceeded", and the context-usage meter reflects the true limit. Clearing the override restores the catalog default.
- **Eval runs finalize server-side (#681, #682)** — a background eval run now evaluates its assertions and leaves `in_progress` on its own, per case, instead of waiting for someone to open the run page to drive the evaluation; the aggregate is idempotent and records the authoritative pass/fail/error status even under concurrent finalizers.
- **Eval agent loop (#682)** — the chat-driven cycle create → run (background) → wake-up → read → edit → rerun → compare now closes end to end without blocking the agent: `run_eval` is background-by-default, a run-finished wake-up posts results back into the conversation, and new `get_eval_run` / `get_eval_runs` / `stop_eval_run` / `edit_eval` / `cancel_wait` / `search_evals` tools plus a run-comparison view (fixed/regressed flips) support the loop. Includes an org concurrency cap and identical-run dedupe.
- **Thinking indicator above the prompt box (#684)** — while a completion is submitting or in progress, the prompt box shows a spinner, a shimmering "Thinking" label, and a live elapsed-time counter that also resumes after a mid-run page refresh and fades out when the run finishes or is stopped.
- **Cleaner shared conversations on mobile (#685)** — user and assistant avatars are hidden below the md breakpoint on the shared `/c/[token]` page, dropping the content indent to reclaim horizontal space and matching the report chat page.
- **Training mode builds agents** — three training-only tools let one prompt go from connection to ready agent: `list_connections` shows the connections you hold create-agent access on, `get_connection` browses a connection's tables-by-schema, MCP tools, or file scope (glob filter + pagination) before any agent exists, and `create_agent` creates the agent on existing connections with inline `schemas`/`tables`/`tools` glob selection, attaches it to the training session, and reports any unmatched selector. No credentials pass through the model — connections stay admin-created.
- **Agent card in the training chat** — a created agent renders as a card with status, description, and Tables/Tools/Files tabs (active/enabled counts live from the agent), plus an Open-agent link for refining the selection on the agent page.

## Version 0.0.461 (July 16, 2026)
- **DOS-Hebrew and any-encoding filenames now work end to end** — the legacy filename recovery gains cp862 (DOS-Hebrew) and ISO-8859-8, picks the best-quality decode instead of the first that succeeds (so Western `café.pdf` shares can't be misread as Hebrew), and adds an encoding-agnostic fallback that resolves a listed file by display-name match even when the encoding is unknown — a listed file can always be read/grepped, worst case with imperfect glyphs. Unrecoverable names log their raw bytes so the right charset can be identified from server logs without host access.
- **Repeating an identical tool call no longer ends the run** — the first repeat now injects a corrective note and lets the agent continue with the result it already has; only a further identical repeat stops the turn (previously the second call ended the run immediately, cutting off recoverable plans mid-flight).

## Version 0.0.460 (July 15, 2026)
- **File listings reach the agent, not just the UI** — list_files and search_files results (names, paths, sizes, ids) are now visible to the model itself, ending the blind re-list loop where the agent called the same listing repeatedly and the run ended with a false "Task completed successfully" message; that circuit-breaker message now tells the model to use the result it already has instead of claiming success.
- **Hebrew/legacy filenames recover instead of becoming `?????`** — directories with names stored in a legacy codepage (cp1255/cp1252 — Windows shares, zips extracted without a codepage) now show their real names in listings and answers, and reading/grepping those files round-trips to the on-disk bytes. Previously every non-ASCII character degraded to `?` (and before 0.0.458, permanently crashed the report).

## Version 0.0.459 (July 15, 2026)
- **SAP HANA / SAP Datasphere connections** — connect to SAP HANA, HANA Cloud, or an SAP Datasphere space (Open SQL schema) with a database user and query it in plain SQL via SAP's official `hdbcli` driver. Schema discovery covers tables **and views** (Datasphere exposes data as "Expose for Consumption" views) with comments and primary keys, system schemas are filtered out by default, and optional schema scoping accepts a comma-separated list — for Datasphere, the space schema. TLS on port 443 is the default (HANA Cloud/Datasphere); on-premise systems can set a custom port and disable encryption. Includes a reproducible HANA Express environment under `tools/hana/` for local verification.
- **read_file and grep_files work on conversation attachments** — uploaded files (JSON, text, logs, PDFs, images) are now readable by the same read_file/grep_files tools that serve file connections: leave connection_id empty and pass the file's id from the conversation. Windowed reads, PDF `page_range`, and line-level grep all work over attachments, and the tools appear in the agent's catalog whenever the conversation has files — no file connector required.
- **The agent can look at images — including ones from earlier turns** — read_file on an attached image shows it to a vision-capable model, so "what's in the screenshot I sent earlier?" now works (previously only images attached to the current message were visible, once). Scanned/image-only PDF pages render to vision per requested `page_range` instead of always the first 8 pages.
- **Attachments are decidable before reading** — the agent's file index now includes a content taste for every type: a 500-char head for JSON/text/logs, dimensions for images, and "N of M pages previewed — use page_range" for long PDFs (previously these rendered as "unsupported"). Conversation history records which files arrived with which message and what was read or viewed (`pages 2-2 of 38`, "viewed by vision"), so later turns can resolve "that file from earlier" by id.

## Version 0.0.458 (July 14, 2026)
- **Fix agents re-reading files in a loop** — read_file results (whole-file text/JSON/CSV head, windowed reads, and PDF page reads) now deliver a bounded content excerpt to the model instead of a bare summary line, with an honest trailer naming the session file and how to page the rest; superseded reads compact to a length marker so long file sessions don't bloat context. Verified live: the agent pages forward with offset/length instead of re-issuing identical reads.
- **PDF page-range reads** — read_file gains `page_range` (e.g. '2' or '10-15') for PDFs on Files & Directories and S3 connections: extracts only the requested pages and reports `pages_total`, so large documents are pageable like large text files instead of all-or-nothing.
- **Fix permanent 500 on reports after reading certain PDFs** — PDFs with broken ToUnicode CMaps make pypdf emit lone UTF-16 surrogates, which persisted with the completion and crashed every later load of the report (`UnicodeEncodeError: surrogates not allowed`). Extracted document text is now sanitized at the source, tool payloads are sanitized before persistence, and previously poisoned rows are scrubbed at read time so affected reports load again.
- **SharePoint/OneDrive search results show clean paths** — file paths in search_files results are now root-relative (`Contracts/acme.pdf`) instead of the raw Graph parentReference (`/drives/b!…/root:/…`), matching listings; file rows in the report view also show the path inline, and read_file headers show the file name instead of a truncated opaque id.

## Version 0.0.457 (July 14, 2026)
- **Bedrock API key authentication (#657)** — the AWS Bedrock provider gains an API Key auth mode alongside IAM and Access Keys: paste a Bedrock API key (the credential the AWS console now generates by default) and connect — no IAM roles or access-key pairs required. The key is injected as a per-provider Bearer token (never a process-global env var, so multiple orgs' keys stay isolated), and the UI notes that short-term keys expire within 12 hours.

## Version 0.0.456 (July 13, 2026)
- **Line-level grep over file sources (#649)** — a new `grep_files` agent tool runs deterministic regex over raw file bytes on Files & Directories and Amazon S3 connections, returning matching lines with line numbers and before/after context plus a total match count, per-file/total match caps, skipped-file reasons (binary, too large, off-scope), and a resumable cursor — so the agent extracts error lines from large log corpora at the source instead of paging whole files through context. Any text file greps regardless of extension (`.log`, `.ndjson`, extensionless); `include_globs` scoping is enforced and off-scope reads are audited.
- **Tool parameters visible on expand (#649)** — `list_files`, `search_files`, `read_file`, and `grep_files` calls in the report view show the exact arguments the agent passed (pattern, scope, paging cursor) behind a collapsed "Parameters" toggle, and windowed `read_file` calls show a byte-range progress badge.
- **Fix truncated `read_file` results being served from cache (#649)** — a large file's first read cached its clipped render and every later read (even with a higher `max_chars`) got the same fraction of the file back, including the session file handed to downstream analysis; truncated renders are no longer cached and stale clipped entries are read live.

## Version 0.0.455 (July 13, 2026)
- **Fix Slack/Teams channel settings crashing when connected** — a bare `@` in the "Usage notes" locale strings broke vue-i18n message parsing, blanking the panel in every language; the `@` is now escaped so connected Slack/Teams panels render again.

## Version 0.0.454 (July 13, 2026)
- **Configurable Teams/WhatsApp session staleness (#646)** — how long Teams 1:1 and WhatsApp chats keep continuing in the same conversation report is set per org in hours from Settings → Channels (`teams_session_max_age_hours`, default 120; `whatsapp_session_max_age_hours`, default 24; range 1–720).

## Version 0.0.453 (July 13, 2026)
- **Infor OLAP XMLA connections** — connect to Infor d/EPM through the OLAP Service Manager or ION API Gateway, with database-worker routing, application credentials, and actionable XMLA faults.

## Version 0.0.452 (July 12, 2026)
- **Files knowledge category with enforced glob scope (#630)** — file connectors (`network_dir`, Amazon S3) become their own Files category instead of masquerading as tables: the agent reads only files matching the connection's `include_globs`, off-scope reads are denied and audited (a `file.access_denied` entry in Settings → Audit Logs), large files page cursor-by-cursor via windowed reads, and a new `index_mode` tier (`none` / `metadata` / `content`) selects live listing, a cached file list, or a keyword index.
- **Agent notes (#631)** — the agent keeps a per-report markdown scratchpad it writes and reads while answering — plans as `- [ ]` checklists, findings, and progress — surfaced read-only in the report and injected back into the planner each iteration; gated by the `enable_agent_notes` org setting.
- **Per-model vision toggle (#632)** — admins can manually turn a model's image (vision) support on or off, and the choice persists even for preset models whose flags otherwise re-sync from the catalog; the toggle controls what the app sends (not the model's real capability), gated on `manage_llm` with a `llm_model.vision_toggled` audit entry.
- **Per-user MCP tool policies with in-run approval (#633)** — every MCP / custom-API tool now carries an `allow` / `ask` / `deny` / `auto` policy resolved per user (personal preference → agent overlay → connection default, with admin `deny` absolute). `ask` pauses the report run with an approval card (Allow once / Always allow / Deny / Always deny — "always" is remembered for future runs), `deny` hides the tool from the agent entirely, and `auto` lets the org's small default model review each call and approve or decline it with a visible reason. Enabling/disabling tools and setting default policies stays admin-only; members see the tools read-only and control only their own preference. Also hardens tool re-discovery: an empty provider response no longer wipes existing tools (or the overlays and preferences attached to them).

## Version 0.0.451 (July 12, 2026)
- **Doc "Save as PDF" exports the full document** — the print stylesheet isolated the document with `position: fixed`, which clips output to a single viewport box and cut the PDF off after a couple of pages; it now uses `position: absolute` with a content-driven height so tall docs (and their charts) paginate across the whole export in both the viewer and the editor.
- **Tighter default doc typography** — document body text is now 13px with a tighter 1.65 line-height (down from 15px / 1.75), and headings are scaled down a notch to match (h1 28→24px, h2 20→18px, h3 17→16px, h4 15→14px), for a compact, document-scale feel in both the viewer and editor.

## Version 0.0.450 (July 12, 2026)
- **Jaeger connector (#624)** — query distributed traces over the Jaeger Query HTTP API; each backend exposes `services`, `operations`, `spans`, and `dependencies` tables, and span search filters by service, operation, tags, latency, and errors.

## Version 0.0.449 (July 12, 2026)
- **`load_step` reuse is now opt-in (#620)** — the coder/planner feature that reuses a prior step's results via `load_step` is gated behind a new org setting `enable_load_step` (default **off**). Only steps built within a recent window (a fixed 300s) are advertised as reusable; re-running saved code that references older steps is unaffected. `load_entity` (published catalog entities) is independent and unchanged.
- **`new`/`חדש` starts a fresh report on Teams and WhatsApp (#619)** — sending a message that is exactly `new` or `חדש` on Teams 1:1 or WhatsApp forces a brand-new conversation report instead of reusing the recent one, so users can explicitly start over mid-conversation.
- **Power BI connector: workspace scoping and reliable connection test (#617)** — a new optional `workspaces` filter scopes discovery/indexing to named workspaces; the connection test now probes multiple datasets and classifies failures by layer (an engine-level error on an empty model passes with a warning), and listing/query calls gain Retry-After-aware backoff on 429/5xx.
- **Dependency security fixes (#618)** — resolved Snyk-reported Critical/High/Medium vulnerabilities in frontend (esbuild, tar, dompurify, markdown-it, and other transitives) and backend (pillow, pypdf, httplib2, pydantic-settings, setuptools) dependencies; zero Critical/High findings remain.

## Version 0.0.448 (July 11, 2026)
- **Document creation (#613)** — the analyst can now write findings as markdown documents, a new artifact type alongside dashboards and slides. Docs carry live charts, mermaid diagrams, tables, and per-claim citations (built for root-cause analyses, deep-dive reports, and memos), render in the report panel and on shared links, export to Markdown/PDF, and are editable in place by the report owner — with full RTL (Hebrew/Arabic) support.

## Version 0.0.447 (July 11, 2026)
- **Reliable completion streaming (#612)** — SSE now survives page refreshes, network drops, and backgrounded mobile tabs: the client reconnects and resumes live (running tool cards and the stop button included) instead of degrading to polling or showing a false error.

## Version 0.0.446 (July 11, 2026)
- **OpenAI model presets** — add GPT-5.6 Sol, Terra, and Luna; keep GPT-5.5 available, make Terra the default model, and retire older GPT-5.4/5.2 presets.

## Version 0.0.445 (July 11, 2026)
- **MCP connectors** — cleaner connect experience: pre-filled provider auth, tool previews, and one-click public agents.
- **Custom agent icons** — pin an emoji or connection icon per agent.

## Version 0.0.444 (July 11, 2026)
- **Elasticsearch connector** — query logs and metrics across indices, patterns, and data streams via the DSL (plus SQL/ES|QL); the index mapping is the schema, and rolling daily indices collapse into a single `<name>-*` pattern table.
- **Splunk connector (enterprise)** — investigate events across indexes and sourcetypes with SPL; the `index::sourcetype` catalog is enumerated cheaply and fields are sampled for the highest-volume sourcetypes, with the rest discovered on demand.
- **Thin-table field discovery** — `describe_tables` now samples a schema-on-read table's fields on inspection (so the agent stops treating "0 columns" as empty) and matches pattern/namespaced names (`security` → `security-*`, `web` → `web::access_combined`).

## Version 0.0.443 (July 10, 2026)
- **Prometheus connector (#595)** — query metrics with PromQL over the Prometheus HTTP API; each metric becomes a table.
- **Concurrent multi-tool execution (#598)** — one planner decision can run its tool calls in parallel (e.g. `create_data` across several sources), controlled by the `ai_tool_concurrency` org setting (defaults to 4; set to 1 for serial).
- **Per-connection request rate limit (#592)** — enterprise admins can cap requests per minute/hour/day on a connection, enforced as a hard block with audit logging.
- **Model-authored tool-call titles (#593)** — connection/external tool calls show a short human-readable label (e.g. "Searching Notion for churned customers") that streams live.
- **WhatsApp outbound images (#590)** — charts and image files are now sent to WhatsApp as native images with captions.
- **RTL email auto-detection (#597)** — free-form emails with Hebrew/Arabic content are automatically rendered right-to-left.
- **LLM selector shows the model's provider icon (#596)** — the prompt-box model button reflects the selected model's provider instead of a generic icon.
- **Zabbix connector (enterprise) (#591)** — query hosts, metrics, triggers, active problems, events, and metric history via the JSON-RPC API.
- **Fix iOS focus-zoom on the report prompt box (#600)** — the mobile prompt field is pinned to 16px so tapping it no longer zooms the viewport.

## Version 0.0.442 (July 9, 2026)
- **Fix SSO login for invited users with mismatched email casing** — invite emails are now matched case-insensitively so members can sign in via Entra/OIDC regardless of the casing the identity provider returns, and the provider's actual error is surfaced on the sign-in page.

## Version 0.0.441 (July 8, 2026)
- **AI-suggestion evidence in the Knowledge Explorer (#573)** — AI-proposed instruction changes now carry a brief evidence line (why the change was suggested), shown in review hover cards and the instruction detail.
- **Fix blank instruction editor in dev (#575)** — dedupe `prosemirror-state` so the tiptap editor mounts instead of rendering an empty body; falls back to a raw-markdown textarea if initialization ever fails.
- **Fix profile Usage tab never updating (#576)** — usage counters now record without a hard cap configured, and the tab refreshes the session on open instead of showing stale zeros.
- **Sandbox-violation feedback loop for codegen (#577)** — `unsafe_python` violations consume a retry instead of ending the run, and the failed code + error are fed back into the next generation attempt so it can self-correct.

## Version 0.0.440 (July 8, 2026)
- **Oracle thick-mode support for legacy servers (#548)** — Oracle connections to servers or accounts that python-oracledb thin mode can't handle (pre-12.1 versions, 10G-only password verifiers producing DPY-3015, Native Network Encryption) failed at connect time. The Docker image now bundles Oracle Instant Client 19c (amd64/arm64) and the backend switches the driver to thick mode at startup whenever the libraries are present — thick is a superset of thin, so existing connections are unaffected; hosts without the libraries (dev, airgapped) keep thin mode. Set `ORACLE_THICK_MODE=0` to force thin mode.
- **Oracle TCPS (TLS) connections (#548)** — the Oracle connector gains a "Use TCPS (TLS)" toggle for listeners that only accept TLS-encrypted SQL*Net (plain-TCP clients get their connection reset), plus a "Verify SSL" option that can be disabled for internal-CA certificates (thin mode only — thick mode's TLS trust requires an Oracle wallet).

## Version 0.0.439 (July 7, 2026)
- **ServiceNow connector (beta) (#563)** — new `servicenow` data source: query ITSM data (incidents, changes, problems, requests, CMDB, users) through the REST Table API with encoded queries. Bulk schema discovery from `sys_db_object`/`sys_dictionary` resolves inherited fields (incident ⊂ task) and turns reference fields into foreign keys; curated default table set with `tables` override and a `discover_all` mode for custom `u_*`/`x_*` tables; human-readable display values by default; actionable error when the instance user lacks metadata read access (a failure ServiceNow reports as HTTP 200 + empty result). Also fixes connection creation ignoring a registry entry's explicit `client_path`.

## Version 0.0.438 (July 7, 2026)
- **Triggers (#562)** — user-owned webhooks that spawn agent sessions, plus report-per-run routing for scheduled tasks, under a new Automations page.
- **QVD indexing progress (#564)** — real per-file indexing progress with stop, file size, and duration.
- **WhatsApp fixes (#565)** — agent replies (text + data) are now delivered back to WhatsApp, and the account-verification page shows WhatsApp branding instead of Slack.

## Version 0.0.437 (July 7, 2026)
- **OpenSearch data source connector (#560)** — indices, aliases and data streams become queryable with the query DSL, aggregations, or SQL.

## Version 0.0.436 (July 6, 2026)
- **Excel (.xlsx) export for CreateData + Hebrew CSV fix (#559)** — the CreateData result download becomes a CSV / Excel dropdown, with `.xlsx` generated server-side via `GET /steps/{id}/export?format=xlsx`. Every CSV export path (server, email attachment, client-side) now emits a UTF-8 BOM so non-ASCII (e.g. Hebrew) headers and values render correctly in Excel instead of ANSI mojibake, and a Unicode widget title no longer crashes the download (RFC 6266 `filename*`).
- **Fix agent-name chips and unreachable suggestion popover in instruction review (#558)** — the instruction editor's agents chip now shows the real agent name instead of its raw UUID even for deactivated or hidden agents (and lets you remove them), and the pending-review Accept/Reject popover is a single JS-positioned floating card anchored under the pointer — fixing it rendering far from the change and vanishing en route in RTL.

## Version 0.0.435 (July 6, 2026)
- **X (Twitter) MCP connector (#553)** — X's first-party MCP server (`https://api.x.com/mcp`) added to the connector catalog as a one-click tile with bearer-token (app-only) auth.
- **`wait` tool for Agent V2 (#554)** — the agent can pause the current turn and auto-resume after a one-shot delay (data refresh still running, rate limit, "try again in 30 minutes"), with a live countdown pill and cancel; not a scheduled task (ephemeral, sub-hour, self-deleting).
- **Scope agent-prompt visibility to membership (#555)** — prompt read visibility now mirrors the /agents list: admins see agent-scoped prompts only for agents they explicitly belong to (owner always sees their own); write/manage authority is unchanged.
- **Fix report rerun for artifact dashboards (#556)** — `POST /reports/{id}/rerun` now re-executes the artifact's query default steps (previously a silent no-op for artifact reports), the refresh reports its true outcome, and the retention purge skips reports shared in any mode so dashboards no longer go blank.


## Version 0.0.434 (July 5, 2026)
- **`network_dir` file connector (#519)** — new data source pointing at a directory (local folder or mounted SMB/NFS share) with `list_files` / `search_files` / `read_file` plus the first *write* capability for file sources (`write_file` agent tool). Path traversal and writes to read-only connections fail closed at a single chokepoint. No migration.
- **Seat cap enforced on all auto-provisioning paths (#540)** — the license `max_users` cap now applies to domain-signup invites, chat auto-provision, LDAP group sync, SCIM provisioning, and OIDC group sync (previously only admin invite / CSV import). New `app/core/seats.py` is the single source of truth; existing members are never blocked — only creation beyond the cap is refused.
- **Reliably responsive `create_artifact` dashboards (#545)** — the page/dashboard code-generation prompt now carries a concrete required responsive-layout section (fluid container, mobile-first grids, wrap/overflow rules), so generated dashboards reflow from the ~360px chat side-panel to full-screen.
- **Structured report schedule builder (#549)** — the report-refresh Schedule modal replaces the fixed dropdown with the structured recurring builder (every N minutes/hours, time of day, weekday chips, day of month), sharing one composable with the schedule-task modal.
- **Honor org row limit on refresh (#550)** — `limit_row_count` now applies to all data re-generation paths (report rerun, query run/preview, entity refresh/preview), not just initial creation; setting the limit to 0 correctly means "no limit" instead of returning 0 rows.


## Version 0.0.433 (July 4, 2026)
- **Claude Fable 5 support** — Claude Fable 5 (`claude-fable-5`) is now a selectable Anthropic preset model.
- **Mobile web UI pass (#534)** — responsive fixes across the main mobile screens. The public artifact top bar is now icon-only on mobile (no more overlapping Back/tabs/Refreshed/Edit/✕), and the artifact/report browser tab (and "Add to Home screen" shortcut) shows the report title instead of the report UUID. Inputs are forced to 16px on small screens to stop iOS focus-zoom, full-height shells use `h-dvh`, and a mobile navigation drawer (hamburger + slide-in sidebar) makes Home/Reports/Dashboards/Settings reachable on a phone. The "Configure your LLM" banner is desktop-only, the report chat prompt box is full-width and aligned with the message/tool content, and the CreateData tool's children are mobile-friendly (data-table columns fill the width with no stray pagination footer; chart x-axis labels no longer overlap). Desktop layout is unchanged.
- **Fix /agents connections footer overflow** — the connections pane's "View all" footer no longer spills outside the pane.
- **MCP tool rows show the connector's icon** — `execute_mcp` rows in the report chat now render the catalog connector's brand icon (Monday, Jira, …) or the MCP logo for custom servers, instead of a generic glyph.


## Version 0.0.432 (July 3, 2026)
- Fix role-management RBAC ↔ legacy-role divergence and sso_only login lockout; adds backfill migration `rbacbf01` (#529)
- Fix extreme slowness on report/artifact pages with large data (#531)
- Fix missing numbers & empty charts in emailed/exported dashboard PDFs (#527)
- Fix pending-changes badge counting rows the pending view can't show (#528)
- Training-mode-for-agent-admins verification plan (#530)


## Version 0.0.431 (July 3, 2026)
- CSV data source connector (#522)
- Claude Sonnet 5 / Opus 4.8 support (#523)
- Enforce prompt write policy at the route layer (#524)
- Localized, direction-aware follow-up suggestions (#521)
- Fix single-value cards rendering the wrong cell as the metric (#520)


## Version 0.0.430 (July 2, 2026)
- Faster instruction loading
- Instructions view in reportagent vs knowledge view
- Prompt to support week day start


## Version 0.0.429 (July 2, 2026)
- **Faster page navigation (#513)** — batched whoami's per-org RBAC resolution, fixed the monitoring/reports N+1s, added hot-path indexes, and share one DB connection per request; pages load noticeably faster and no longer stall under load.

## Version 0.0.428 (June 29, 2026)
- **File references + MCP file materialization (#497)** — adds a `file_reference` model/route/service and materializes files surfaced by connector tools (MCP resources, Graph mail attachments) so they can be referenced as first-class files. Wires file materialization into `execute_mcp` / `read_mcp_resource` / the MCP client and adds a Graph mail client path. Backed by two migrations (`filesrc01` adds a file source-kind, `fileref01` adds the file-references table), chaining off the service-accounts head. Adds unit tests for the reference service and MCP file materialization.
- **/agents tree — lazy-load instructions + server-side search (#494)** — the Agents tree no longer loads **all** instructions on mount (`GET /instructions?limit=200`) and derives everything client-side. It now draws from cheap aggregate **counts** and loads rows **lazily on expand**. New backend endpoints: `GET /instructions/counts` (badge aggregates with no row hydration, same visibility filter as the list), `GET /knowledge/search?q=` (cross-entity grouped search over agents + instructions), and an `?global_only=true` list filter. The frontend mounts with counts + agents only, lazy-loads rows per group/agent on expand (with per-node spinners), turns "search everything" into a grouped server-side results view, and keeps a deduped lazy row cache. Validated with `tests/e2e/test_instruction.py` (17 passed).
- **Prompts tools in Training mode (#495)** — the training-mode agent can now curate reusable **Prompts** the same way it curates Instructions, via three new agent tools (`create_prompt`, `edit_prompt`, `search_prompts`, all `allowed_modes=["training"]`) surfaced as rich, localized tool cards. Unlike instructions, prompts go **live immediately** (no draft/approval build) by writing the `Prompt` row directly via `PromptService`, and authoring is governed by the agent-manager (`manage`) tier from #489 — `create`/`update` already require `manage` on each target agent (or org admin for `scope="global"`), so the tools inherit that gate with no new permission. New tool-card components mirror the instruction cards (scope/starter/param badges, `{{param}}` chips, Live state, "Open in Prompts"); localized across all 10 locales.
- **Service accounts for API use (#493)** — adds **service accounts**: non-human, org-managed API principals for automation/integrations, with their own RBAC role and API keys, owned at the org level (survive offboarding) and not tied to a person. A core (non-EE) capability gated by a new `manage_service_accounts` permission (covered by `full_admin_access`). Implemented as a `ServiceAccount` row backed by a hidden `users` row (`is_service_account=True`, `is_active=False`), so existing `users.id` FKs / ownership / RBAC work with no attribution migration; org binding lives on a dedicated `service_accounts` table so an SA consumes no license seat and never leaks into member lists. Login (JWT/SSO) is blocked while API keys keep working; a `forbid_service_account_principal` guard prevents a leaked SA key from minting keys, creating accounts, or assigning roles, and role assignment is capped to the creator's own permissions. New Service Accounts sub-tab under Settings → Members. Alembic migration `c2d3e4f5a6b7`.
- **Agent-manager RBAC tier (#489)** — a per-agent `manage` grant is now the "agent-manager" tier: a non-admin who owns or is granted `manage` on an agent can fully manage **that** agent — its tables, instructions, entities, evals and members — while staying scoped to their own agents. `manage` now implies `manage_instructions` / `create_entities` / `manage_evals` / `manage_members` (+ `view`/`view_schema`) on the **same** data source (not org-wide), the three table-mutation endpoints move from the read-tier `view_schema` to `manage`, and global instruction/entity creation stays gated on org-level `manage_instructions` / `create_entities`. Mirrored in the frontend (`usePermissions`, table/tools editing UI, and an agent settings panel that highlights the current user's effective role). Also adds **per-connection RBAC grants** (`manage_connection` / create / manage-agents) so a connection owner or grantee can manage that connection's config and build agents on it — surfaced in the role editor and the create-agent connection dropdown, with backend resolver support and e2e coverage.
- **Agent admins publish their own agents' instructions live + pending changes visible in the tree (#489/#494 follow-up)** — instruction publish was gated only on org-level `manage_instructions`, so an agent admin's create/edit on their own agent was staged as a pending non-admin proposal and never reached the main build, leaving it invisible in the lazy `/agents` tree (and spamming admins with review notifications). The auto-publish decision is now data-source-scoped: an agent admin (per-agent `manage`) auto-approves + promotes builds scoped entirely to their own agents (org admins still publish anything; authoring an org-wide global instruction stays an org-level capability). Separately, the tree's lazy list and counts now surface instructions that are still awaiting approval (e.g. AI/training suggestions) — rendered with an amber "Pending review" dot + chip and a "not live yet, waiting for approval" tooltip — instead of hiding them. The agent's runtime instruction set is unaffected (it reads the main build directly).
- **External MCP tool gateway (#487)** — BoW's external MCP server (`/api/mcp`) can now act as a gateway in front of each agent's connected **MCP servers** and **custom APIs**, letting an external MCP client discover and trigger those tools through BoW alongside the existing `create_data` / `inspect_data` surface. New `ConnectionToolGateway` service resolves an agent's tools and computes effective enable/policy from the per-agent overlay (allow-only over the gateway); new `list_agent_tools` (discovery with full input schemas) and `execute_mcp` (invocation) MCP tools; `get_context` now advertises each agent's tools plus a `tools_hint`.
- **Gate low-confidence notifications (#486)** — the `low_confidence` review producer fired on every answer scored below 3/5, which felt like it triggered on nearly all prompts. A `low_confidence` notification is now only surfaced once an agent accumulates **5 answers scored below 3/5 within a rolling 7-day window**; below the floor the low score is tracked silently via the completions ledger. Per-agent dedup and dismissal/resurface behavior are unchanged.
- **Release DB connection before serialization on hot reads (#485)** — every authenticated request held one pooled DB connection for its entire lifetime (across response serialization too), so a burst on the `/agents` page could exhaust the pool and stall every endpoint at a uniform ~10s. Adds `release_request_db(db)` and calls it at the end of the hot read handlers (`/reports`, `/instructions`, `/instructions/pending-changes`, `/data_sources/active`, `/data_sources/{id}/full_schema`) so the connection returns to the pool before serialization — mirroring the proven SSE early-release pattern.
- **Connectors without agents (#467)** — tool providers (e.g. Notion) are now usable standalone without wrapping them in a full analytical agent, including Notion dynamic client registration (DCR) OAuth and connector-aware UI in the Knowledge Explorer (`connector_key`, `is_connector`, a "Connector" badge). Localized across all 10 locales.
- **Quota policies — monthly spend cap in USD (#488)** — usage policies can now cap monthly LLM **dollar spend** (`monthly_spend_limit_usd`) in addition to tokens, queries, and data volume. Spend is tracked in micro-USD on a new `llm_cost_micro_usd` usage counter, computed per LLM call from the same per-model token rates the Cost console uses, and buffered on the per-agent `UsageLimitContext` then flushed at end-of-run (mirroring the token path). The pre-call quota check now also stops a user once their buffered+recorded spend reaches the cap (a 429 with metric `llm_cost_micro_usd`). The Create/Edit Quota modal gains a **Monthly spend limit (USD)** field and a per-policy **Spend** badge; the whoami usage-quota summary exposes a `spend` metric in USD. Localized (en/he/es).
- **Localize the monitoring Cost tab + RTL (#492)** — the `/monitoring` Cost tab used `monitoring.cost.*` keys that existed only in `en.json`, so every other locale silently fell back to English. Adds the full `monitoring.cost` block (33 keys) plus the `tabCost` label to all 9 non-English locales, localizes the echarts trend tooltip/series name, and makes the metric toggle RTL-ready (physical `border-l` → logical `border-s`, `space-x-2` → `gap-2`).
- **Fix — `[object Object]` in Custom API headers/endpoints (#491)** — in the Custom API "Edit connection" form, the **Custom Headers** (`dict`) and **Endpoints** (`list`, `ui:type: "json"`) fields fell through to a plain-text input bound to an object/array and rendered as `[object Object]`. Tags `headers` with `ui:type: "keyvalue"` to use the key/value row editor, and adds a `json` field type to `ConnectForm.vue` (monospace textarea with parse/serialize sync, inline "Invalid JSON" error, and a proper array/object default).

## Version 0.0.427 (June 28, 2026)
- **Reports sidebar sorts by last activity (#479)** — the report list (sidebar and `/reports`) now orders by real conversation activity (`is_starred DESC, last_activity_at DESC`) instead of creation date, so an active chat moves to the top. Backed by a new denormalized, indexed `reports.last_activity_at` column bumped at two coarse choke points (new user message, agent turn finalize) and backfilled from `MAX(completions.created_at)`, keeping the list read cheap (no join to the high-volume completions table). The sidebar "REPORTS" header now links to `/reports`.
- **Run scheduled prompts on demand (#474)** — a new **Run now** button in the scheduled-prompt modal triggers a scheduled prompt immediately without waiting for its cron, via a new `…/scheduled-prompts/{id}/trigger` endpoint. Manual runs use `force=True` to bypass the cross-worker claim and the `is_active` pause check, are restricted to the prompt owner (404 for missing, 403 for unauthorized), persist any unsaved edits first, and navigate to the report to watch execution. Localized across all 10 locales.
- **Global Evals in the /agents tree (#478)** — a new **Global Evals** entry below **Skills** in the Knowledge Explorer surfaces org-wide test cases (those scoped to all agents) in one place rather than only inside each agent's Evals panel. Admin-gated by `manage_evals`, with per-agent-only controls (reliability badge, Self Learning, "Run evals now") hidden in global mode. Client-side filter only — no backend changes.
- **Audit coverage for prompts, RBAC, webhooks, OAuth & more (#466)** — closes the high-priority gap where ~75 state-changing endpoints emitted no audit trail. Adds best-effort, route-level `audit_service.log(...)` calls (capturing IP / user-agent, wrapped so an audit failure never breaks the request) for prompts, scheduled prompts, RBAC (roles, groups, memberships, role assignments, resource grants), webhooks, OAuth clients, usage policies, and external user mappings. Action types are discovered dynamically by the audit UI, so no registry change is needed. Adds `docs/design/audit-trail-coverage.md` documenting the full inventory and deferred follow-ups.
- **Fix — report title sometimes never set on Postgres (#475)** — the auto-generated report title was written by a fire-and-forget `asyncio.create_task` that got garbage-collected before its LLM call returned (worst on Postgres, where the pooled connection recycles the instant the response completes), leaving reports stuck on `untitled report`. Title generation now runs inline so the DB session stays alive, and is gated on the title value (empty/placeholder) rather than "first completion" — making it self-healing across turns. The sidebar also live-updates the title in place (with a fade-in) via a `report:updated` event instead of waiting for the next navigation.
- **Fix — notification inbox order + never blank on a bad row (#477)** — the inbox could show a non-zero unread badge over an empty "all caught up" panel when the list endpoint 500'd on a single malformed row, and (when items showed) sorted severity-first so stale high-severity rows outranked fresh ones. The list now sorts newest-first (severity only as a tiebreaker), swallows unrepresentable timestamps, falls back to DB ordering on any sort error, and serializes each row independently so one poison row degrades to a placeholder instead of blanking the list. The frontend now shows a "Couldn't load / Try again" state instead of masquerading a failed fetch as an empty inbox.
- **Fix — slow /agents instructions load (#476)** — the Instructions tree-pane could take a minute+ on large orgs because `get_pending_change_instruction_ids` ran an N+1 (`review_hunks()` once per pending instruction org-wide, ~8 SQL statements each). Replaced with a fixed set of bulk queries plus an in-memory diff pass — byte-for-byte identical results with the same per-hunk rules. Measured on a 600-instruction seed: 4801 → **4** SQL statements and 5.41s → **0.28s** service time.
- **Fix — TraceModal outer scrollbar clipped summary badges (#473)** — incomplete cancellation of the UCard body padding pushed the trace modal past its fixed height, adding an outer scrollbar that overlaid the summary strip and clipped the right-aligned LLM-judge score badge (`Resp x/5`). Removes the card body padding (matching `BuildExplorerModal`) so the modal no longer overflows. Template-only.
- **OpenShift (OCP) deployment fixes (#468)** — removes the redundant file-based log handler (`RotatingFileHandler` → `logs/app.log`), which broke under OCP's read-only root filesystem; logs now go to stdout/stderr only (12-factor). Also disables asyncpg SSL auto-detection for unconfigured connections (`ssl_mode` unset → `ssl=False`), preventing the `~/.postgresql/` client-cert lookup that failed with "Permission denied" under OCP's arbitrary-UID pods. No breaking change: `ssl_mode: require` / `verify-full` behave as before.

## Version 0.0.426 (June 27, 2026)
- Add **Prompts** - save and reuse prompts. Including running for usrers
- Added **Notifications** - sharing/alerts from agents will be shown here
- Redesigned main nav menu to include inline reports, prompts, and clean ups

## Version 0.0.425 (June 25, 2026)
- **Channels (integrations) settings — full localization + RTL fixes (#452)** — the six **Settings → Channels** panels (Slack, Microsoft Teams, WhatsApp, AI Mailbox, Excel Add-in, OAuth Clients) were hardcoded in English; every user-facing string is now wired through i18n with a new `settings.integrations.channels.*` key set translated into all 10 locales (code literals like `users:read.email`, `manifest.xml`, and `X-Hub-Signature-256` kept literal via slots). Also fills previously-untranslated integration keys that silently fell back to English, and fixes RTL in `OAuthClientsModal` (physical `ml-`/`mr-`/`right-` → logical `ms-`/`me-`/`end-`), keeping list rows icon-left / status-right under RTL.
- **Personal API keys in the User Profile + MCP modal fix (#451)** — the user profile modal gains an **API Keys** tab (list, one-time-reveal generate, delete) for the `bow_…` tokens used by the MCP server / programmatic access, reusing the per-user `/api/api_keys` endpoints. Also fixes a bug where the MCP modal always showed **"0 API tokens"** when opened from the sidebar: its key-list `watch` wasn't `{ immediate: true }`, so on the already-open (`v-if`) mount the loader never ran. New `profile.apiKeys.*` strings across all 10 locales.
- **Per-model LLM access control (RBAC, enterprise) (#449)** — admins can restrict individual LLM models to specific users, groups, or roles. Models are **open by default**; restriction is opt-in per model (`is_restricted`) and reuses the existing `ResourceGrant` permission machinery. When a model is restricted, only principals with an explicit grant can see and use it; **full admins bypass** restrictions and **members without a grant get 403**. An **always-available guard** prevents restricting the org default / small-default models (400) so an org can't lock itself out, and enforcement is **fail-open** when the `llm_access_control` license feature is inactive (no regression for non-EE installs). Enforced in both `get_models` (list/picker) and `get_model_by_id` (the real completion-selection boundary), with audited `…/models/{id}/access` and `…/models/{id}/restricted` routes. Surfaced as a new **Access** column + grant modal in the models settings table and in the role editor.
- **Apache Druid — API token (bearer) authentication** — the Druid connector now offers an **API Token** auth method alongside Username / Password, for endpoints that authenticate with a bearer token (e.g. Imply Polaris API tokens). The token is sent as `Authorization: Bearer <token>` via the driver's `jwt` path and is mutually exclusive with Basic auth (a token suppresses any user/password). Pick it from the connection's auth-method selector; the token is encrypted at rest like any other credential.

## Version 0.0.424 (June 25, 2026)
- **Fix — single-value cards show the asked-for value over melted KPI tables (#446)** — when `create_data` produces a melted/long KPI result (`Metric | Value | Format`, one row per metric), a single-value `count` / `metric_card` no longer renders the wrong row (the date, or the sum of every metric). The row-selecting default filter is now carried through `create_data` and `agent_v2` (previously dropped), derived deterministically when the viz model omits it (`derive_kpi_row_filter`), and applied in `ToolWidgetPreview` via the view's own `defaultFilters`.
- **Monitoring — surface origin platform in diagnosis/trace (#447)** — the monitoring diagnosis table and trace modal now show where each agent run originated (Slack / Teams / WhatsApp / MCP / Email vs. the web UI). An origin platform icon sits next to each run's message and an origin badge in the trace header (web-UI runs show none); `external_platform` is plumbed through `AgentExecutionSummaryItem`, `ConversationTraceResponse`, and `console_service`. The diagnosis **User** column moved next to the prompt, and **Date** now shows date and time in the org timezone.

## Version 0.0.423 (June 25, 2026)
- **Cost console — LLM spend by user / agent / group over time (#440)** — a new **Cost** tab under `/monitoring` for admins, breaking LLM token and dollar spend down by user, agent (data source), group, model, provider, or feature (scope) over a date range, with KPI totals, a daily cost/tokens trend chart, and a per-dimension breakdown table. Backed by new attribution columns (`organization_id` / `user_id` / `report_id` / `data_source_id`) on `llm_usage_records` — all nullable, so pre-existing rows stay org-scoped and the rest surface as an **"Unattributed"** bucket. Attribution is stamped once per agent run via an ambient context var and snapshotted at record-schedule time, so it survives the background recorder and the worker-thread judge (tool sub-calls included).
- **Follow-up question suggestions (#441)** — after each answer in the web app, the agent proposes a few natural next questions, rendered as minimalist chips below the feedback bar. Gated to web sessions only (Slack/Teams/Email/Excel/scheduled runs are excluded) and to the org setting **`enable_follow_ups`** (on by default, surfaced in AI settings). Suggestions are generated inline at the tail of the agent run on the small/default model, persisted on the completion (`follow_ups` column) and pushed over SSE for instant render, and rehydrated on reload.
- **Report avatar branding + per-model provider logo (#442)** — the assistant avatar in a report now renders the **organization's uploaded brand image** (falling back to the BoW logo), height-bound and aspect-ratio-safe, with a small overlay badge for the **LLM brand** that produced each completion and a `Generated with …` tooltip. Brand resolution is name-first (`claude → anthropic`, `gpt/o1/o3 → openai`, `gemini → google`), falling back to the hosting provider type, so a Claude/GPT model served via Bedrock or a custom OpenAI-compatible endpoint still shows its true model brand. The shipped Anthropic provider icon is replaced with the orange **Claude** mark (square burst for compact slots, burst + wordmark for the provider picker).

## Version 0.0.422 (June 24, 2026)
- **Dark mode** — comprehensive dark theme across the app using Tailwind's `dark:` variant strategy driven by `@nuxtjs/color-mode` (default preference `system`, so the OS setting is respected on first load). Form inputs get explicit dark backgrounds, light PNG illustrations are hidden in favor of icon placeholders, and ECharts render with a dark theme. Choose Light / Dark / System per user from the new profile modal's **Appearance** tab.
- **User profile modal** — a new modal opened from the sidebar user menu (**Profile**), with four sections: **General** (avatar upload/remove, editable full name, email, and a summary of linked external platforms), **Custom Instructions** (a personal note about yourself surfaced to the AI when it works on your behalf — stored as your per-org membership note), **Usage** (per-user tokens/queries/data for the current window, with a clear notice when usage tracking isn't enabled), and **Appearance** (theme + language). Avatars are stored on the user (`users.image_url`, migration `usravatar01`) and now also render in the sidebar and on your messages in reports.
- **Per-user language override** — each user can pick their own interface language from **Appearance**, overriding the organization default for their account only (persisted per browser). The profile modal is fully localized across all supported languages.

## Version 0.0.421 (June 24, 2026)
- **Auto-reindex schedule — interval _or_ fixed time** — a connection's scheduled schema reindex (including QVD sources) can now run **either** on a recurring interval (every N minutes/hours, 10-minute minimum) **or** at a fixed daily time, chosen per connection in the connection detail panel. Fixed times are interpreted in the organization timezone. (Enterprise `scheduled_reindex`.)
- **Organization timezone** — a new **Settings → General** option that sets the org's IANA timezone. Timestamps across the app (reports, monitoring, audit, instructions, integrations, etc.) now render in it, scheduled jobs (reindex times and scheduled reports) fire in it, and the planner is told the current time in it. Storage stays UTC — the timezone only governs schedule interpretation and display; leaving it unset keeps the prior browser-local behavior.

## Version 0.0.420 (June 23, 2026)
- **Fix — BigQuery queries failed with "Please install the 'db-dtypes' package"** — added the `db-dtypes` runtime dependency so BigQuery results convert to dataframes correctly.

## Version 0.0.419 (June 23, 2026)
- **Instructions — scope by run-mode and delivery channel** — instructions can now be restricted to specific agent run-modes (Chat, Deep analytics, Training) and delivery channels (Web app, Slack, Teams, AI mailbox, MCP). The selectors live in a new collapsible **Advanced** section of the instruction editor (empty = applies everywhere). The scoping is honored at prompt-build time so an instruction only loads in the modes/channels it targets, and the fields are versioned (snapshotted into instruction versions and carried through build promotion/diffing).
- **Fix — instruction "Pending review" status was inconsistent across views** — the same instruction could read **Active** in the agent instruction view but **Pending review** in the report agent panel. The list and single-instruction endpoints now derive the pending signal from the same authoritative per-hunk review rule as `/instructions/pending-changes`, so a leftover/already-applied (covered) build no longer over-reports as pending. The status dropdown in the report agent editor also stops showing a value ("Pending review") that wasn't one of its options.
- **Fix — "+" to add an instruction was hidden behind the Review panel** — clicking the **+** on Instructions while the Review feed was open now closes Review and opens the new-instruction editor instead of doing nothing.

## Version 0.0.418 (June 23, 2026)
- **Microsoft Analysis Services (SSAS) data source** — a new enterprise connector for SQL Server Analysis Services over XMLA, supporting both Multidimensional (MDX) and Tabular (DAX/MDX) models.

## Version 0.0.417 (June 21, 2026)
- **Infor OLAP (Infor d/EPM) data source (#425)** — a new enterprise connector for the Infor d/EPM OLAP semantic layer (formerly Infor BI / MIS Alea OLAP), the supported path into on-premise **Infor OLAP 25.x** where native connections are gone and **XMLA is mandatory**. It speaks the standard **XMLA SOAP** contract over HTTP with Basic auth: schema discovery via `Discover` (catalogs, cubes, dimension hierarchies, and measures — each cube surfaced as a `Catalog/Cube` table whose columns carry their MDX `unique_name`), and query execution via `Execute` (Tabular) that runs **MDX** and flattens the rowset into a DataFrame (decoding XMLA `_xHHHH_` escapes). SOAP faults and inline XMLA errors surface as clear errors. Configurable endpoint URL, optional catalog scope, SSL verification, and timeout.
- **Agents — connections footer fixes** — the bottom-left **Connections** footer is no longer pushed off-screen (requiring a scroll) when a top banner is shown: the Knowledge Explorer now sizes itself to the viewport minus the banner height. It also shows an explicit **"Add connection"** CTA in the empty state, and **childless connections** — created but not yet linked to any agent — now appear in the list instead of being hidden until an agent exists.
- **Fix — Tables selector "Save" button hidden until scroll** — the Save bar in the tables selector is now pinned (sticky) to the bottom of its scroll container, so it stays visible without scrolling to the end of long table lists (agent Tables panel, schema wizard, onboarding, etc.).
- **Fix — report tool card flicker** — the `edit_instruction` tool card no longer rapidly flickers between its rendered document and its `v1 → v2` version-diff view during/after an edit stream. The card is keyed on the stable block id so streaming/poll updates no longer remount it.
- **Fix — Microsoft Fabric "Login timeout expired" on cold-start endpoints** — Fabric Warehouse/Lakehouse SQL endpoints are serverless and can be slow to respond on the first connection after the capacity has been idle, routinely exceeding the ODBC driver's short default login timeout (~15s) and surfacing as `HYT00 … Login timeout expired (SQLDriverConnect)`. The Fabric client now sets a generous 60s login timeout (`Connect Timeout` + `pyodbc` `timeout`), adds driver-level `ConnectRetryCount`, and retries transient connection-timeout SQLSTATEs (`HYT00`/`HYT01`/`08001`/`08S01`) a few times with backoff so a cold endpoint gets a chance to wake up. This affects both service-principal and per-user (OBO/Entra) auth.

## Version 0.0.416 (June 21, 2026)
- **Backend dependency management moved from pip to uv (#408)** — `requirements_versioned.txt` is replaced by a PEP 621 `pyproject.toml` + `uv.lock`, the Docker build and CI now use `uv sync --frozen`, and contributors install with `uv sync --extra dev` (see `DEV.md`). uv is from the same Astral toolchain as ruff and is significantly faster than pip/Poetry.
- **Security — resolved all High/Critical dependency vulnerabilities** flagged by Snyk in both the backend (uv) and frontend (yarn) dependency trees.
  - Backend: `cryptography` 46.0.7 → 49.0.0 (out-of-bounds read) and `starlette` 0.50.0 → 1.3.1 (SSRF, resource exhaustion, unsafe reflection, request smuggling, incorrectly-resolved name). Resolving Starlette required matching bumps to `fastapi` (→ 0.138.0), `fastapi-mail` (→ 1.6.5), and `aiosmtplib` (→ 5.1.2), which previously capped it. Backend scan now reports **0 issues**.
  - Frontend: `nuxt` → ^3.21.7 (open redirect), `vite` resolution corrected to `>=7.3.5 <8` (directory traversal — the prior resolution pinned the vulnerable 7.3.3), and a new `ws` resolution `>=8.21.0` (asymmetric resource consumption). Frontend now has **0 High/Critical** issues.
- **Docs** — added `docs/snyk-dependency-scanning.md` (skill-format guide) covering how to scan the uv backend and yarn frontend with the Snyk CLI and apply fixes.

## Version 0.0.415 (June 20, 2026)
- **Knowledge Explorer** — a new three-pane workspace (at `/instructions`) for browsing and managing everything an agent knows: global instructions, skills, per-agent resources, pending reviews, and each agent's tables and tools. Tree navigation with search and filtering (by status, load mode, source, category), inline editing of titles/descriptions/conversation starters, file upload and preview, and a version-history pane with diff view.
- **Agent management** — a guided **New Agent wizard**, a dedicated **Agent Settings** panel, and per-agent **automation settings**, plus a clearer public/private agent distinction surfaced across the UI.
- **Continual & self-learning** — agents can automatically run evals and a retrain/reliability loop (e.g. on instruction or table changes), surfaced in a new **Agent Evals** panel, so higher-autonomy agents keep improving on their own.
- **Skills with smart loading** — instructions can now be authored as **skills** that load on demand: the prompt carries a lightweight skills catalog and the agent reads a skill's full body only when it needs it (`read_skill`), keeping context lean.
- **Suggestions & review workflow** — a **pending-review feed** with **per-hunk tracked changes** (accept or reject individual edits), diff visualization, and an approval flow for instruction suggestions.
- **Better instruction management** — instruction **descriptions**, table-scoped instructions with name-based datasource fallback, and improved reference resolution (connection-table IDs, bare and schema-prefixed table names).

## Version 0.0.414 (June 18, 2026)
- **Fix — long instructions hid the Edit button in the instruction modal** — in the global create/edit instruction modal, a long instruction body made the content area un-scrollable and pushed the action footer (Edit in view mode; Update/Cancel in edit mode) off the bottom of the modal, so the instruction couldn't be edited. The modal now keeps a properly bounded flex layout so the content scrolls internally and the footer stays visible.

## Version 0.0.413 (June 18, 2026)
- feat(mcp): let the agent read MCP server resources (list_mcp_resources + read_mcp_resource)
- fix: prevent 'Cannot use import statement outside a module' in artifact iframe 

## Version 0.0.412 (June 16, 2026)
- **Apache Druid data source** — connect to Apache Druid and query it as a new data source.
- **Trino data source** — connect to the Trino distributed SQL engine and query it as a new data source.
- Agents page redesign - easier navigation around instructions, tools, tables, tc
- **Continual Learning** - trigger evals -> retrain loop on table change or instruction change for high autonomy

## Version 0.0.411 (June 15, 2026)
- **⌘K command palette** — a global **⌘K / Ctrl+K** palette for quick navigation and creation, opened from anywhere in the app. One input searches across **recent reports**, **agents**, and **instructions** (server-side search for reports/instructions, client-side filtering for agents; recents shown by default), with pinned, query-echoing create actions: `New report "…"` (creates and navigates) and a permission-aware `New instruction "…"` / `Suggest instruction "…"` that opens the instruction modal pre-filled with the typed text. No-match queries still surface the create actions.
- **Publishing lifecycle for agents (`publish_status`)** — a manager-set publishing state, distinct from the system-managed connection-health flag: **published** (visible to everyone with access), **draft** (visible only to builders who can `manage` the agent), and **disabled** (hidden everywhere and excluded from AI context). Viewer-aware filtering applies across the data-source/agent selector, schema context, and public (Slack) listing; consumers see only published agents while managers also see drafts. Existing agents are backfilled to *published*.
- **Agent research tools (`search_reports` / `read_report`)** — two read-only planner tools that let the agent discover and read **the current user's own reports**: `search_reports` lists/substring-searches the caller's reports by title with status/mode filters, and `read_report` returns one of the caller's reports (metadata, data sources, artifact summary, conversation). Both are strictly scoped to the caller — any other report, including ones merely shared with the user, returns *not found* (no leak) — and each has its own tool card in the report view.
- **Settings → Channels** — the settings **Integrations** tab is renamed **Channels** across all locales, with a redesigned page (and a new empty state). The **SMTP Server** configuration moves from a modal into its own dedicated settings page.
- **Test Connection for existing LLM providers** — the **Test Connection** button is now available when editing an existing LLM provider, not just when adding a new one. Blank credential fields fall back to the stored (encrypted) values, so you can re-test a saved provider without re-entering secrets.
- **Instruction modal redesign** — the create/edit instruction modal gains a wider split layout with a dedicated, slide-in **analysis panel** (related instructions, impacted prompts, and impact score) and a cleaner global-vs-private form structure.
- **Scheduled tasks** — clicking a scheduled task card now opens its edit modal directly (clicking the report name still navigates to the report), and the modal shows a link back to the report when editing a task tied to one.
- **Fix — second admin sees empty tables on shared OBO/Fabric agents** — on a shared-catalog `user_required` (Fabric/PowerBI/OBO) data source, a second admin with a valid delegated token saw zero tables and *Reload tables* didn't help, because the reload refreshed only the canonical catalog and never the caller's per-user overlay. The shared-catalog reload now also refreshes the caller's overlay so their tables appear immediately, without leaking the canonical catalog to disconnected callers.
- **Fix — race when deleting a data source during background indexing** — deleting a data source while a background connection indexer was re-syncing schema tables could reintroduce `datasource_tables` rows and trigger a foreign-key violation. The delete now re-clears the schema tables and retries until the indexer stops producing rows.
- **Fix — RTL alignment** in the Clarify tool.

## Version 0.0.410 (June 13, 2026)
- **Email the AI analyst (AI Mailbox)** — a new **Email** channel (alongside Slack/Teams/WhatsApp) lets people email the analyst and get answers back. It's IMAP/SMTP under the hood (provider-agnostic: Microsoft 365, Google Workspace, or any self-hosted server) with three auth modes — password/app-password, **Microsoft 365 app-only OAuth** (XOAUTH2), and **Google Workspace** (service account + domain-wide delegation). Inbound mail flows into a report, the agent replies in-thread (with a deep link back to the report), and **attachments are ingested as report files** (size-limited). Configure it from **Settings → Integrations**, with an inline **Test connection** before saving. IMAP is the optional upgrade that turns a send-only mailbox into a two-way channel.
- **Verify-first inbound identity** — by default a new sender must prove they control both the mailbox *and* a BOW account: first contact gets a **verification link** that, clicked while signed in, creates a trusted `email → user` binding (subsequent mail is trusted, like Slack/Teams). A spoofable `From` alone never grants data access. A pre-filter (DMARC/DKIM where available + domain allowlist + loop/auto-reply suppression) drops spoofers and noise first; registered-but-unlinked users, open invites, and signup-admitted domains each get the appropriate link rung, and everything else is **ignored + audited**. Auto-linking without verification is now an explicit, clearly-labeled opt-in.
- **Org SMTP transport** — a dedicated **SMTP Server** setting (separate from the AI Mailbox) becomes the org's transport for *system* mail — share notifications, scheduled-report/prompt results, verification links — overriding the global `bow-config` SMTP. The password is Fernet-encrypted at rest, no-auth/anonymous relays and a `validate_certs` toggle are supported, and there's a pre-save Test connection. Analyst mail always uses the mailbox; system mail never does — the two transports are kept strictly separate.
- **Scheduled schema auto-reindex** — connections can now periodically re-index themselves so tables stay fresh, with a per-connection toggle and a configurable interval (every N hours) in the connection detail modal; the last reindex error is surfaced inline. Scheduled reindexing is an enterprise feature.
- **Per-org license quotas** — licenses can now cap `max_users` and `max_agents` per organization (claims read from the license JWT; missing/negative means unlimited), enforced on user and agent creation.
- **Guaranteed data access on every dashboard** — building on 0.0.409's component ⓘ popover, an always-on, LLM-independent **DataInspector** (a floating "Data" button auto-mounted into the dashboard iframe) lists every visualization with the same **Data**/**Code** tabs, so even fully custom dashboards that never use the prebuilt cards still expose their backing data and query. A bare `<EChart>` outside a SectionCard now also carries the ⓘ popover. Suppressed in headless thumbnail/preview renders.
- **Instruction pill fix** — the report completion pill now includes **system-category** instructions (previously hidden), and partial/pill accepts are reflected correctly in the knowledge group.

## Version 0.0.409 (June 11, 2026)
- **Built-in info popover on dashboard components** — the prebuilt KPICard and SectionCard now carry a small ⓘ popover that surfaces a component's backing data. It opens on a **Data** tab (the actual visualization rows in a compact scrollable table) with a **Code** tab for the generating query, plus metadata above (source, type, row/column counts, active filters) and the viz id in a persistent footer. Both producers wire it automatically: deterministic "Add to Dashboard" codegen emits `viz={viz[N]}`, and the `create_artifact` / `edit_artifact` prompts instruct the model to do the same. The popover is **filter-aware** — when a component renders filtered rows it shows exactly what's on screen ("X of Y rows (filtered)") and only attributes filters that map onto the viz's columns, falling back to the full dataset otherwise.
- **Spark Connect data source** — new connector for querying Spark via the Spark Connect protocol, with partition metadata in the schema, a pre-flight `EXPLAIN` gate (partition-filter + scan-size guard), and a Spark icon in the data-source picker.
- **Scheduled tasks on specific days of the week** — recurring scheduled prompts can now target specific weekdays (e.g. Mon/Wed/Fri) instead of only daily/interval cadences, with localized day labels (including conventional Arabic/Hebrew day-of-week abbreviations).
- **Copy invite link always returns a usable link** — copying a pending member's link now rotates the token and resets the 14-day window if the invite has expired (or had no token), clearing the **Expired** badge; a still-valid link is returned unchanged so an already-emailed link isn't invalidated. No email is sent (that's **Resend**).

## Version 0.0.408 (June 10, 2026)
- **Roles, groups & quotas for not-yet-registered members** — admins can now assign RBAC roles, add to groups, and set a usage-policy (quota) on a *pending* invite (a user who hasn't signed up yet). These are stored against the invite and automatically materialized onto the user when they register, so access is correct on their very first request. Invites can also be pre-assigned at invite time (role/group/quota fields in the Invite modal), and removing a pending member cleans up its role/group/quota assignments.
- **Token-gated invites with expiry + resend** — invite links now carry a single-use token and expire after 14 days. On local/password sign-up the token is required: an invalid, expired, or missing token (for an invited email under closed signups) blocks account creation entirely. SSO/OIDC sign-up is unchanged (the IdP verifies identity, no token needed). A per-row **Resend** action (Members tab, requires `manage_members`) rotates the token, resets the 14-day window, and re-sends — the old link stops working immediately. Admins can also fetch a pending invite's link via an admin-only endpoint (handy when SMTP is off). Pending rows show an **Expired** status when the window lapses.
- **Reliable, human invite & welcome emails** — the invite email is now sent synchronously with retries + a per-attempt timeout (no more silent fire-and-forget), and the outcome (`sent` / `failed` / `skipped_no_smtp`) is surfaced to the admin. New users get a plain-text **welcome email** summarizing the agents (data sources) they can access with a link in. Copy is plain-text and human (no buttons), signed "BOW".
- **Members tab overhaul** — compact, cleaner table; checkbox selection with **bulk actions** (add role, add to group, remove); client-side **pagination**; row **Resend**; the **Actions column is frozen** to the right while the wide table scrolls; borderless inline Role/Quota selects; consistent role-name casing; collapsed group chips ("+N"); wider Note column with tooltip. The **Groups** and **Quotas** tabs now share the same compact styling.
- **Private data sources by default (#364)** — newly created data sources / agents are now private by default (`is_public = false`); only explicitly-added members (and admins) can see them unless opted public. Adding a member to a data source now sends a **delayed "you've been added" email** (5-minute delay, re-validated at send time so an undone add never mails, claimed so exactly one worker sends).
- **MCP search (#366)** — `search_mcps` supports wildcard queries (list everything) and ships a clearer tool description.

## Version 0.0.407 (June 9, 2026)
- Fix "Shared with me" reports linking to the owner's `/reports/:id` page (which renders blank for non-owners) — they now open the read-only shared conversation view at `/c/:token`. Shared reports without a share token are no longer clickable.

## Version 0.0.406 (June 9, 2026)
- SQL Server connections can now pass extra ODBC keywords (e.g. `ApplicationIntent=ReadOnly` to route to a read-only Always On replica) via a new optional **Additional Connection Parameters** key-value editor in the connect form. Security-sensitive keys (Encrypt, credentials, driver, server, database) cannot be overridden, and existing connections are unchanged.

## Version 0.0.405 (June 9, 2026)
- QVD date/timestamp/time fields now load as real DATE/TIMESTAMP/TIME columns instead of raw Excel-style serial numbers, so they filter, sort, and group as dates

## Version 0.0.404 (June 8, 2026)
- Fix duplicate scheduled emails/reports under multi-worker/replica deployments — each cron fire is now claimed once via a DB-backed lock so exactly one worker runs it (also covers cache warmups, payload purge, and LDAP sync)
- License expiry now takes effect without a restart, plus a global expiry-countdown banner and a redesigned license settings page (tier/expiry details, expiring-soon and expired states, renew CTA)
- Small (<10 row) create_data results are no longer sent to Slack/Teams and are auto-collapsed in the report UI, since the agent's text already states the values
- Manage an agent's primary instruction from the agent page: edit, replace with an existing instruction, or start a training session
- Many-series (>8) line/bar/area charts now use a scrollable vertical legend docked on the right instead of an overflowing horizontal one
- Data-source and agent pickers grow to fit long names instead of truncating
- Fix report auto-title silently not saving (mostly on Postgres) when the background task outlived its DB session

## Version 0.0.403 (June 8, 2026)
- **Teams** — a reused Teams 1:1 conversation report (up to 5 days old) now re-syncs its data sources to the user's current access on each message, so grants appear and revocations disappear without waiting out the window.
- **UI** — the data-source members panel relabels the management column to "Management role" and the empty state to "Query only" (was "None"), and clarifies that everyone listed can query the agent and that Remove is what revokes access.

## Version 0.0.402 (June 8, 2026)
- Admin query-identity toggle for delegated (Entra ID / Microsoft Fabric OBO) connections — admins/owners can now choose, per connection, to run queries as the **service account** (the connection's principal) or as **themselves** (their own delegated/OBO token), from the connection detail modal. Default is "Me": the service principal is never used silently for an admin's interactive queries — if they have no personal token yet, the query is blocked and the UI prompts them to Connect. The selection is persisted per (user, connection) and applied consistently across the tables selector (overlay vs shared catalog), the agent's schema context, and query execution (inspect/create data).

## Version 0.0.401 (June 7, 2026)
- Agent run activity chart in /monitoring diagnosis — daily agent executions bucketed by status (success/error) with click-to-filter by day, backed by a new diagnosis timeseries endpoint
- Add a `bagofwords` MCP skill template documenting the core analysis workflow (create report, run tracked queries, build dashboards) for use with the BOW MCP connector
- MCP error handling: tool-level MCP failures (`isError`) now surface the server's real error message instead of `None`, so the agent can correct course instead of retrying blindly — and failed MCP calls no longer show a misleading green ✓ in the trace
- MCP planner context: the `execute_mcp` digest now echoes which underlying tool was called and with what arguments (plus the real error on failure), so the planner stops looping through call variants
- MCP tool UI: the tool card now shows the actual command/input invoked (tool + arguments for `execute_mcp`, query for `search_mcps`, code for `write_csv`), not just the result

## Version 0.0.400 (June 7, 2026)
- Teradata Vantage data source integration — connect Teradata as a data source, with sample queries included in the client description
- Generated-code reuse via `load_step`/`load_entity` — the planner and coder now prefer loading a prior step's results over rebuilding from scratch, reducing redundant code generation
- Fix LLM token-usage undercount in /monitoring (no added latency)

## Version 0.0.399 (June 7, 2026)
- Fix MCP tool results aborting the agent run: materializing a large/tabular MCP result to a file linked it to the report before the file's id was assigned, causing a foreign-key violation that poisoned the shared transaction (surfaced as "transaction is aborted" / agent execution errors). File linking now happens after the id is set and inside a savepoint, so a materialization failure degrades gracefully instead of failing the whole run. Also restores CSV preview generation, which was silently broken.

## Version 0.0.398 (June 6, 2026)
- Inbound webhooks for reports — connect GitHub, Jira, or any other service (Generic catch-all) so external events flow into a report's chat. Configure them from the report Summary tab; each report's webhook count shows in the reports list.
  - Per-webhook signing key with three verification modes: token header (default — a shared secret, works with Jira Cloud and most legacy systems), HMAC signatures (GitHub-native or BOW's own scheme), and URL token (for senders that can only POST). Per-org delivery dedup and rate limiting, plus a one-time URL + key reveal on create/rotate.
  - Optional small-model AI classifier decides whether an event warrants a response — guided by an optional per-webhook prompt plus your org instructions and the report's conversation — and, when it acts, authors the task the agent runs. The event entry shows a live 👀 (working) → ✅ (done) status; declined events are marked "no action needed".
  - Gated org-wide by the new "Report Webhooks" setting (on by default), with org limits for max webhooks and delivery rate.

## Version 0.0.396 (June 6, 2026)
- Star (favorite) reports — starred reports are pinned to the top of /reports. Starring is per-user, so each person keeps their own favorites, and you can star reports shared with you read-only

## Version 0.0.395 (June 6, 2026)
- Native web search for OpenAI and Azure OpenAI (provider-executed, via the Responses API) — opt-in per provider and gated by the org Web Fetch setting, with a live "Searching the web" step (rendered as a tool with the query + cited sources) and source citations

## Version 0.0.394 (June 6, 2026)
- Fix scheduled tasks running one weekday late (cron day-of-week off-by-one vs the scheduler), and the schedule editor showing the wrong day
- Conversation history now records scheduled-task and email actions (so the assistant can dedupe schedules, cancel the right task, and recall what it emailed)

## Version 0.0.393 (June 6, 2026)
- Scheduled tasks: ask the agent to run something on a recurring schedule (e.g. "email me once a week about ...") — new create/cancel scheduled-task tools, reusing the existing scheduled-prompt UI

## Version 0.0.392 (June 5, 2026)
- Major performance & concurrency-reliability improvements (faster completions, fewer stalls under load)

## Version 0.0.391 (June 3, 2026)
- Email sending tool in reports when SMTP is enabled
- Postgres support for materialized views
- Enhance tableau system prompt

## Version 0.0.390 (June 3, 2026)
- Improve tests reliabilty

## Version 0.0.389 (June 2, 2026)
- Security patches/dependecy updates
- OneDrive indexing fixes
- Athena connector: support boto3 default auth and optional S3 output location

## Version 0.0.388 (May 25, 2026)
- Hide intercom for mobile
- Sharepoint/onedrive/Google drive integrations
- Quick integration of agents

## Version 0.0.387 (May 25, 2026)
- Performance improvements

## Version 0.0.386 (May 25, 2026)
- UI improvement for knowledge group
- auto-link teams/slack members

## Version 0.0.384 (May 24, 2026)
- Improve instructions mgmt and creation
- Add web/http tools to code gen

## Version 0.0.383 (May 21, 2026)
- Improve ds selector to support 'auto' mode
- Performance & reliability fixes
- Clarify tool enhancement
- Added new tool: list agent execution in training mode
- Add MCP to multiple agents

## Version 0.0.382 (May 20, 2026)
- speed improvements
- web fetch tool v2

## Version 0.0.381 (May 18, 2026)
- web fetch tool
- custom system prompt for each platform
- add timestamps for completions

## Version 0.0.380 (May 17, 2026)
- Tableau performance and reliability improvements

## Version 0.0.379 (May 16, 2026)
- fix background completion API
- security patches and fixes

## Version 0.0.378 (May 13, 2026)
- Per-member admin-managed `note` (per-org) injected into the planner prompt as `<user_profile>` context
- Bulk import members from Excel/CSV with dry-run preview; idempotent — never touches roles or group memberships
- Local password sign-in now works for admins as a break-glass when `auth.mode = sso_only`
- Cleaner sign-up disabled error message

## Version 0.0.377 (May 13, 2026)
- Allow SMTP without credentials (use_credentials: false) for anonymous/open relays

## Version 0.0.376 (May 11, 2026)
- Fix connection-indexing crashes ("attached to a different loop" / "unknown protocol state 3") on long Postgres-backed indexing runs by giving the background runner its own NullPool engine

## Version 0.0.375 (May 10, 2026)
- Fix MSSQL "0 tables" on case-sensitive / binary collations (e.g. Hebrew_BIN)
- Surface MSSQL schema introspection errors instead of silently returning empty

## Version 0.0.374 (May 10, 2026)
- Enrich instructions mgmt and diff
- Fix filter bug in widget preview

## Version 0.0.373 (May 8, 2026)
- Query timeout settings
- remove answer tool

## Version 0.0.372 (May 7, 2026)
- Fix clarify tool not verbose enough

## Version 0.0.371 (May 7, 2026)
- Agent db writes - performance/reliability
- better signal in create data tool
- instructions ui fixes

## Version 0.0.370 (May 6, 2026)
- Performance/reliability improvements

## Version 0.0.369 (May 3, 2026)
- add usage / quota limits policies organization wide

## Version 0.0.368 (May 2, 2026)
- add locale for additional languages
- improve UI for agent mgmt and data soures
- allow upload files (csv/xls/pdf) to agents

## Version 0.0.367 (May 1, 2026)
- add a new reindexing connection button
- enable mcp tools by default in org settings
- strengthen clarify tool

## Version 0.0.366 (April 27, 2026)
- 70% speed improvements
- better caching for tokens

## Version 0.0.365 (April 26, 2026)
- Performance improvements
- Change to a faster token counter approach
- Planner v3 (native Anthropic tool_use) is now the default; set `BOW_PLANNER=v2` to fall back to the legacy JSON-envelope planner
- Anthropic prompt caching on planner system prompt + tool catalog; `cached_tokens` instrumentation for OpenAI/Azure
- Async DB writes for `finish_tool_execution` + `upsert_block_for_tool` (next planner call no longer blocks on the prior turn's persistence)
- Measured impact (3/3 trial pass rate, identical plans/SQL): per-trial cost -69% on both Haiku 4.5 and Sonnet 4.6; wall-clock -29% on Sonnet, -5% on Haiku; input tokens -73%

## Version 0.0.364 (April 25, 2026)
- feat: evals tools for training mode
- loading mode to when adding new connectins with a large amount of objects
- auto draft new evals when (admin) user thumbs up
- fix bug when submitting a new prompt when completion ends but in agent knowledge harness mode 
- added native support to GPT-5.5


## Version 0.0.363 (April 22, 2026)
- Improve prompting for Azure default guardrails
- Put oauth in the admin settings
- Improve infer widget visualizations to include filter and agg

## Version 0.0.362 (April 20, 2026)
- PBI on-prem server improvements

## Version 0.0.361 (April 20, 2026)
- Remove nuxt from prod deployment and serve static files via FastAPI
- feat: add Power BI reporting server (on-prem)
- feat: add Oracle BI integration

## Version 0.0.360 (April 19, 2026)
- Fix QVD type parsing
- Improving qvd -> duckdb reliability and performance

## Version 0.0.359 (April 19, 2026)
- Enhance Sybase client for better code/timout/error handling
- Add instruction button in Agent panel
- Improve Dockerfile

## Version 0.0.358 (April 18, 2026)
- SSO + OBO for data connections: OIDC login now extracts email from the id_token, syncs groups, and propagates user identity through to the warehouse
- Entra ID native support for the On-Behalf-Of flow, including `offline_access` and hardened OAuth connection handling
- Permission overlay revokes stale rows when a user loses upstream access; data sources returning 403 are skipped instead of failing the run
- SIEM integration with end-to-end test coverage
- Dashboards and Scheduled Tasks promoted to first-class items in the main navigation
- Per-domain signup controls for opening up self-serve access
- New Excel-specific tools for spreadsheet artifacts
- `exportCSV()` available as a sandbox global so artifacts can produce CSV downloads
- Improved dashboard-generation system prompt for more reliable multi-widget layouts
- Evals harness (dogfooding): YAML suites under `tests/evals`, pytest runner, LLM matrix from `LLM_MODEL_DETAILS`, JudgeRule with execution metadata (tokens, iterations, per-tool durations), tag-based filtering, multi-turn support, SSE streaming, and per-turn completions/reasoning in failure reports

## Version 0.0.356 (April 13, 2026)
- Share dashboards / conversations with specific users or globally

## Version 0.0.356 (April 11, 2026)
- BOW for Excel - you can now have BOW inside your excel!
- PowerBI enhancements

## Version 0.0.355 (April 10, 2026)
- Show instruction usage and attribution per turn
- New sidebar in report page to show summary, dashboard and current agent
- New knowledge harness for agentic instruction suggestions
- Faster instructions management
- UI improvements across report and dashboard views
- RBAC: groups, roles, policies, per-data-source permissions, and connection/MCP tools authorization
- LDAP integration for enterprise authentication
- WhatsApp Cloud API integration
- Spider text-to-SQL benchmark eval driver
- Fix: make SMTP password optional in settings
- Added support for a .bowignore file when integrating a git account

## Version 0.0.354 (April 5, 2026)
- New Scheduled Tasks: set up recurring or scheduled tasks within reports
- New "Add to Dashboard" button to instantly add widgets to an artifact
- New "Polish" action for quick dashboard refinements
- Show recent queries and artifact shortcuts above the prompt box
- Improved dashboard generation speed and performance
- Improved agent filtering by prioritizing master tables for more reliable results
- Added sandbox support for better agentic code development
- Display abort status during tool execution

## Version 0.0.353 (March 30, 2026)
- feat: new a2a integration for timbr
- increase timeout in agent harness

## Version 0.0.351 (March 29, 2026)
- WAL mode for SQLite deployments and timeout settings for PostgreSQL
- Performance improvement for the main completion flow
- Add timing metrics across code gen / execution for agent execution traces 

## Version 0.0.350 (March 29, 2026)
- Add ability to integrate custom MCPs
- Add NetSuite native integration

## Version 0.0.349 (March 28, 2026)
- Performance improvements
- additional logging

## Version 0.0.348 (March 26, 2026)
- Improve Sybase integration and SQL Anywhere to use tds config

## Version 0.0.347 (March 25, 2026)
- Improve context compaction to include inspect_data and set a budget of 200k (overriden by known models if exist)
- Add agent indicator/icon to agent trace
- Add download as png button for charts
- Add more filters to reports page and advanced search

## Version 0.0.346 (March 24, 2026)
- Fix bug that images are sent in future completions
- Allow support for secret/access key in Bedrock LLM service

## Version 0.0.345 (March 24, 2026)
- Make test_connection and other data client utils async calls

## Version 0.0.344 (March 23, 2026)
- Fix artifact sandbox: download React development builds in vendor script
- Remove CDN fallbacks for airgapped deployments — missing vendored libs now fail loudly

## Version 0.0.343 (March 22, 2026)
- Set headers/handling for streaming in HTTP calls from front-end
- Improve context mgmt budgeting 
- Fork previous created reports

## Version 0.0.342 (March 22, 2026)
- Fix context bloat when designing dashboards
- Add full SCIM support
- Enhanced audit trail with more activities
- Expose OpenAPI swagger docs
- Improve animation and frontend look and feel when streaming messages
- Send PDF attachment when publishing a dashboard
- Add read_query tool
- Improve dashboard generation and editing
- BOW for Excel initial set up
- New: GPT-5.4 and GPT-5.4-mini native integration

## Version 0.0.341 (March 18, 2026)
- add opentelemetry

## Version 0.0.340 (March 18, 2026)
- create/edit artifact tool improvements

## Version 0.0.339 (March 17, 2026)
- Sybase connector to support owner schema
- Keep alive for long running MCP queries

## Version 0.0.338 (March 16, 2026)
- minor fixes and changes

## Version 0.0.337 (March 15, 2026)
- added support for MSSQL 2008 (ODBC 17)
- improve artifact generation (speed and reliability)
- added support for Sisense BI

## Version 0.0.336 (March 12, 2026)
- feat: notification service for sending emails — supports dashboard sharing, conversation sharing, and scheduled report delivery with optional PDF attachment

## Version 0.0.335 (March 9, 2026)
- fix: improve timbr semantic layer integration
- fix: llm usage chart to show both input and output

## Version 0.0.334 (March 8, 2026)
- feat: add support for snowflake semantic views
- fix: improve mssql integration to support schema
- fix: mcp improvements
- Add support for databricks multi-catalog discovery

## Version 0.0.332 (March 7, 2026)
- Improved MCP-Apps stability and compatibility with Claude
- Enhanced Databricks SQL connector reliability
- Increased OAuth token storage limits
- Added logging to LLM integrations
- Fix connectivity issues via MCP servers

## Version 0.0.330 (March 5, 2026)
- Pre-cache tiktoken encodings in Docker build for airgapped environments
- Added more logging

## Version 0.0.328 (March 5, 2026)
- fix: when gpt-5 is in model_id string, don't add temprature

## Version 0.0.327 (March 4, 2026)
- Allow skip verify_ssl for custom LLM endpoints
- Intrdouce native Bedrock integration, with IAM/API Key auth methods
- Support MCP-Apps! Now using the MCP in MCP-Apps compatible clients will render visualizations and dashboards
- Introducing Timbr AI beta integration

## Version 0.0.325 (March 3, 2026)
- Fix Alembic migration SSL error when using Aurora PostgreSQL with IAM authentication

# Version 0.0.324 (March 2, 2026)
- Default SMTP config
- Improve k8s helm to support custom certs when using Aurora DB as backend

### Version 0.0.322 (March 1, 2026)
- Support long oauth string columns for Entra
- Allow AWS Aurora PG with IAM as backend DB

## Version 0.0.320 (February 24, 2026)
- Improve table lookup
- Improve OAuth MCP integration

## Version 0.0.320 (February 22, 2026)
- Support deployment in airgapped systems
- Improve PowerBI integration
- Improve Thumbnail generatio for Artifacts


## Version 0.0.319 (February 22, 2026)
- Fixed edit connection "Test Connection" to validate new credentials instead of using saved ones
- Credentials in edit mode are now locked by default with a "Change" button to explicitly unlock
- Renamed "Domains" to "Data Agents" in connection detail modal

## Version 0.0.318 (February 22, 2026)
- Added Sybase SQL Anywhere data source connector (enterprise license required)
- Uses FreeTDS ODBC driver for TDS protocol connectivity on port 2638

## Version 0.0.316 (February 21, 2026)
- Added filters for low score agent executions in monitoring/diagnosis
- Enhanced file upload and completion context handling, and special support for images
- Pass images and screenshots to create_artifact tool

## Version 0.0.315 (February 19, 2026)
- Improved organization logo upload
- Power BI: one table per internal table, relationship support, cleaner SharePoint names
## Version 0.0.314 (February 18, 2026)
- Added Microsoft Fabric data source integration (Warehouse and Lakehouse SQL endpoints)
- Azure AD Service Principal authentication support for Fabric
- Added `read_artifact` tool and improved context engineering for designing dashboards

## Version 0.0.313 (February 16, 2026)
- Update license env variable and secret configuration in k8s and docker-compose

## Version 0.0.312 (February 14, 2026)
- Refactor sidebar to use nav config and proper active states
- Improved slides artifact generation 

## Version 0.0.311 (February 13, 2026)
- Multi-connection support: data sources can now have multiple connections
- Added PowerBI and Qlik (QVD) data source integrations (Enterprise)
- Configurable step retention per organization (Enterprise)
- Exclude shared conversations and published reports from step cleanup
- Connection icons shown when describing/inspecting tables
- Schema enrichment with metadata and column comments
- Data agents and example agent templates
- Delete connections support
- Artifact thumbnails
- Added filtering for reports by schedule to easily view reports based on their schedule settings
- Added domain filtering for monitoring diagnosis to filter by specific domains
- Added report thumbnail generation and preview cards on home page for quick visual reference
- Added support for Claude Opus 4.6 model

## Version 0.0.309 (February 4, 2026)
- Create artifact (dashboard/slides) tool is now available via MCP 
- Added support for Databricks SQL
- Add enterprise license management and audit log

## Version 0.0.308 (January 31, 2026)
- Instruction @mentions now only show published instructions from the main build
- Referenced instructions are automatically loaded into AI context when a parent instruction mentions them
- Schema index and full schema now display instruction count per table, guiding the planner to use `describe_tables` for business rules
- Updated MCP `get_context` tool to expose instruction count per table
- **Microsoft Teams Integration**: Full bot support for Teams channels and 1:1 chats
  - Send questions via @mention in channels or direct message the bot
  - Thread-based conversations with report reuse across replies
  - User verification flow with Adaptive Cards
  - Markdown tables, count results, and report links rendered natively in Teams
  - JWT signature verification for inbound webhooks
  - Teams setup UI in Settings > Integrations

## Version 0.0.307 (January 28, 2026)
- Separated code and queries for better UX
- Added created/approved by metadata for instructions

## Version 0.0.306 (January 26, 2026)
- **New Interactive Dashboards**: Dashboards are now generated as executable React/HTML code, enabling rich interactivity, custom styling, and dynamic visualizations
- **Visual Feedback**: Upload screenshots or images with your prompts to show the AI exactly what you want—perfect for requesting design tweaks or pointing out issues
- Dashboard validation now includes automatic screenshot capture, allowing the AI to visually verify the output before finalizing
- Added vision model support for OpenAI, Anthropic, and Google Gemini LLM providers

## Version 0.0.305 (January 24, 2026)
- **Rebuilt Dashboards**: Now fully AI-generated as executable code (React/HTML) with iterative refinement based on conversation history
- Fixed @ mention detection in prompt input (no longer triggers inside existing mentions)

## Version 0.0.304 (January 22, 2026)
- SQLite data source now available in production (previously dev-only)
- Security updates and dependency patches

## Version 0.0.303 (January 22, 2026)
- AI-suggested instructions now show persistent "Published" status with timestamp
- Added checkbox selection when publishing AI suggestions 
- Fixed AI builds not being linked to agent executions

## Version 0.0.302 (January 20, 2026)
- Rename Catalog to Queries
- Show chart and visualization in query page

## Version 0.0.301 (January 20, 2026)
- Support for local DuckDB databases via file:// or absolute path i.e /data/myduck.db
- Set global git repo management

## Version 0.0.300 (January 19, 2026)
- **Slack Integration Enhancements**
  - Thread-based responses: replies now appear in threads instead of separate messages
  - Each thread corresponds to a single report for better conversation continuity
  - Added support for @mentions in channels (in addition to DMs)
  - Visual feedback via emoji reactions: 👀 when processing, ✅ when complete
  - Data source access control: channel mentions query only public data sources, while DMs include private data sources the user has access to

## Version 0.0.298 (January 18, 2026)
- Added guardrails around code execution
- Removed code validation flag, as it's now deterministic and built-in 

# Version 0.0.297 (January 18, 2026)
- Introducing: Training Mode
  - A dedicated mode for documenting and managing your data domain knowledge
  - Explore schemas, inspect data, and create instructions to guide AI behavior
  - New tools: `create_instruction` and `edit_instruction` for real-time instruction management
  - Instructions are versioned and tracked in draft builds until finalized
- Improve DuckDB system prormpt
- HBD!

## Version 0.0.296 (January 12, 2026)
- Added PostHog integration for analytics
- Fix Dockerfile

## Version 0.0.294 (January 12, 2026)
- improve streaming performance
- support heatmap charts
- block sending prompts if no llm or data source/file were set
- improve conversation layout for mobile presentation
- add delete connection

## Version 0.0.293 (January 10, 2026)
- Fix tables page not showing all tables when navigating between pages

## Version 0.0.292 (January 9, 2026)
- Fix demo data sources not loading in Docker container

## Version 0.0.291 (January 6, 2026)
- Improve streaming for final_message
- Fix multi bar chart rendering bug 

## Version 0.0.290 (January 1, 2026)
- Happy new year!
- Connections and data sources are now decoupled. You can attach multiple data sources to a single connection, each with its own tables, instructions, and evals. This brings much greater flexibility, reliability, and organization to your workspace.
- New: Context Selector – easily control which data sources are currently active throughout the application.
- Added ability to share report conversations with others
- Clarify tool and prompt optimizations

## Version 0.0.288 (December 26, 2025)
- UI improvements: eval, build ID
- Added modal to manage test suites
- Added new MCP tools: list, create, and delete instructions

## Version 0.0.286 (December 25, 2025)
- Auto suggest instructions if user provided negative feedback to an answer
- Improve auto-detect uvicorn workers

## Version 0.0.284 (December 23, 2025)
- Git providers: Now support Personal Access Token (PAT) authentication for seamless integration.
- You can now create pull requests and branches for build (instruction versions) directly from the interface.
- Each build now includes integration tests and eval runs to ensure greater reliability and code quality.
- Simplified instruction status life cycle and integrating to buid statuses
- UI/UX upgrades: Enhanced workflows for adding instructions and reviewing builds, making navigation and use smoother.
- Code clean ups and tests

## Version 0.0.282 (December 22, 2025)
- Launched instruction build/versioning system: every instruction update creates a new version, with point-in-time builds (snapshots), approval workflow, diff, and rollback.
- All instructions now tied to builds; `is_main` build sets active instruction set for org, with full history & audit.
- Added `/builds` API: get builds, build diffs, rollback, and detailed version/content lineage for every instruction.
- Test/Eval runs can select which build to use.
- Exposed top-k instructions retrieval API.
- Extensive automated E2E test coverage for build/version/rollback/git flows.

## Version 0.0.280 (December 19, 2025)
- Context and instructions are now unified
- Instructions now show detailed usage statistics
- New rules for instruction application: always apply, or smart based on relevance/search
- Instructions table redesigned—now with filters, git-sourced instructions, and other enhancements
- Improved create/edit instruction workflow with a refreshed design
- Expanded and updated automated end-to-end tests

## Version 0.0.279 (December 17, 2025)
- Added **MCP Server** for integration with Claude, Cursor, and other MCP clients
- Available tools: `create_report`, `get_context`, `inspect_data`, `create_data`
- MCP sessions are fully tracked in reports with tool executions and visualizations
- Added per-user API keys for MCP and external integrations

## Version 0.0.278 (December 15, 2025)
- Enhancing MongoDB integration to support Atlas/SRV connections
- Add more triggers for autogenerate suggestions 
- UI improvements/fixes

## Version 0.0.277 (December 14, 2025)
- Frontend tests (playwright) and CI/CD improvements

## Version 0.0.274 (December 12, 2025)
- Added support for GPT-5.2 model
- Enhanced the describe entity tool for better usability and accuracy
- Fixed a user authentication bug affecting specific environments

## Version 0.0.271 (December 10, 2025)
- Describe entity from catalog - new tool!
- Remove forgot password/etc when SMTP is not available

## Version 0.0.270 (December 10, 2025)

- bug fixes, performance and reliability

## Version 0.0.269 (December 10, 2025)
- Performance and speed

## Version 0.0.268 (December 9, 2025)
- Speed and readme

## Version 0.0.266 (December 8, 2025)
- Added a new **Inspect Data** tool for quickly examining the structure and sample content of a dataset and preview data before generating insights or diagnosing issues
- Docker Compose now bundled for both development and production environments
- Added sample databases to assist onboarding and demos
- Enhanced overall system reliability and robustness

## Version 0.0.265 (December 7, 2025)
- Bug fixes

## Version 0.0.264 (December 6, 2025)
- Enhanced file management and analysis capabilities (supports xls, csv, and pdf files)
- Improved MariaDB improvements
- Add support for loading up to 60K tables when connecting data sources
- Added automated tests for postgres database

## Version 0.0.263 (December 4, 2025)
- System prompt improvements and a new section for analytical standards
- Improvements to custom LLM integration (set default/small default models)
- Data source onboarding improvement

## Version 0.0.262 (December 2, 2025)
- Added data source integration to MongoDB
- Added native support for Custom LLM endpoints (openai compatible)
- Added support for Claude Opus 4.5

## Version 0.0.261 (December 2, 2025)
- Bias partitions in bigquery

## Version 0.0.260 (December 2, 2025)
- Dependencies updates
- Improve instructions list modal 

## Version 0.0.259 (December 1, 2025)
- Introducing Filters in dashboards
- Performance improvements, page loads, indices, reliability, and more
- Improved resources selector in context page (toggle between chunks/files, index status info, and more)
- UI enhancements


## Version 0.0.258 (December 1, 2025)
- Increase anthropic max tokens to 32k
- Impove behavior of reindexing (do not auto-add)

## Version 0.0.257 (November 30, 2025)
- Added Azure Data Explorer data source (thanks @licanhua)
- Improved BigQuery system prompt to consider special syntax guidelines when generating code

## Version 0.0.256 (November 29, 2025)
- Improved visualization features
- Enhanced dashboard creation workflow
- Suggestions now cover more user actions, such as corrections, querying the same tables, and sharing code
- Expanded instruction categories for system, dashboard, and visualizations
- UI improvements for agent trace, observations, and reduced visualization flicker
- Improved data source onboarding and test connections
- Added integration tests for LLMs and popular data sources

## Version 0.0.255 (November 27, 2025)
- Extended user token validity to one week, reducing the need for frequent logins
- Improved evaluation (Evals) features for more robust and insightful testing
- Added support for anonymous MySQL connections


## Version 0.0.254 (Noveber 25, 2025)
- Fix azure llm integration
- Improve mysql authentication 

## Version 0.0.253 (November 24, 2025)
- Gemini 3 Pro Preview added!

## Version 0.0.252 (November 22, 2025)
- Implemented tracking of LLM usage and associated costs in the console dashboard
- Enhanced metadata resource handling:
  - Remove objects no longer found during reindexing
  - Newly discovered objects are no longer auto-activated by default
- Introduced SQLite integration (for testing and development), and expanded test coverage for git repositories, metadata resources, and more
- Improved the process for deleting data sources
- Added bulk archive functionality for reports and revamped the main reports index page

## Version 0.0.251 (November 20, 2025)
- Data sources deletion

## Version 0.0.250 (November 19, 2025)
- Add context estimator when writing prompts

## Version 0.0.249 (November 19, 2025)
- Pinot get tables to use user:pass when creating the HTTP request

## Version 0.0.248 (November 18, 2025)
- Resolve flickering in the Reasoning section and enhance the reliability of data source deletion and modal overlays
- Improve stability and robustness of table auto-activation and deactivation

## Version 0.0.247 (November 17, 2025)
- Instruction labels added for more effective categorization and management
- Instructions can now be auto-enhanced with AI suggestions
- Message display now clearly distinguishes between user and agent responses
- Trace modal correctly navigates to the selected completion ID within the reports page

## Version 0.0.246 (November 16, 2025)
- Snowflake keypair auth
- Repair migrations

## Version 0.0.245 (November 16, 2025)
- Repair migrations

## Version 0.0.244 (November 15, 2025)
- Updating dependencies

## Version 0.0.243 (November 15, 2025)
- Fixing a couple of bugs and renaming release notes to CHANGELOG

## Version 0.0.242 (November 14, 2025)
- Enhanced markdown parser for better handling of complex formatting and edge cases
- Added support for Dataform projects and introduced SQLX file parsing, enriching contextual metadata for queries and models
- Integrated GPT-5.1 as an available LLM by default
- Improved metadata indexing service with additional guardrails for git repository management and error management
- Upgraded user interface for reports and tables

## Version 0.0.241 (November 14, 2025)
- Optimize datbase migrations to include report_type
- Wrap maintenance job with guardrails

## Version 0.0.240 (November 13, 2025)
- Introducing Evals! You can now create and run custom sets of tests on demand to assess system performance. Define your own test cases and assertions, such as:
  - User prompts triggering create_data on table1 and table2
  - Validating that specific data columns (e.g., a, b, c) are present
  - Using custom LLM Judge prompts to automatically determine pass/fail outcomes
- Added the ability to adjust the sample k size for schema tables and metadata resources
- Improved the data source pages for a faster, smoother experience, including enhanced loading indicators and improved item removal
- Unused steps are now auto-deleted after 14 days. You can restore them anytime by rerunning the code.

## Version 0.0.236 (November 13, 2025)
- Added sorting and filtering capabilities to the table selector
- Reduced logging verbosity in production environments
- Enforced strict limits on context section sizes

## Version 0.0.235 (November 12, 2025)
- Added ability to select and deselect items in table and metadata resource selectors
- Enhanced BigQuery integration to allow connections to multiple datasets
- Enforced organization-level uniqueness for data source and LLM provider names
- Allow service json for BigQuery required user auth mode

## Version 0.0.233 (November 11, 2025)
- Improved instructions visibility in prompts' context
- Introduced an "Analysis Panel" for admins when creating or approving instructions:
  - Impact Score Estimation: Evaluate how the new instruction relates to existing prompts and user questions
  - Related Instructions: Identify potential redundancy or conflicts with other instructions
  - Related Metadata Resources: Review if the instruction overlaps or conflicts with current enriched context (such as dbt, markdown, etc.)

## Version 0.0.232 (November 10, 2025)
- Introduced default small models: you can now designate a default "small" model for back-office operations such as evals, judge tasks, instruction generation, and more
- User feedback (thumbs up/down) is now attributed at the table level

## Version 0.0.231 (November 8, 2025)
- Enhanced the UI for agentic retrieval and search for greater clarity and usability
- Refined the agent head prompt to more effectively leverage and guide the use of search tools
- Improved the agent trace user interface for better readability and interaction

## Version 0.0.230 (November 6, 2025)
- Introduced a new create_data tool that is more robust, reliable and accurate data generation
- Enhanced code generation for more accurate and robust SQL and Python outputs
- Improved chart visualizations for clearer and more informative data presentation
- Added new data source integration support: Apache Pinot and Oracle DB
- Table browsing now displays detailed statistics, including usage frequency, scoring, and feedback metrics
- Launched the new `read_resources` tool for intelligent, on-demand searching across all metadata resources
- Added successful executed queries in the same tables for when agent is generating code


## Version 0.0.220 (November 4, 2025)
- Added BigQuery support for `maximum_bytes_billed` for cost guardrails and support for `use_query_cache`
- Improved main AI loop with additional observations from sub-agent create data (code, errors, etc)
- Improved UI for list of instrusctions modal - pagination, visibility, etc

## Version 0.0.219 (November 3, 2025)
- Improved table discovery and retrieval in main agent loop
- Introduced describe_tables tool for better data modeling, with light UI signaling
- Reduced the main agent's context footprint by 5x, significantly faster and leaner
- The create data sub-agent now receives a provided list of tables instead of inferring the data model itself

## Version 0.0.218 (November 1, 2025)
- Fixed issue where the data source form was not fully rendered in the onboarding screen
- Fixed issue where Claude outputs a Python code fence before the actual code

## Version 0.0.217 (November 1, 2025)
- Basic telemetry (configurable in bow-config)

## Version 0.0.215 (October 31, 2025)
- Support multi schema for Postgres client

## Version 0.0.214 (October 30, 2025)
- Support multi-db connection for ClickHouse

## Version 0.0.213 (October 28, 2025)
- Clickhouse fix
- Better rendering of booleans in connection form

## Version 0.0.212 (October 20, 2025)
- Integrate Mentions component and enhance prompt capabilities
- Implement mentions context integration in tools and agents
- Released: Catalog feature for efficient management and discovery of models, metrics, visualizations, and queries. Enables reusable components and enhances AI analyst intelligence
- Fix yarn cache issue in docker image

## Version 0.0.206 (October 19, 2025)
- Bug fix reloading tables in schema

## Version 0.0.205 (October 17, 2025)
- Added support for multiple schemas in Snowflake
- Added `MSSQL` driver into Dockerfile 

## Version 0.0.204 (October 16, 2025)
- Fixed permission issue in Docker when uploading files
- Fixed instructions not showing creator in instruction list

## Version 0.0.203 (October 12, 2025)
- Enhanced the chat interaction and conversation flow with the AI agent
  - Improved prompt capabilities by auto setting thinking levels
  - Enhanced message context with processed data and answer metadata for better LLM interactions
- Optimized CI/CD workflows by integrating GitHub Release automation

## Version 0.0.202 (October 8th, 2025)
- Added DuckDB support for object store files (aws, gcs, azure)
- Added Claude Sonnet 4.5 support

## Version 0.0.200 (September 27, 2025)
- Enhanced data source setup experience for new users
- Redesigned user interface for data source management
- Introduced "require user authentication" option for data sources
- Sample questions for data sources is now customizable
- Added to organizations ability to set judge, autogen instructions and code editing as enabled/disabled
- Added a bunch of AGENTS.md files throughout the repo for faster and better coding

## Version 0.0.199 (September 20, 2025)
- Redesigned application onboarding experience
- Implemented automatic instruction suggestions throughout the onboarding process
- Added support to Tableau as a data source
- Some general updates, bug fixes and new tests and sentry removal

## Version 0.0.198 (September 17, 2025)
- Adding login with OpenID Connect (Okta, etc)
- Updating Helm to allow oidc params and auth mode (hybrid, local or sso)
- Touch up to signin/signup screens
- Fix docker image to include client for openssh

## Version 0.0.197 (September 15, 2025)
- Introduced Tableau data source integration: TDS files can now be imported to enhance contextual information for data sources
- Deprecated AI Rules feature at the data source level, consolidating rule management into the centralized instruction system
- Added support for Google Gemini LLM
- Added verbosity to git integration
- Squashed bugs and improved overall usability


## Version 0.0.196 (September 14, 2025)
- Added inline code editor for queries with full execution capabilities: users can now edit query code, preview data results, visualize outputs, and save changes directly within the interface
- Added widget customization controls for labels, titles, and styling
- Rebuilt query/visualization engine for improved scalability
- Improved dashboard layout, reactivness and synchronization to other visualizations
- Enhanced backend architecture and data modeling to support query versioning and multi-visualization relations
- Added ability to test LLM connection before saving as a new provider

## Version 0.0.195 (September 10, 2025)
- Introducing Deep Analysis: Users can now change from Chat mode to Deep Analytics for doing a more comprehensive open ended analytics research to identify root cause, anomalies, opportunities, and more!
- New Prompt box for both home/report page, including customizing LLM per prompt
- Roles with console/monitoring access can now view the full agent loop trace inside the report chat

## Version 0.0.194 (September 9, 2025)
- **Enhanced Dashboards**
  - Improved dashboard creation, allowing more control on styles and the new dashboards look amazing!
  - User can now select themes (default, retro, hacker, or research)
- Added the answer question tool, allowing agent to search across schema, resources, and other pieces context to come up with the answer
- Improvements to Slack bot integration
- Enhancements around: cron visibility, excel files, and sharing

## Version 0.0.193 (September 6, 2025)
- Introduced automatic instruction suggestion system to enhance AI decision-making and performance. The system generates suggestions triggered by:
  - User clarifications regarding terms, facts, or metrics
  - AI successfully resolving data generation code after encountering multiple failures
- All generated suggestions are stored globally and require administrative review and approval before implementation
- Improved main AI agent planner prompt
- Redesigned and expanded the navigation menu, elevating monitoring and instructions to prominent first-class menu items
- Bug fixes and enhancements

## Version 0.0.192
- Fixed file upload functionality within Docker container environment
- Resolved issues with report rerunning capabilities
- Reduced database logging output to only display warnings and errors

## Version 0.0.190 (August 31, 2025)
- Launched Agent 2.0, a comprehensive redesign of the backend agentic architecture
  - Implements ReAct methodology with single-tool execution per planning cycle
  - Enhanced tool registry featuring comprehensive tracking and governance capabilities
  - Added clarify tool for detecting user queries with undefined metrics/measures or ambiguous requirements
  - Improved error handling, tool schema validation, and enhanced reliability throughout agent execution
  - Comprehensive tracking system for agent executions, tool usage, and AI decision-making processes
- Released Context Management 1.0, providing robust and reliable context tracking for both warm and cold AI interactions
  - Complete monitoring of context utilization patterns
  - Streamlined interface for context construction and management during agent operations
- Enhanced compatibility with LLMs that generate prefix/postfix formatting symbols such as json/``` markers
- Redesigned streaming architecture with server-sent events (SSE) implementation for real-time user prompt processing
- Enhanced admin interface for monitoring agent execution flows and tracking user request patterns
- Introduced new analytics visualization in console dashboard displaying metrics for data request creation (user-initiated), AI clarification requests, and additional operational insights
- Added automated testing for the system
- As this change was signifcant, old reports (in version prior 0.0.190) will be set as read-only.
- Introduced customizable branding and AI identity features, allowing organizations to upload their own logos, remove Bow attribution, and personalize their AI assistant's identity


## Version 0.0.189 (August 25, 2025)
- Enhanced table usage analytics with comprehensive success/failure tracking, performance scoring, and intelligent usage pattern recognition
- Implemented automated TableStats model to capture query performance metrics, execution outcomes, and user satisfaction data in real-time
- Advanced code generation now leverages historical success patterns and proven code snippets, significantly improving accuracy and reliability
- Upgraded AI planner with feedback-driven decision algorithms that incorporate table performance scores and usage data for continuous self-improvement
- Added weighted performance/feedback scoring based on user role (admin vs. rest)
- Added tests covering llm providers, azure backend, and console metrics

## Version 0.0.188 (August 23, 2025)
- Enhanced streaming reliability for data models and query results in chat interface
- Strengthened completion termination handling with comprehensive SIGKILL support across all agent lifecycle stages
- Introduced custom base URL configuration for OpenAI provider deployments
- Resolved console metrics and usage data functionality issues
- Corrected admin permissions to allow deletion (not just archival/rejection) of suggested instructions


## Version 0.0.186 (August 19, 2025)
- Enhanced instructions functionality with support for referencing dbt models, tables and other metadata resources
- Updated data source section with improved views of dbt and other metadata resources
- Fixed various bugs and enhanced overall usability

## Version 0.0.181 (August 10, 2025)
- Added data source visibility controls - admins can now set data sources as public or private within organizations and manage granular access permissions through user memberships
- Improved interface and user experience with differentiated views and controls for administrators versus regular users in the data source management area
- Integrated OpenAI's latest GPT-5 language model into the platform
- Updated Docker image to use Ubuntu base with latest security patches
- Updated Python package dependencies to latest stable versions
- Implemented container vulnerability scanning using Trivy in CI/CD pipeline

## Version 0.0.180 (August 6, 2025)
- Enhanced security by updating Dockerfile with latest vulnerability patches
- Integrated Claude 4 Sonnet and Opus language models
- Implemented full support for Vertica database connectivity and querying
- Added capability to incorporate markdown files from git repositories to enhance data sources with contextual information
- Added support for Azure OpenAI and custom model endpoints
- Added support for AWS Redshift database connectivity

## Version 0.0.177 (July 30, 2025)
- Added comprehensive admin console with three main sections: Explore, Diagnose, and Instructions management
- **Explore**: Organization analytics dashboard with real-time metrics, activity charts, performance tracking, table usage analysis, table joins heatmap, failed queries overview, recent instructions, top users, and prompt type analytics
- **Diagnose**: Advanced troubleshooting interface featuring failed query tracking, negative feedback analysis, instructions effectiveness scoring, detailed trace debugging, and issue categorization with actionable insights
- **Instructions**: Centralized instruction management system with search and filtering capabilities, add/edit functionality, data source associations, and user permission controls
- Added LLM Judge system for automated quality assessment - scores instruction effectiveness and context relevance on a 1-5 scale, evaluates AI response quality against user intent, and provides detailed reasoning for continuous system improvement

## Version 0.0.176 (July 26, 2025)
- Added ability to provide detailed feedback messages when submitting negative feedback on AI completions
- Improved reports main page UI

## Version 0.0.175 (July 26, 2025)
- Added ability for users to suggest new instructions and view published instructions
- Added workflow for admins and privileged users to review, approve, or reject suggested instructions
- Enhanced instruction management with data source associations - instructions can now be set globally or scoped to specific data sources
- Added visibility controls allowing admins to hide certain instructions from unprivileged users

## Version 0.0.174 (July 23rd, 2025)
- Filters and pagination for reports
- Reports are now invisible for other users when not published

## Version 0.0.172 (July 17th, 2025)
- Slack integration! Now admins can integrate their Slack organization account and have users converse with bow via slack. Includes user-level authorization, formatting, charts, and tables
- LookML support for git integration indexing
- Download steps data as CSV is now available in UI
- Added *Instructions*: add custom rules and instructions for LLM calls

## Version 0.0.166 (July 13th, 2025)
- Resolved membership invitation handling for closed deployments with OAuth authentication
- Corrected query count calculation in admin dashboard metrics

## Version 0.0.165 (July 7th, 2025)
- Added admin dashboard with usage analytics, query history tracking, and LLM feedback collection
- Implemented secure password recovery workflow with email verification
- Enhanced Kubernetes deployment configuration with expanded Helm chart coverage and options

## Version 0.0.164 (April 24th, 2025)

- Refactored dashboard visualization capabilities:
  - Improved chart rendering performance and responsiveness
  - Enhanced data handling for large datasets
  - Added better error handling and validation
  - Streamlined chart configuration options
- Fixed candlestick chart bug where single stock data was not properly displayed when no ticker field was present
- Added "File" top level navigation item. You can now see all files uploaded in the org
- You can now mention files outside of the report
- Support older version of Excel (97-03)

## Version 0.0.163 (April 21, 2025)

- Added new charts: area, map, treemap, heatmap, candletick, and more
- Better experience for charts to handle zoom, resize and overall better rendering

## Version 0.0.162 (April 16, 2025)

- Added ability to stop AI generation mid-completion with a graceful shutdown option
- Enhanced application startup reliability with automatic database connection retries
- Moved configuration management to server-side, enabling centralized client configuration
- Introduced support for deploying the application on Kubernetes clusters using Helm charts

## Version 0.0.161 (April 14, 2025)

- Added support to OpenAI GPT-4.1 model series

## Version 0.0.160 (April 12, 2025)

- Enhanced AI reasoning with ReAct framework and advanced planning capabilities
- Added upvote/downvote system for users to provide feedback on AI responses
- Added detailed reasoning explanations for AI responses in both UI and backend
- Improved Completion API to support synchronous jobs and return multiple completions
- Added OpenAPI support for global authentication and organization ID handling
- Enhanced organization settings and key management system
- Added visual source tracing in data modeling interface


## Version 0.0.155 (March 30, 2025)

- Added code validation for generated code
- Added safeguards for planner and coder agents
- Enabled code review for user's own code
- Fixed memory bug
- Added reasoning for planner agent
- Added data preview for LLM to achieve ReAct like flow with code generation
- Added organization settings to control AI features (specific agent skills) and additional settings (LLM viewing data, etc)
- Added df summary for tables
- Refactored code execution to be more robust and handle edge cases better

## Version 0.0.154 (March 24, 2025)

- Added advanced logging infrastructure
- Added e2e tests infrastructure and created first e2e test for user onboarding
- Improved ci/cd to run tests before building image

## Version 0.0.153 (March 22, 2025)

- Added support with dbt (via git repo) models and metrics
- Added context building for dbt models
- Added token usage to plan
- Added x-ray view for completions for admin roles

## Version 0.0.152 (March 16, 2025)

- Added AWS Athena integration
- Fixed bug when generating data source items
- Fixed bug when deleting data sources

## Version 0.0.151 (February 25, 2025)

- Added Claude 3.7 Sonnet to LLM models
- Added sync provider with latest models

## Version 0.0.15 (February 24, 2025)

- Added active toggle to data source tables to hide from context
- Fixed bug when generating data source items
- Add top bar to index page when no LLMs are available

## Version 0.0.14 (January 3, 2025)

- Added basic self-hosting support
- Added printing in code gen for better healing
- Improve answering agent and planner agent
- Replaced highcharts with ECharts
- Added intercom
- Various fixes and improvements

## Version 0.0.13 (December 26, 2024)

- Added prompt guidelines 
- Fixed modify, creation of widgets
- Fix proxy in nuxt/fastapi 
- Improved agents: dashboard, data model, chart, and prompt
- Added email validation for signups
- Dockerized the application
- Kubernetesized the application

## Version 0.0.12 (December 13, 2024)

- Added functionality to rerun dashboard steps, including cron support with configurable intervals
- Enabled automated LLM-generated summaries, starters, and reports for connected data sources
- Integrated Google Sign-In for seamless user authentication
- Added support for nginx reverse proxy
- Redesigned the home page for improved usability
- Added `bow-llm`, an abstracted LLM provider to set as the default
- Enhanced error handling with interactive toasts for better feedback
- Improved agent capabilities for code generation with better data source context and refined JSON parsing
- Enabled dynamic modifications to agent plans
- Resolved the "thinking bug"
- Made LLM provider presets uneditable
- Fixed WebSocket functionality in production
- Completed end-to-end tests for completions and data sources

## Version 0.0.11 (December 5, 2024)

- Completed integrations for Presto, Salesforce, and Google Analytics
- Added support to CRUD model providers and LLM models
- Added Claude AI model support
- Implemented data source credential security
- Enhanced agent capabilities:
  - Added clarification questions feature
  - Fixed dashboard layout generation
  - Fixed chart parameter rendering
  - Improved data model modifications
- UI Improvements:
  - Fixed report title updates
  - Resolved copy-paste styling issues in prompt box
  - Completed memberships interface
  - Enhanced mention component
- Infrastructure updates:
  - Added configuration file support
  - Removed Excel special routes
  - Cleaned up Nuxt from git repository
  - Fixed default menu data source association
  - Removed unique organization name requirement

## Version 0.0.10 (November 28, 2024)

- Edge left menu is now scrollable.  
- Fixed logo scaling issue in Edge browser.  
- Added schema browser for data sources.  
- Enabled manual test connection for data sources.  
- Converted data source list in prompts to a dictionary for better position handling.  
- Added Markdown support for completions in both agent and UI.  
- MySQL, BigQuery, Snowflake, MariaDB, and ClickHouse integrations are complete.  
- Initial scaffold for service type data sources
- Fixed `_build_schemas_context` to run only once during agent initialization.  
- Improved data source error messages.  
- Only active data sources are now displayed.  
- Data sources failing test connection are automatically set to inactive.  
- Introduced a service-type architecture for data source handling in code generation.  
- Permissions module completed.  
- Public dashboard completed.
