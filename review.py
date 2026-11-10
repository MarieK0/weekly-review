#!/usr/bin/env python3
"""Collects the data for the weekly and monthly review and cleans up.

Usage:
  review.py strava-query week|month [..]   JavaScript that fetches the period's activities in
                                           the logged-in browser on strava.com
  review.py data week [YYYY-Www] [--strava -]   data for one week (default: the last full week);
                                           with --strava - the result of the Strava query is
                                           read from stdin
  review.py data month [YYYY-MM] [--strava -]   the same for a month (default: previous month)
  review.py save-chats < chats.json        add claude.ai chats to the chat log (chats.csv)
  review.py projects                       known work projects, subjects and private folders
                                           (helps the chat log task to assign chats)
  review.py cleanup                        deletes what is no longer needed

Sources: claude-activity.csv (from export.py), ActivityWatch (apps on the Mac, see
activitywatch.py), chats.csv (claude.ai), Git and GitHub, changed files in the study folders,
the timetable (iCal link in settings.json) and Strava (only read during the review, never
stored). Anything that is not set up is skipped.

The review itself is posted in the scheduled task's chat; weeks/ and months/ only hold short
notes that later reviews read as background.

Cleanup:
- ActivityWatch entries before the previous month are deleted (at most 3000 per run).
- weeks/YYYY-Www.md is deleted as soon as months/ has the notes of its month
  (a week belongs to the month of its Sunday).
- months/YYYY-MM.md is deleted after KEEP_MONTHS months.
- chats.csv keeps only the current and the previous month.
Only files named exactly like above are deleted.

Claude Code time is only the time in which you were active (export.py explains how it is
calculated). Parallel sessions do not count twice."""
import csv, json, os, re, subprocess, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.dont_write_bytecode = True  # no __pycache__ folder next to the scripts
from common import (AREAS, BASE, PRIVATE_DIRS, REPO_SUBJECTS, SETTINGS, TERM_FOLDER, UNI_DIRS,
                    WORK_DIR, WORK_PROJECTS, area_order, classify, start_of_previous_month)
from export import BREAK_MIN, CSV, FLAT_MIN
import activitywatch

WEEKS = os.path.join(BASE, "weeks")
MONTHS = os.path.join(BASE, "months")
CHATS = os.path.join(BASE, "chats.csv")
KEEP_MONTHS = 12
# Folders searched for Git repos (not Desktop/Documents, macOS asks extra for those)
GIT_FOLDERS = list(dict.fromkeys(d for d in [WORK_DIR, *UNI_DIRS, *PRIVATE_DIRS] if d))
SKIP_DIRS = {"node_modules", "build", "dist", "out", "target", "__pycache__", "venv", "Testing"}

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEK_FILE = re.compile(r"^(\d{4})-W(\d{2})\.md$")
MONTH_FILE = re.compile(r"^(\d{4})-(\d{2})\.md$")
CHAT_COLUMNS = ["date", "title", "area", "subject"]


# ---------- Helpers ----------

def local(ts):
    """ISO timestamp -> local time without time zone, or None."""
    try:
        t = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def duration(td):
    m = int(round(td.total_seconds() / 60))
    if m == 0:
        return "–"
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def short_day(d):
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTH_NAMES[d.month - 1][:3]}"


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def days_text(days):
    return ", ".join(short_day(d) for d in sorted(days))


def weeks_in(start, end):
    """(ISO week, first day, last day) for every week touching the period, clipped to it."""
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        yield monday.isocalendar()[1], max(monday, start), min(monday + timedelta(days=6), end)
        monday += timedelta(days=7)


def write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


# ---------- Sources ----------

def distribute(spans):
    """spans: [(start, end, key)] -> (total, {key: time}).
    Parallel sessions only count once: time in which several spans run at once is split evenly
    between the keys active at that moment. So the project times add up to exactly the total."""
    points = []
    for s, e, k in spans:
        if e > s:
            points += [(s, 1, k), (e, -1, k)]
    points.sort(key=lambda p: (p[0], p[1]))
    active, share, total, previous = defaultdict(int), defaultdict(timedelta), timedelta(), None
    for t, d, k in points:
        if previous is not None and t > previous:
            now = [x for x, n in active.items() if n > 0]
            if now:
                piece = t - previous
                total += piece
                for x in now:
                    share[x] += piece / len(now)
        active[k] += d
        previous = t
    return total, share


