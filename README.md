# Weekly and monthly review

Automatic weekly and monthly reviews of your work, study and sport, written by Claude from data
collected on your Mac. Four parts work together:

| Part | When | What it does |
|---|---|---|
| **Weekly review** | Mondays 8:00 | Claude posts a review of the past week in the chat of a scheduled task. |
| **Monthly review** | 1st of the month, 8:00 | Claude posts a review of the past month, with weekly trends, a list of work hours per day and a comparison with the month before. |
| **Chat log** | daily 0:00 | Notes the date and title of your claude.ai chats, so they still count after you delete them. |
| **Activity export** | hourly (cron) | Saves when you were active in Claude Code, before Claude Code deletes old transcripts, and cleans up old data. |

The reviews run as scheduled tasks in the [Claude desktop app](https://claude.ai/download). The
scripts in this repo only collect and prepare the data; Claude reads their output and writes the
review in the language you choose.

## What the reviews contain

**Weekly review** (about one screen):

- **Overview** of the week in a few sentences.
- **Working hours:** a table grouped by area (Work, Uni, Private, Other) with a subtotal per area
  and a row per project or subject. Only active time counts (see below). For Uni, a second column
  shows the hours of your timetable, plus a "Uni total" of both.
- **Uni:** per subject, what happened: Claude sessions, chats, commits, changed files. Subjects
  without any activity are named too.
- **Work:** per project, what moved forward, summarised from your commit messages.
- **Private:** a short summary.
- **Sport:** sessions per sport with time and distance from Strava. For the gym (logged with
  [Hevy](https://www.hevyapp.com/) and synced to Strava): which workouts, how many working sets
  and which exercises got heavier.
- **Notable:** one or two observations, e.g. very long days, a lot of work after midnight,
  subjects that were left lying, or a week without sport.

**Monthly review** (one to two screens), the same areas for the whole month plus:

- a table per week, so you can see the trend over the month,
- a **list of work hours per day** with the monthly sum, ready to pass on (e.g. for invoicing),
- gym progress for exercises you did several times (first → last session, heaviest set),
- a comparison with the previous month and two or three points for the next one,
- housekeeping: it archives old review chats in the Claude app so the sidebar stays tidy.

If a source is not set up or fails (e.g. you are logged out of Strava), the review says so in
one sentence and carries on with the rest.

### Example (made-up numbers)

> **Working hours**
>
> | Area / project | Active | Timetable |
> |---|---:|---:|
> | **Work** | **14 h 20 min** | |
> | – MyApp | 11 h 05 min | |
> | – Website | 3 h 15 min | |
> | **Uni** | **6 h 40 min** | **6 h 20 min** |
> | – Databases | 4 h 10 min | 3 h 10 min |
> | – Java | 2 h 30 min | 3 h 10 min |
> | **Total** | **21 h 00 min** | |
>
> 3 claude.ai chats: 2 Work, 1 Uni.
>
> **Sport:** 3 gym sessions (Upper 1, Lower 1, Upper 2, 58 working sets), 2 runs with 9.1 km.
> Romanian deadlift went from 50 kg × 11 to 60 kg × 6.

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
  Anything that matches nothing is listed as "unassigned", with the biggest apps named, so you
  can add a rule; it does not count as working time.
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
| `timetable_subjects` | Course code → subject, e.g. `{"DES": "Databases"}` counts the course `09_DES3VO` as Databases. Courses without a match get their own row. |

Everything else falls into Other, named after its top folder in your home directory.

## Files

| File | Purpose |
|---|---|
| `common.py` | Settings and the mapping from folders to area and project. |
| `export.py` | Hourly: writes the spans in which you were active in Claude Code to `claude-activity.csv` and records which documents were opened in ONLYOFFICE (`documents.csv`). |
| `activitywatch.py` | Reads app and window activity from ActivityWatch and assigns it. |
| `review.py` | `data week\|month` prints all data for a period as Markdown, `strava-query` builds the Strava query, `save-chats` and `projects` are used by the chat log task, `cleanup` deletes old data. |
| `tasks/` | Prompts of the three scheduled tasks. |

## Privacy and cleanup

Everything stays on your Mac; the only network requests go to GitHub (via `gh`), your
timetable link and, in the Claude app's browser, Strava and claude.ai. Your data files
(`settings.json`, the CSVs, `weeks/`, `months/`) are in `.gitignore`.

- No message or chat content is stored, only times, folder names and chat titles.
- Strava activities are read during the review and never written to disk.
- The CSVs and ActivityWatch entries keep only the current and the previous month.
- The review itself is posted in the task's chat. `weeks/` and `months/` only hold short notes
  that later reviews read as background: weekly notes are deleted once their month has monthly
  notes, monthly notes after 12 months.
- The monthly review archives old review chats in the Claude app.

## Caveats

- It reads Claude Code's local transcripts and Strava's internal web endpoint; both can change
  without notice.
- Paths for ONLYOFFICE assume macOS.
