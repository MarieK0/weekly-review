# Weekly and monthly review

Automatic weekly and monthly reviews of your work, study and sport, written by Claude from data
collected on your Mac. Four parts work together:

| Part | When | What it does |
|---|---|---|
| **Weekly review** | Mondays 8:00 | Claude writes a review of the past week, split into Work, Uni and Private with a comparison with the week before at the end, and saves it in `weeks/`. A chat task can show it to you (see below). |
| **Monthly review** | 1st of the month, 8:00 | The same for the past month in `months/`, with weekly trends, a list of work hours per day and a comparison with the month before. |
| **Chat log** | daily 0:00 | Notes the date and title of your claude.ai chats, so they still count after you delete them. |
| **Activity export** | hourly (cron) | Saves when you were active in Claude Code, before Claude Code deletes old transcripts, and cleans up old data. |

The reviews run as scheduled tasks in the [Claude desktop app](https://claude.ai/download). The
scripts in this repo only collect and prepare the data; Claude reads their output and writes the
review in the language you choose.

## What the reviews contain

Both reviews use the same layout, so they are quick to scan:

| Section | What is in it |
|---|---|
| **Overview** | A few sentences on how the period went and a small table with the active time per area. |
| **💼 Work** | A table per project (active time, days, commits) and what moved forward, summarised from your commit messages. |
| **🎓 Uni** | A table per subject: active time at the computer, hours of your timetable and both together, plus a line on what happened in each subject. |
| **🏠 Private** | Private projects (e.g. a hobby website) and, as **🏃 Sport**, your Strava activities: sessions, time and distance per sport; for the gym (logged with [Hevy](https://www.hevyapp.com/) and synced to Strava) the workouts, working sets and exercises that got heavier. |
| **💡 Notable** | One or two observations, e.g. very long days, a lot of work after midnight, subjects that were left lying, or apps that could not be assigned. |
| **📈 Compared with last week / month** | At the very end: a compact table with last period, this period and the change (▲/▼) for every area, commits and sport, plus two or three sentences on what changed most. |

Only active time counts as hours (see below). The **monthly review** additionally has:

- a table per week, so you can see the trend over the month,
- a **list of work hours per day** with the monthly sum, ready to pass on (e.g. for invoicing),
- gym progress for exercises you did several times (first → last session, heaviest set),
- two or three points for the next month,
- housekeeping: it checks that the chat log task runs and archives its leftover chats.

If a source is not set up or fails (e.g. you are logged out of Strava), the review says so in
one sentence and carries on with the rest. If the previous period has less data (e.g. app
tracking started in the middle of it), the comparison says so instead of reporting it as a
real change.

### Example (made-up numbers)

> # Weekly review · Week 45 · 2–8 Nov
>
> A solid week: most of the time went into MyApp, and you made it to almost every lecture.
>
> | Area | Active | Timetable |
> |---|---:|---:|
> | 💼 Work | 14 h 20 min | |
> | 🎓 Uni | 6 h 40 min | 6 h 20 min |
> | 🏠 Private | 1 h 10 min | |
> | **Total** | **22 h 10 min** | |
>
> ## 💼 Work
>
> | Project | Active | Days | Commits |
> |---|---:|---:|---:|
> | MyApp | 11 h 05 min | 5 | 42 |
> | Website | 3 h 15 min | 2 | 9 |
>
> **MyApp:** the new export is done and the settings page got a cleanup. …
>
> ## 🏠 Private
>
> **Hobby website:** 1 h 10 min on the new character pages.
>
> ### 🏃 Sport
>
> 3 gym sessions (Upper 1, Lower 1, Upper 2, 58 working sets) and 2 runs with 9.1 km.
> Romanian deadlift went from 50 kg × 11 to 60 kg × 6.
>
> …
>
> ## 📈 Compared with last week
>
> | | Last week | This week | Change |
> |---|---:|---:|---:|
> | Active total | 18 h 40 min | 22 h 10 min | +3 h 30 min ▲ |
> | Work | 12 h 05 min | 14 h 20 min | +2 h 15 min ▲ |
> | Uni (timetable) | 7 h 50 min | 6 h 20 min | −1 h 30 min ▼ |
> | Sport sessions | 4 | 5 | +1 ▲ |
>
> More work and one more workout than last week; one lecture was cancelled.

## Getting the review in a chat

The review tasks run on your Mac and only save the review. To have it shown to you, nicely
formatted and with a notification on your phone, set up a scheduled task in a normal Claude chat
that reads the file from your Mac:

- Connect the folder that contains this repo, so the task can read `weeks/` and `months/`.
- Let it run a few times after the review (e.g. hourly on Monday to Wednesday for the weekly
  review, on the 1st to 3rd for the monthly one), in case your Mac is off at 8:00.
- Have it show the newest review once and then leave a marker such as `weeks/.shown-2026-W45`
  (or `months/.shown-2026-10`); `cleanup` deletes old markers.
- Ask it for charts at the top (key numbers, active time per project or subject) and the review
  in your language below, keeping the layout and the comparison at the end.

## Where the data comes from

| Source | What is used | Needed for |
|---|---|---|
| Claude Code | Local transcripts in `~/.claude/projects`: only timestamps and the project folder, no content | Active hours with Claude |
| [ActivityWatch](https://activitywatch.net/) | Active app and window title while you are at the computer | Hours in other apps (IDEs, Office, browser …) |
| ONLYOFFICE | Its list of recently opened documents | Assigning Office time to the right folder |
| claude.ai | Titles of your recent chats (read in the Claude app's built-in browser) | Chats per area |
| Git and GitHub | Your commits in local repos; commits, PRs and issues via the `gh` CLI | What moved forward per project |
| Study folders | Files changed in the period | Uni activity per subject |
| Timetable | Your personal iCal link | Attendance hours per course |
| Strava | Your activities, read in the Claude app's built-in browser during the review | Sport and gym exercises |

Each source is optional; whatever is not set up is skipped.

## How the hours are counted

- **Claude Code:** for each of your messages, the time from Claude's reply up to your message
  (you were reading, testing and writing). If you write while Claude is still working, the time
  since your previous message counts. Gaps over 30 minutes count as a flat 5 minutes. Time in
  which Claude worked on its own does not count, and scheduled runs do not count either.
- **Other apps:** time in the active window while you were at the computer. It is assigned by
  project or file names in the window title, by app rules and by keywords (see `settings.json`).
- **Studying in the browser and elsewhere:** window titles that no rule matches (lecture slides
  from a download, a quiz page, a chat or web search about a course topic) are sorted by Claude
  at the start of each review (`review.py titles` / `save-titles`), and the decisions are kept in
  `titles.csv` so later reviews only ask about new titles. Titles that say nothing by themselves,
  like "ChatGPT", get the course running at that moment or what you did right before or after.
  Leisure (messengers, social media, videos) stays "unassigned" and does not count.
- **Uni totals:** if you are active on your laptop during a timetable course, that time is
  already part of the timetable hours, so the Uni total counts it only once.
- **Areas and projects** come from the folder you work in: your work folder, study folders
  (one subject per subfolder) and private folders, all set in `settings.json`.
- Simultaneous time (two Claude sessions, or Claude Code and an IDE) only counts once.

## Requirements

- macOS, Python 3.9 or newer (the built-in `/usr/bin/python3` is fine), no extra packages.
- The Claude desktop app with scheduled tasks and the built-in browser.
- Optional: ActivityWatch, the [GitHub CLI](https://cli.github.com/) (`gh`, logged in), a Strava
  account, an iCal link to your timetable, ONLYOFFICE.

## Setup

1. Clone the repo to `~/weekly-review` (the task prompts use this path; adjust them if you put
   it somewhere else).
2. Copy `settings.example.json` to `settings.json` and fill it in (see below).
3. Install ActivityWatch, allow it under *Accessibility* in System Settings and enable
   *Start at login* in its menu bar icon:
   ```bash
   brew install --cask activitywatch
   ```
4. Run the export every hour with cron (`crontab -e`):
   ```
   15 * * * * (cd ~/weekly-review && /usr/bin/python3 export.py && /usr/bin/python3 review.py cleanup) > ~/weekly-review/last-run.log 2>&1
   ```
5. In the Claude desktop app, create three scheduled tasks with the prompts from `tasks/`
   (the part below the `---` header) and these schedules:

   | Task | Schedule |
   |---|---|
   | `weekly-review` | Mondays 8:00 |
   | `monthly-review` | 1st of the month, 8:00 |
   | `log-claude-chats` | daily 0:00 |

6. Log in to Strava and claude.ai once in the Claude app's built-in browser.

To check everything, run `python3 review.py data week` and read the output.

## settings.json

| Key | Meaning |
|---|---|
| `review_language` | Language Claude writes the review in, e.g. `"English"`. |
| `area_names` | Names the review uses for the areas Work, Uni, Private and Other. |
| `work_dir` | Folder with everything work related. Files directly in it (invoices etc.) count as Work/Admin. |
| `work_projects_dir` | Folder whose subfolders are your work projects (defaults to `work_dir`). Copies like `MyApp-feature` count as `MyApp`. |
| `uni_dirs` | Study folders. The value is a default subject, or `""` if the subject is the subfolder's name. Term folders like `Semester3` are skipped, so `~/Uni/Semester3/Java` is the subject `Java`. |
| `term_folder_pattern` | Optional regex for those term folders (default `^(semester\|term)\s*\d+$`). |
| `private_dirs` | Folders that count as Private, each as its own project. |
| `repo_subjects` | Folder or repo name prefix → subject, e.g. for GitHub Classroom repos. |
| `apps` | Part of an app name → `"Area/Project"`, used when the window title gives nothing away. |
| `keywords` | `"Area/Project"` (or just `"Area"`) → words in window titles, e.g. browser tabs. |
| `timetable_ical` | iCal link of your timetable. Keep it private; `settings.json` is never committed. |
| `timetable_subjects` | Course code → subject, e.g. `{"DB": "Databases"}` counts the course `07_DB2VO` as Databases. Courses without a match get their own row. |
| `context_default` | Optional `"Area"` or `"Area/Project"` for generic titles like "ChatGPT" when no course or other activity is around them, e.g. `"Uni"`. Without it, that time stays unassigned. |

Everything else falls into Other, named after its top folder in your home directory.

## Files

| File | Purpose |
|---|---|
| `common.py` | Settings and the mapping from folders to area and project. |
| `export.py` | Hourly: writes the spans in which you were active in Claude Code to `claude-activity.csv` and records which documents were opened in ONLYOFFICE (`documents.csv`). |
| `activitywatch.py` | Reads app and window activity from ActivityWatch and assigns it. |
| `review.py` | `data week\|month` prints all data for a period as Markdown (ending with the comparison with the previous period), `strava-query` builds the Strava query, `titles` and `save-titles` sort window titles (decisions in `titles.csv`), `save-chats` and `projects` are used by the chat log task, `cleanup` deletes old data. |
| `tasks/` | Prompts of the three scheduled tasks. |

## Privacy and cleanup

Everything stays on your Mac; the only network requests go to GitHub (via `gh`), your
timetable link and, in the Claude app's browser, Strava and claude.ai. Your data files
(`settings.json`, the CSVs, `weeks/`, `months/`) are in `.gitignore`.

- No message or chat content is stored, only times, folder names, chat titles and the window
  titles Claude sorted (`titles.csv`).
- Strava activities are read during the review and never written to disk.
- The CSVs (including `titles.csv`) and ActivityWatch entries keep only the current and the two
  previous months (enough for the monthly review to compare the past month with the one before).
- The reviews are saved as Markdown in `weeks/` and `months/`. A weekly review is deleted a week
  after its month has a monthly review, a monthly review after 12 months.
- The review tasks archive their own runs (and the monthly review any leftovers), so the
  sidebar stays tidy.

## Caveats

- It reads Claude Code's local transcripts and Strava's internal web endpoint; both can change
  without notice.
- Paths for ONLYOFFICE assume macOS.