def claude_spans(start_date, end_date):
    """[(start, end, (area, project))] in local time, assigned to the day of the end."""
    rows = []
    if os.path.exists(CSV):
        with open(CSV, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                end = local(r.get("end_utc"))
                if not end or not start_date <= end.date() <= end_date:
                    continue
                start = local(r.get("start_utc")) if r.get("start_utc") else None
                rows.append((start or end - timedelta(minutes=FLAT_MIN), end,
                             (r.get("area") or "Other", r.get("project") or "(unknown)")))
    return rows


def section_working_time(start_date, end_date, is_month):
    z = ["## Working time (active time only: Claude Code + apps on the Mac)", ""]
    spans = [(s, e, (a, p, "claude")) for s, e, (a, p) in claude_spans(start_date, end_date)]
    messages = len(spans)
    notes, unassigned, apps = [], {}, defaultdict(float)
    try:
        aw = activitywatch.spans(start_date, end_date)
        for s, e, (a, p), app in aw["assigned"]:
            spans.append((s, e, (a, p, "apps")))
            apps[(app, a, p)] += (e - s).total_seconds()
        unassigned = aw["unassigned"]
        since = activitywatch.first_entry()
        if since and since > start_date:
            notes.append(f"_Apps on the Mac (ActivityWatch) are only tracked since {short_day(since)}; "
                         "before that only Claude Code counts._")
    except Exception as e:
        notes.append(f"_ActivityWatch not reachable ({type(e).__name__}): Claude Code time only. "
                     "Is ActivityWatch running (icon in the menu bar)?_")
    if not spans:
        return z + notes + ["No tracked working time in this period."]

    per_day = defaultdict(list)
    for s, e, k in spans:
        per_day[e.date() if e.time() != datetime.min.time() else (e - timedelta(seconds=1)).date()].append((s, e, k))
    total, proj, source, proj_days, days = timedelta(), defaultdict(timedelta), defaultdict(timedelta), defaultdict(set), {}
    for d, sp in sorted(per_day.items()):
        t, share = distribute(sp)
        total += t
        by_area = defaultdict(timedelta)
        for (a, p, q), time_ in share.items():
            proj[(a, p)] += time_
            source[(a, p, q)] += time_
            proj_days[(a, p)].add(d)
            by_area[a] += time_
        day_start = datetime.combine(d, datetime.min.time())
        days[d] = (t, by_area, max(day_start, min(s for s, _, _ in sp)), max(e for _, e, _ in sp))
    areas = sorted({a for a, _ in proj}, key=area_order)

    z += [f"Active time total: **{duration(total)}** on {len(days)} days "
          f"({messages} messages/answers from you in Claude Code)", ""]
    z += ["| Area / project | Active total | of which Claude Code | of which apps | Days |",
          "|---|---:|---:|---:|---:|"]
    for a in areas:
        ps = sorted(((p, t) for (aa, p), t in proj.items() if aa == a), key=lambda pt: -pt[1])
        subtotal = lambda q: sum((source[(a, p, q)] for p, _ in ps), timedelta())
        a_days = set().union(*(proj_days[(a, p)] for p, _ in ps))
        z.append(f"| **{a}** | **{duration(sum((t for _, t in ps), timedelta()))}** | "
                 f"**{duration(subtotal('claude'))}** | **{duration(subtotal('apps'))}** | **{len(a_days)}** |")
        for p, t in ps:
            z.append(f"| – {cell(p)} | {duration(t)} | {duration(source[(a, p, 'claude')])} | "
                     f"{duration(source[(a, p, 'apps')])} | {len(proj_days[(a, p)])} |")
    z.append(f"| **Total** | **{duration(total)}** | | | **{len(days)}** |")

    header = " | ".join(areas)
    right = "---:|" * (len(areas) + 1)
    if is_month:
        z += ["", f"| Week | {header} | Total |", f"|---|{right}"]
        for week, a, e in weeks_in(start_date, end_date):
            wd = [v for d, v in days.items() if a <= d <= e]
            z.append(f"| W{week} ({short_day(a)} – {short_day(e)}) | "
                     + " | ".join(duration(sum((v[1][ar] for v in wd), timedelta())) for ar in areas)
                     + f" | {duration(sum((v[0] for v in wd), timedelta()))} |")
    z += ["", f"| Day | {header} | Total | From–To |", f"|---|{right}---|"]
    for d, (t, by_area, a, e) in sorted(days.items()):
        z.append(f"| {short_day(d)} | " + " | ".join(duration(by_area[ar]) for ar in areas)
                 + f" | {duration(t)} | {a:%H:%M}–{e:%H:%M} |")

    if apps:
        z += ["", "| App | Time | Assigned to |", "|---|---:|---|"]
        for (app, a, p), sec in sorted(apps.items(), key=lambda kv: -kv[1])[:15]:
            z.append(f"| {cell(app)} | {duration(timedelta(seconds=sec))} | {a} / {cell(p)} |")
    if unassigned:
        rest = sorted(unassigned.items(), key=lambda kv: -kv[1])
        subtotal = timedelta(seconds=sum(unassigned.values()))
        z += ["", f"Unassigned (does not count as working time): {duration(subtotal)} – "
              + ", ".join(f"{cell(a)} {duration(timedelta(seconds=s))}" for a, s in rest[:8] if s >= 60)]
    z += [""] + notes + [
        f"_Active time: Claude Code from Claude's reply to your next message (reading, testing, writing), "
        f"gaps over {BREAK_MIN} min count as a flat {FLAT_MIN} min; apps = time in the active window while you "
        "were at the computer, assigned by app, project or file name. Claude windows only count via "
        "Claude Code, simultaneous time only counts once._"]
    return z


def section_chats(start_date, end_date):
    z = ["## claude.ai chats (Chat tab and website)", ""]
    if not os.path.exists(CHATS):
        return z + ["No chats logged yet (the daily chat log has not run yet)."]
    chats = defaultdict(set)  # (area, subject, title) -> days
    with open(CHATS, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if start_date <= d <= end_date:
                chats[(r.get("area") or "Other", r.get("subject") or "", r.get("title") or "")].add(d)
    if not chats:
        return z + ["No claude.ai chats in this period."]
    z += [f"{len(chats)} chats with activity.", "", "| Area | Subject / topic | Chat | Days |", "|---|---|---|---|"]
    for (a, s, t), ds in sorted(chats.items(), key=lambda kv: (area_order(kv[0][0]), kv[0][1], min(kv[1]))):
        z.append(f"| {a} | {cell(s)} | {cell(t)} | {days_text(ds)} |")
    return z


def git_repos():
    repos = []
    for root in GIT_FOLDERS:
        for folder, dirs, _ in os.walk(root):
            if os.path.isdir(os.path.join(folder, ".git")):
                repos.append(folder)
                dirs[:] = []
                continue
            if folder[len(root):].count(os.sep) >= 3:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith((".", "cmake-build")) and d not in SKIP_DIRS]
    return repos


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        message = (r.stderr or r.stdout).strip().splitlines()
        raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: {message[0] if message else 'error'}")
    return json.loads(r.stdout or "[]")


def section_git(start_date, end_date):
    z = ["## Git and GitHub", ""]
    authors = []
    for key in ("user.email", "user.name"):
        r = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True)
        if r.stdout.strip():
            authors.append(re.escape(r.stdout.strip()))
    commits = {}  # sha -> (area, project, time, subject line)
    since, until = f"{start_date.isoformat()} 00:00:00", f"{end_date.isoformat()} 23:59:59"
    repos = git_repos()
    by_name = {os.path.basename(r): classify(r) for r in repos}
    for repo in repos:
        a, p = classify(repo)
        args = ["git", "-C", repo, "log", "--all", "--no-merges", f"--since={since}", f"--until={until}",
                "--format=%H%x09%aI%x09%s"] + [f"--author={x}" for x in authors]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        for line in r.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[0] not in commits:
                commits[parts[0]] = (a, p, local(parts[1]), parts[2])

    # On GitHub: PRs, issues and commits from repos that are not cloned here
    gh_note, prs, issues = None, [], []
    try:
        login = gh_json("api", "user")["login"]
        period = f"{start_date.isoformat()}..{end_date.isoformat()}"
        fields = "repository,title,state,url,createdAt,closedAt"
        seen = set()
        for filt in (f"--created={period}", f"--merged-at={period}"):
            for pr in gh_json("search", "prs", "--author", login, filt, "--json", fields, "--limit", "100"):
                if pr["url"] not in seen:
                    seen.add(pr["url"]); prs.append(pr)
        issues = gh_json("search", "issues", "--author", login, f"--created={period}", "--json", fields, "--limit", "100")
        for c in gh_json("search", "commits", "--author", login, f"--author-date={period}",
                         "--json", "sha,repository,commit", "--limit", "100"):
            info = c.get("commit") or {}
            subject = (info.get("message") or "").splitlines()[0] if info.get("message") else ""
            if c["sha"] not in commits and not subject.startswith("Merge "):
                when = local(((info.get("author") or {}).get("date")) or "")
                repo = (c.get("repository") or {}).get("name") or "?"
                a, p = by_name.get(repo) or next(
                    (("Uni", s) for prefix, s in REPO_SUBJECTS.items() if repo.startswith(prefix)),
                    ("GitHub", repo))
                commits[c["sha"]] = (a, p, when, subject)
    except Exception as e:  # GitHub not reachable: show local commits only
        gh_note = f"_GitHub not queried: {e}_"

    if not commits and not prs and not issues:
        z.append("No commits, PRs or issues in this period.")
    if commits:
        groups = defaultdict(list)
        for a, p, t, s in commits.values():
            groups[(a, p)].append((t or datetime.min, s))
        z += [f"{len(commits)} commits.", "", "| Area | Repo | Commits | Days | Examples |", "|---|---|---:|---|---|"]
        for (a, p), cs in sorted(groups.items(), key=lambda kv: (area_order(kv[0][0]), -len(kv[1]))):
            cs.sort(reverse=True)
            ds = {t.date() for t, _ in cs if t != datetime.min}
            examples = "; ".join(cell(s)[:70] for _, s in cs[:8])
            z.append(f"| {a} | {cell(p)} | {len(cs)} | {days_text(ds)} | {examples} |")
    if prs:
        z += ["", "Pull requests:"]
        for pr in prs:
            z.append(f"- {pr['repository'].get('name', '?')}: {pr['title']} ({pr['state']})")
    if issues:
        z += ["", "Issues:"]
        for i in issues:
            z.append(f"- {i['repository'].get('name', '?')}: {i['title']} ({i['state']})")
    if gh_note:
        z += ["", gh_note]
    return z


def section_uni_files(start_date, end_date):
    z = ["## Uni folders: changed files", ""]
    subjects = defaultdict(list)  # subject -> [(time, name)]
    for root in UNI_DIRS:
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "cmake-build")) and d not in SKIP_DIRS]
            for f in files:
                if f.startswith(".") or f.endswith((".o", ".class", ".pyc")):
                    continue
                try:
                    t = datetime.fromtimestamp(os.path.getmtime(os.path.join(folder, f)))
                except OSError:
                    continue
                if start_date <= t.date() <= end_date:
                    subjects[classify(folder)[1]].append((t, f))
    if not subjects:
        return z + ["No files changed in the study folders." if UNI_DIRS else "No study folders in settings.json."]
    z += ["| Subject | Files | Days | Last changed |", "|---|---:|---|---|"]
    for subject, fs in sorted(subjects.items(), key=lambda kv: -len(kv[1])):
        fs.sort(reverse=True)
        names = ", ".join(cell(n) for n in dict.fromkeys(n for _, n in fs[:6]))
        z.append(f"| {cell(subject)} | {len(fs)} | {days_text({t.date() for t, _ in fs})} | {names} |")
    return z


def ical_events(text):
    """Events (start, end, title) from an iCal file; weekly series are expanded."""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)

    def parse(value, param):
        if "VALUE=DATE" in param and "DATE-TIME" not in param:
            return None
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S")

    events, ev = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            ev = {"exdate": set()}
        elif line == "END:VEVENT" and ev is not None:
            s, e = ev.get("start"), ev.get("end")
            if s and e and ev.get("status") != "CANCELLED":
                rule = ev.get("rrule")
                if rule and rule.get("FREQ") == "WEEKLY":
                    step = timedelta(weeks=int(rule.get("INTERVAL", "1")))
                    count = int(rule["COUNT"]) if "COUNT" in rule else 400
                    series_end = parse(rule["UNTIL"], "") if "UNTIL" in rule else s + timedelta(days=400)
                    for i in range(count):
                        a = s + i * step
                        if a > series_end:
                            break
                        if a not in ev["exdate"]:
                            events.append((a, a + (e - s), ev.get("title", "")))
                else:
                    events.append((s, e, ev.get("title", "")))
            ev = None
        elif ev is not None and ":" in line:
            head, value = line.split(":", 1)
            name, _, param = head.partition(";")
            try:
                if name == "DTSTART":
                    ev["start"] = parse(value, param)
                elif name == "DTEND":
                    ev["end"] = parse(value, param)
                elif name == "SUMMARY":
                    ev["title"] = value.replace("\\,", ",").replace("\\;", ";").strip()
                elif name == "STATUS":
                    ev["status"] = value.strip()
                elif name == "RRULE":
                    ev["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
                elif name == "EXDATE":
                    for v in value.split(","):
                        t = parse(v, param)
                        if t:
                            ev["exdate"].add(t)
            except ValueError:
                pass
    return events


def section_timetable(start_date, end_date):
    z = ["## Uni hours according to the timetable", ""]
    url = SETTINGS.get("timetable_ical")
    if not url:
        return z + ["Not set up yet (no timetable_ical link in settings.json)."]
    text, error = None, None
    for attempt in dict.fromkeys([url, url.replace("https://", "http://", 1)]):
        try:
            with urllib.request.urlopen(attempt, timeout=30) as r:
                text = r.read().decode("utf-8", errors="replace")
            break
        except Exception as e:
            error = e
    if text is None:
        return z + [f"_Timetable not available: {error}_"]
    courses = defaultdict(lambda: [timedelta(), set()])
    total = timedelta()
    for s, e, title in ical_events(text):
        if start_date <= s.date() <= end_date:
            courses[title][0] += e - s
            courses[title][1].add(s.date())
            total += e - s
    if not courses:
        return z + ["No courses in this period."]
    per_subject = defaultdict(timedelta)
    z += [f"Scheduled total: **{duration(total)}**", "", "| Course | Subject | Hours | Days |", "|---|---|---:|---|"]
    for title, (t, ds) in sorted(courses.items(), key=lambda kv: -kv[1][0]):
        subject = course_subject(title)
        per_subject[subject or "(not mapped)"] += t
        z.append(f"| {cell(title)} | {cell(subject or '–')} | {duration(t)} | {days_text(ds)} |")
    z += ["", "Per subject: " + ", ".join(f"{s} {duration(t)}" for s, t in
                                       sorted(per_subject.items(), key=lambda st: -st[1]))]
    return z


def course_subject(title):
    """Subject of a timetable course from settings.json "timetable_subjects" (course code -> subject),
    e.g. {"DES": "Databases"} maps "09_DES3VO" to Databases. None if no code matches."""
    for code, subject in (SETTINGS.get("timetable_subjects") or {}).items():
        if re.search(r"(?<![A-Za-z])" + re.escape(code), title):
            return subject
    return None


STRAVA_JS = """// Strava activities __FROM__ to __TO__ - pass the result to review.py unchanged
const from = "__FROM__", to = "__TO__", out = [];
const day = m => new Date(m.start_date_local_raw * 1000).toISOString().slice(0, 10);
for (let page = 1; page <= 30; page++) {
  const r = await fetch(`/athlete/training_activities?new_activity_only=false&page=${page}`,
    {headers: {Accept: "application/json", "X-Requested-With": "XMLHttpRequest"}});
  if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) {
    out.push({error: `Strava not logged in or not reachable (HTTP ${r.status})`});
    break;
  }
  const j = await r.json(), ms = j.models || [];
  for (const m of ms) {
    const d = day(m);
    if (d < from || d > to) continue;
    const exercises = [];  // Hevy description: [exercise, heaviest kg, reps with it, working sets]
    for (const block of (m.description || "").split(/\\n\\s*\\n/)) {
      const [name, ...rows] = block.trim().split("\\n");
      const sets = rows.filter(s => /^\\s*Set \\d+:/.test(s) && !/warm/i.test(s));
      if (!sets.length) continue;
      let best = null;
      for (const s of sets) {
        const x = s.match(/:\\s*([\\d.]+)\\s*kg\\s*x\\s*(\\d+)/);
        if (x && (!best || +x[1] > best[0] || (+x[1] === best[0] && +x[2] > best[1]))) best = [+x[1], +x[2]];
      }
      exercises.push([name.trim(), best ? best[0] : null, best ? best[1] : null, sets.length]);
    }
    out.push({date: d, sport: m.activity_type_display_name, title: m.name, min: Math.round(m.elapsed_time_raw / 60),
              km: Math.round((m.distance_raw || 0) / 10) / 100, elev_m: Math.round(m.elevation_gain_raw || 0), exercises});
  }
  if (!ms.length || page * (j.perPage || 20) >= j.total || day(ms[ms.length - 1]) < from) break;
}
out
"""


def number(x):
    """2.5 -> "2.5", 25.0 -> "25" (for kg)."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def km_text(km):
    return f"{km + 1e-9:.1f} km" if km else "–"


def set_text(kg, reps):
    return f"{number(kg)} kg × {reps}" if kg is not None else "no weight"


def read_strava(source):
    """Result of the Strava query (JSON list, or a JSON list wrapped in a JSON string)."""
    data = json.load(source)
    if isinstance(data, str):
        data = json.loads(data)
    return data if isinstance(data, list) else []


def section_sport(start_date, end_date, strava):
    z = ["## Sport (Strava)", ""]
    if strava is None:
        return z + ["Not queried (no Strava data passed in)."]
    errors = [a["error"] for a in strava if isinstance(a, dict) and a.get("error")]
    acts = []
    for a in strava:
        try:
            d = date.fromisoformat(str(a["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if start_date <= d <= end_date:
            acts.append((d, a))
    acts.sort(key=lambda da: da[0])
    if errors:
        z += [f"_{errors[0]}_", ""]
    if not acts:
        return z + ["No activities in this period." if not errors else "So no sport data."]

    per_sport = defaultdict(lambda: [0, 0, 0.0])  # sessions, minutes, km
    for _, a in acts:
        p = per_sport[a.get("sport") or "?"]
        p[0] += 1
        p[1] += int(a.get("min") or 0)
        p[2] += float(a.get("km") or 0)
    z += ["| Sport | Sessions | Time | Distance |", "|---|---:|---:|---:|"]
    for sport, (n, minutes, km) in sorted(per_sport.items(), key=lambda kv: -kv[1][1]):
        z.append(f"| {cell(sport)} | {n} | {duration(timedelta(minutes=minutes))} | {km_text(km)} |")
    n_total = sum(p[0] for p in per_sport.values())
    min_total = sum(p[1] for p in per_sport.values())
    km_total = sum(p[2] for p in per_sport.values())
    z.append(f"| **Total** | **{n_total}** | **{duration(timedelta(minutes=min_total))}** | "
             f"**{km_text(km_total)}** |")
    z += ["", f"Sport on {len({d for d, _ in acts})} of {(end_date - start_date).days + 1} days. "
          "Strava does not count steps.", ""]

    if (end_date - start_date).days > 7:
        z += ["| Week | Sessions | Time |", "|---|---:|---:|"]
        for week, w_start, w_end in weeks_in(start_date, end_date):
            w = [a for d, a in acts if w_start <= d <= w_end]
            z.append(f"| W{week} ({short_day(w_start)} – {short_day(w_end)}) | {len(w)} | "
                     f"{duration(timedelta(minutes=sum(int(a.get('min') or 0) for a in w)))} |")
        z.append("")

    z += ["| Day | Sport | Title | Duration | Distance | Exercises |", "|---|---|---|---:|---:|---|"]
    exercises = defaultdict(list)  # exercise -> [(date, kg, reps, sets)]
    for d, a in acts:
        ex = [e for e in (a.get("exercises") or []) if isinstance(e, list) and len(e) == 4]
        for name, kg, reps, sets in ex:
            exercises[name].append((d, kg, reps, int(sets or 0)))
        ex_text = f"{len(ex)} exercises, {sum(int(e[3] or 0) for e in ex)} working sets" if ex else ""
        km = float(a.get("km") or 0)
        z.append(f"| {short_day(d)} | {cell(a.get('sport', ''))} | {cell(a.get('title', ''))} | "
                 f"{duration(timedelta(minutes=int(a.get('min') or 0)))} | {km_text(km)} | {ex_text} |")

    if exercises:
        z += ["", "### Gym exercises (from Hevy, without warm-up sets)", "",
              "| Exercise | Sessions | Sets | Heaviest set | First → last session |", "|---|---:|---:|---|---|"]
        for name, entries in sorted(exercises.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            weighted = [e for e in entries if e[1] is not None]
            best = max(weighted, key=lambda e: (e[1], e[2])) if weighted else None
            progress = (f"{set_text(entries[0][1], entries[0][2])} → {set_text(entries[-1][1], entries[-1][2])}"
                        if len(entries) > 1 else "")
            z.append(f"| {cell(name)} | {len(entries)} | {sum(e[3] for e in entries)} | "
                     f"{set_text(best[1], best[2]) + ' (' + short_day(best[0]) + ')' if best else '–'} | {progress} |")
    return z


# ---------- Commands ----------

def period(kind, value, today):
    if kind == "week":
        if value:
            m = re.fullmatch(r"(\d{4})-W(\d{1,2})", value)
            if not m:
                sys.exit("Please give the week as YYYY-Www, e.g. 2026-W45")
            monday = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        else:
            monday = today - timedelta(days=today.weekday() + 7)
        sunday = monday + timedelta(days=6)
        year, week, _ = monday.isocalendar()
        title = (f"Week {week}/{year}: {short_day(monday)}{' ' + str(monday.year) if monday.year != sunday.year else ''}"
                 f" – {short_day(sunday)} {sunday.year}")
        return monday, sunday, title, os.path.join(WEEKS, f"{year}-W{week:02d}.md")
    if value:
        m = re.fullmatch(r"(\d{4})-(\d{2})", value)
        if not m:
            sys.exit("Please give the month as YYYY-MM, e.g. 2026-10")
        year, month = int(m[1]), int(m[2])
    else:
        year, month = months_back(today.year, today.month, 1)
    first = date(year, month, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first, last, f"Month {MONTH_NAMES[month - 1]} {year}", os.path.join(MONTHS, f"{year}-{month:02d}.md")


def data(kind, value=None, today=None, strava=None):
    start_date, end_date, title, path = period(kind, value, today or date.today())
    names = {**{a: a for a in AREAS}, **(SETTINGS.get("area_names") or {})}
    z = [f"# Data for the review · {title}", "",
         f"Review language: {SETTINGS.get('review_language') or 'English'}. Area names to use: "
         + ", ".join(f"{a} = {names[a]}" for a in AREAS) + ".",
         f"Save the review notes as: `{os.path.relpath(path, BASE)}`", ""]
    for section in (lambda: section_working_time(start_date, end_date, kind == "month"),
                    lambda: section_chats(start_date, end_date),
                    lambda: section_git(start_date, end_date),
                    lambda: section_uni_files(start_date, end_date),
                    lambda: section_timetable(start_date, end_date),
                    lambda: section_sport(start_date, end_date, strava)):
        try:
            z += section()
        except Exception as e:
            z += [f"_Error in this section: {type(e).__name__}: {e}_"]
        z.append("")
    print("\n".join(z))


def save_chats(entries):
    """Adds {date, title, area, subject} to chats.csv; each date/title pair only once."""
    known = {}
    if os.path.exists(CHATS):
        with open(CHATS, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                known[(r["date"], r["title"])] = (r.get("area", ""), r.get("subject", ""))
    new = 0
    for e in entries:
        try:
            d = date.fromisoformat(str(e["date"])).isoformat()
        except (KeyError, ValueError):
            continue
        title = str(e.get("title", "")).strip()
        if not title:
            continue
        new += (d, title) not in known
        known[(d, title)] = (str(e.get("area") or "Other"), str(e.get("subject") or ""))
    write_csv(CHATS, CHAT_COLUMNS, sorted((d, t, a, s) for (d, t), (a, s) in known.items()))
    print(f"{new} new entries, {len(known)} in total in chats.csv")


def projects(today=None):
    """Prints the known work projects, subjects and private folders, most recently changed first."""
    today = today or date.today()
    found = {}  # (area, project) -> latest change

    def note(path):
        a, p = classify(path)
        try:
            changed = datetime.fromtimestamp(os.path.getmtime(path)).date()
        except OSError:
            return
        if a != "Other" and not p.startswith("("):
            found[(a, p)] = max(changed, found.get((a, p), date.min))

    def subfolders(folder):
        try:
            return [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                    if not n.startswith(".") and os.path.isdir(os.path.join(folder, n))]
        except OSError:
            return []

    for root in UNI_DIRS:
        for sub in subfolders(root):
            for s in subfolders(sub) if TERM_FOLDER.match(os.path.basename(sub)) else [sub]:
                note(s)
    for sub in subfolders(WORK_PROJECTS) if WORK_PROJECTS else []:
        note(sub)
    for folder in PRIVATE_DIRS:
        note(folder)
    print("Known areas and projects/subjects (folder last changed):")
    for a in AREAS:
        items = sorted(((p, d) for (aa, p), d in found.items() if aa == a), key=lambda pd: (-pd[1].toordinal(), pd[0]))
        if items:
            print(f"- {a}: " + ", ".join(f"{p} ({d.isoformat()})" for p, d in items))
    timetable = list(dict.fromkeys((SETTINGS.get("timetable_subjects") or {}).values()))
    if timetable:
        print("- Uni subjects on the current timetable: " + ", ".join(timetable))
    print("- Other: anything that fits nowhere else")


def months_back(year, month, n):
    i = year * 12 + month - 1 - n
    return i // 12, i % 12 + 1


def cleanup(today=None):
    today = today or date.today()
    previous_month = months_back(today.year, today.month, 1)
    cutoff = start_of_previous_month(today)
    if os.path.isdir(WEEKS):
        for name in os.listdir(WEEKS):
            m = WEEK_FILE.match(name)
            if not m:
                continue
            try:
                sunday = date.fromisocalendar(int(m[1]), int(m[2]), 7)
            except ValueError:
                continue
            month_done = os.path.exists(os.path.join(MONTHS, f"{sunday.year}-{sunday.month:02d}.md"))
            if month_done or (sunday.year, sunday.month) < previous_month:
                os.remove(os.path.join(WEEKS, name))
    if os.path.isdir(MONTHS):
        oldest = months_back(today.year, today.month, KEEP_MONTHS)
        for name in os.listdir(MONTHS):
            m = MONTH_FILE.match(name)
            if m and (int(m[1]), int(m[2])) < oldest:
                os.remove(os.path.join(MONTHS, name))
    if os.path.exists(CHATS):
        with open(CHATS, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        keep = [r for r in rows if r.get("date", "") >= cutoff.isoformat()]
        if len(keep) < len(rows):
            write_csv(CHATS, CHAT_COLUMNS, [[r.get(c, "") for c in CHAT_COLUMNS] for r in keep])
    activitywatch.cleanup(cutoff)


if __name__ == "__main__":
    a = sys.argv[1:]
    strava = None
    if "--strava" in a:
        i = a.index("--strava")
        source = a[i + 1] if len(a) > i + 1 else "-"
        del a[i:i + 2]
        if source == "-":
            strava = read_strava(sys.stdin)
        else:
            with open(source, encoding="utf-8") as fh:
                strava = read_strava(fh)
    if a[:1] == ["data"] and len(a) >= 2 and a[1] in ("week", "month"):
        data(a[1], a[2] if len(a) > 2 else None, strava=strava)
    elif a[:1] == ["strava-query"] and len(a) >= 2 and a[1] in ("week", "month"):
        start_date, end_date, _, _ = period(a[1], a[2] if len(a) > 2 else None, date.today())
        print(STRAVA_JS.replace("__FROM__", start_date.isoformat()).replace("__TO__", end_date.isoformat()))
    elif a == ["save-chats"]:
        save_chats(json.load(sys.stdin))
    elif a == ["projects"]:
        projects()
    elif a == ["cleanup"]:
        cleanup()
    else:
        sys.exit(__doc__)
