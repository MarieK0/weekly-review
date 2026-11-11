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
  review.py titles week|month [..]          window titles of the period that no rule could
                                           assign, for Claude to sort (e.g. slides, ChatGPT)
  review.py save-titles < titles.json      remembers those decisions in titles.csv
  review.py cleanup                        deletes what is no longer needed

Sources: claude-activity.csv (from export.py), ActivityWatch (apps on the Mac, see
activitywatch.py), chats.csv (claude.ai), Git and GitHub, changed files in the study folders,
the timetable (iCal link in settings.json) and Strava (only read during the review, never
stored). Anything that is not set up is skipped.

The review is saved in weeks/ and months/; a chat task can show it from there.

Cleanup:
- ActivityWatch entries older than the two previous months are deleted (at most 3000 per run).
- weeks/YYYY-Www.md is deleted a week after months/ has the review of its month
  (a week belongs to the month of its Sunday).
- months/YYYY-MM.md is deleted after KEEP_MONTHS months.
- chats.csv keeps only the current and the two previous months (enough to compare the
  monthly review with the month before); titles.csv the decisions made in that time.
Only files named exactly like above are deleted.

Claude Code time is only the time in which you were active (export.py explains how it is
calculated). Parallel sessions do not count twice."""
import csv, json, os, re, subprocess, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.dont_write_bytecode = True  # no __pycache__ folder next to the scripts
from common import (AREAS, BASE, PRIVATE_DIRS, REPO_SUBJECTS, SETTINGS, TERM_FOLDER, UNI_DIRS,
                    WORK_DIR, WORK_PROJECTS, area_order, classify, keep_from, target)
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
# A window title that says nothing by itself (decision "context", e.g. "ChatGPT") takes the
# course running at that moment or the activity at most this far before or after it
CONTEXT_MIN = 20


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


def merge(intervals):
    """Overlapping (start, end) intervals merged, sorted."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def course_times(start_date, end_date):
    """[(start, end, subject or None)] of the timetable courses in the period ([] without timetable)."""
    problem, _, _ = timetable(start_date, end_date)
    if problem:
        return []
    return [(s, e, course_subject(t)) for s, e, t in _timetable["events"] if start_date <= s.date() <= end_date]


def nearest(s, e, known):
    """Target of the activity in known [(start, end, target)] closest to the span s..e,
    if it is at most CONTEXT_MIN away, else None."""
    best, best_gap = None, timedelta(minutes=CONTEXT_MIN)
    for ks, ke, k in known:
        gap = max(ks - e, s - ke, timedelta())
        if gap <= best_gap:
            best, best_gap = k, gap
    return best


def context_target(s, e, courses, known):
    """Where time with a generic window title goes: the course running at that moment, else the
    nearest activity (see nearest), else settings.json "context_default", else None."""
    for cs, ce, subject in courses:
        if cs < e and s < ce:
            return ("Uni", subject or "(general)")
    t = nearest(s, e, known)
    if t:
        return t
    default = SETTINGS.get("context_default")
    return target(default) if default else None


def working_time(start_date, end_date):
    """Active time in a period as a dict: total, proj {(area, project): time},
    source {(area, project, "claude"|"apps"): time}, proj_days, days {date: (total, by_area, from, to)},
    messages, apps {(app, area, project): seconds}, unassigned {app: seconds},
    in_class {subject: Uni time during timetable courses}, notes."""
    spans = [(s, e, (a, p, "claude")) for s, e, (a, p) in claude_spans(start_date, end_date)]
    messages = len(spans)
    notes, unassigned, apps = [], {}, defaultdict(float)
    courses = course_times(start_date, end_date)
    try:
        aw = activitywatch.spans(start_date, end_date)
        for s, e, (a, p), app in aw["assigned"]:
            spans.append((s, e, (a, p, "apps")))
            apps[(app, a, p)] += (e - s).total_seconds()
        unassigned = dict(aw["unassigned"])
        known = [(s, e, (a, p)) for s, e, (a, p, _) in spans]
        for s, e, app, title in aw["context"]:
            t = context_target(s, e, courses, known)
            if t:
                spans.append((s, e, (*t, "apps")))
                apps[(app, *t)] += (e - s).total_seconds()
            else:
                unassigned[app] = unassigned.get(app, 0) + (e - s).total_seconds()
        since = activitywatch.first_entry()
        if since and since > start_date:
            notes.append(f"_Apps on the Mac (ActivityWatch) are only tracked since {short_day(since)}; "
                         "before that only Claude Code counts._")
    except Exception as e:
        notes.append(f"_ActivityWatch not reachable ({type(e).__name__}): Claude Code time only. "
                     "Is ActivityWatch running (icon in the menu bar)?_")
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
    in_class = defaultdict(timedelta)
    lectures = merge((s, e) for s, e, _ in courses)
    for p in {p for _, _, (a, p, _) in spans if a == "Uni"}:
        for s, e in merge((s, e) for s, e, (a, pp, _) in spans if a == "Uni" and pp == p):
            for cs, ce in lectures:
                if cs < e and s < ce:
                    in_class[p] += min(e, ce) - max(s, cs)
    return {"total": total, "proj": proj, "source": source, "proj_days": proj_days, "days": days,
            "messages": messages, "apps": apps, "unassigned": unassigned, "in_class": dict(in_class),
            "notes": notes}


def section_working_time(start_date, end_date, is_month):
    z = ["## Working time (active time only: Claude Code + apps on the Mac)", ""]
    w = working_time(start_date, end_date)
    total, proj, source, proj_days, days = w["total"], w["proj"], w["source"], w["proj_days"], w["days"]
    messages, apps, unassigned, notes = w["messages"], w["apps"], w["unassigned"], w["notes"]
    if not days:
        return z + notes + ["No tracked working time in this period."]
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
    if any(t >= timedelta(minutes=1) for t in w["in_class"].values()):
        z += ["", "Uni active time during timetable courses (already part of the timetable hours, so count it "
              "only once when adding active time and timetable): "
              + ", ".join(f"{cell(p)} {duration(t)}" for p, t in sorted(w["in_class"].items(), key=lambda pt: -pt[1])
                         if t >= timedelta(minutes=1))]
    if unassigned:
        rest = sorted(unassigned.items(), key=lambda kv: -kv[1])
        subtotal = timedelta(seconds=sum(unassigned.values()))
        z += ["", f"Unassigned (does not count as working time): {duration(subtotal)} – "
              + ", ".join(f"{cell(a)} {duration(timedelta(seconds=s))}" for a, s in rest[:8] if s >= 60)]
    z += [""] + notes + [
        f"_Active time: Claude Code from Claude's reply to your next message (reading, testing, writing), "
        f"gaps over {BREAK_MIN} min count as a flat {FLAT_MIN} min; apps = time in the active window while you "
        "were at the computer, assigned by app, project or file name and by the titles sorted with review.py titles "
        "(generic ones like ChatGPT go to the course or activity around them). Claude windows only count via "
        "Claude Code, simultaneous time only counts once._"]
    return z


def collect_chats(start_date, end_date):
    """{(area, subject, title): days} of the logged claude.ai chats, or None if there is no log yet."""
    if not os.path.exists(CHATS):
        return None
    chats = defaultdict(set)
    with open(CHATS, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if start_date <= d <= end_date:
                chats[(r.get("area") or "Other", r.get("subject") or "", r.get("title") or "")].add(d)
    return chats


def section_chats(start_date, end_date):
    z = ["## claude.ai chats (Chat tab and website)", ""]
    chats = collect_chats(start_date, end_date)
    if chats is None:
        return z + ["No chats logged yet (the daily chat log has not run yet)."]
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


def collect_git(start_date, end_date):
    """(commits {sha: (area, project, time, subject line)}, prs, issues, note or None)."""
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
    return commits, prs, issues, gh_note


def section_git(start_date, end_date):
    z = ["## Git and GitHub", ""]
    commits, prs, issues, gh_note = collect_git(start_date, end_date)
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


_timetable = {}


def timetable(start_date, end_date):
    """(problem or None, {course: [time, days]}, total). The iCal file is fetched once per run."""
    url = SETTINGS.get("timetable_ical")
    if not url:
        return "Not set up yet (no timetable_ical link in settings.json).", {}, timedelta()
    if "events" not in _timetable:
        text, error = None, None
        for attempt in dict.fromkeys([url, url.replace("https://", "http://", 1)]):
            try:
                with urllib.request.urlopen(attempt, timeout=30) as r:
                    text = r.read().decode("utf-8", errors="replace")
                break
            except Exception as e:
                error = e
        _timetable["events"] = ical_events(text) if text is not None else None
        _timetable["error"] = error
    if _timetable["events"] is None:
        return f"_Timetable not available: {_timetable['error']}_", {}, timedelta()
    courses = defaultdict(lambda: [timedelta(), set()])
    total = timedelta()
    for s, e, title in _timetable["events"]:
        if start_date <= s.date() <= end_date:
            courses[title][0] += e - s
            courses[title][1].add(s.date())
            total += e - s
    return None, courses, total


def timetable_by_subject(courses):
    per_subject = defaultdict(timedelta)
    for title, (t, _) in courses.items():
        per_subject[course_subject(title) or "(not mapped)"] += t
    return per_subject


def section_timetable(start_date, end_date):
    z = ["## Uni hours according to the timetable", ""]
    problem, courses, total = timetable(start_date, end_date)
    if problem:
        return z + [problem]
    if not courses:
        return z + ["No courses in this period."]
    per_subject = timetable_by_subject(courses)
    z += [f"Scheduled total: **{duration(total)}**", "", "| Course | Subject | Hours | Days |", "|---|---|---:|---|"]
    for title, (t, ds) in sorted(courses.items(), key=lambda kv: -kv[1][0]):
        subject = course_subject(title)
        z.append(f"| {cell(title)} | {cell(subject or '–')} | {duration(t)} | {days_text(ds)} |")
    z += ["", "Per subject: " + ", ".join(f"{s} {duration(t)}" for s, t in
                                       sorted(per_subject.items(), key=lambda st: -st[1]))]
    return z


def course_subject(title):
    """Subject of a timetable course from settings.json "timetable_subjects" (course code -> subject),
    e.g. {"DB": "Databases"} maps "07_DB2VO" to Databases. None if no code matches."""
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


def sport_activities(start_date, end_date, strava):
    """([(date, activity)] in the period, sorted, and the errors of the Strava query)."""
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
    return acts, errors


def per_sport(acts):
    """{sport: [sessions, minutes, km]}"""
    result = defaultdict(lambda: [0, 0, 0.0])
    for _, a in acts:
        p = result[a.get("sport") or "?"]
        p[0] += 1
        p[1] += int(a.get("min") or 0)
        p[2] += float(a.get("km") or 0)
    return result


def heaviest_sets(acts):
    """{exercise: (kg, reps)} with the heaviest working set in these activities."""
    best = {}
    for _, a in acts:
        for e in a.get("exercises") or []:
            if isinstance(e, list) and len(e) == 4 and e[1] is not None:
                if e[0] not in best or (e[1], e[2] or 0) > best[e[0]]:
                    best[e[0]] = (e[1], e[2] or 0)
    return best


def section_sport(start_date, end_date, strava):
    z = ["## Sport (Strava)", ""]
    if strava is None:
        return z + ["Not queried (no Strava data passed in)."]
    acts, errors = sport_activities(start_date, end_date, strava)
    if errors:
        z += [f"_{errors[0]}_", ""]
    if not acts:
        return z + ["No activities in this period." if not errors else "So no sport data."]

    sports = per_sport(acts)
    z += ["| Sport | Sessions | Time | Distance |", "|---|---:|---:|---:|"]
    for sport, (n, minutes, km) in sorted(sports.items(), key=lambda kv: -kv[1][1]):
        z.append(f"| {cell(sport)} | {n} | {duration(timedelta(minutes=minutes))} | {km_text(km)} |")
    n_total = sum(p[0] for p in sports.values())
    min_total = sum(p[1] for p in sports.values())
    km_total = sum(p[2] for p in sports.values())
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


# ---------- Comparison ----------

def previous_period(kind, start_date):
    """(start, end, label) of the week or month before."""
    if kind == "week":
        s = start_date - timedelta(days=7)
        e = s + timedelta(days=6)
        return s, e, f"W{s.isocalendar()[1]}: {short_day(s)} – {short_day(e)}"
    y, m = months_back(start_date.year, start_date.month, 1)
    return date(y, m, 1), start_date - timedelta(days=1), f"{MONTH_NAMES[m - 1]} {y}"


def change(now, before):
    """Difference with an arrow: timedelta -> "+1 h 05 min ▲", numbers -> "−2 ▼", equal -> "±0"."""
    if isinstance(now, timedelta):
        minutes = round((now - before).total_seconds() / 60)
        if minutes == 0:
            return "±0"
        return f"{'+' if minutes > 0 else '−'}{duration(timedelta(minutes=abs(minutes)))} {'▲' if minutes > 0 else '▼'}"
    diff = round(now - before, 1)
    if not diff:
        return "±0"
    text = f"{abs(diff):g}" if isinstance(diff, float) else str(abs(diff))
    return f"{'+' if diff > 0 else '−'}{text} {'▲' if diff > 0 else '▼'}"


def sport_text(n, minutes, km):
    if not n:
        return "–"
    return f"{n} × {duration(timedelta(minutes=minutes))}" + (f", {km_text(km)}" if km else "")


def first_claude_day():
    """Day of the oldest row in claude-activity.csv, or None."""
    try:
        with open(CSV, newline="", encoding="utf-8") as fh:
            days = [local(r.get("end_utc") or "") for r in csv.DictReader(fh)]
    except OSError:
        return None
    days = [d.date() for d in days if d]
    return min(days) if days else None


def section_comparison(kind, start_date, end_date, strava, today):
    p_start, p_end, label = previous_period(kind, start_date)
    word = "week" if kind == "week" else "month"
    z = [f"## Comparison with the previous {word} ({label})", "",
         f"| | This {word} | Previous {word} | Change |", "|---|---:|---:|---:|"]
    row = lambda name, now, before, fmt=str: z.append(
        f"| {name} | {fmt(now)} | {fmt(before)} | {change(now, before)} |")
    now_w, before_w = working_time(start_date, end_date), working_time(p_start, p_end)
    row("**Active time total**", now_w["total"], before_w["total"], lambda t: f"**{duration(t)}**")
    row("Days with active time", len(now_w["days"]), len(before_w["days"]))
    keys = {k for k, t in list(now_w["proj"].items()) + list(before_w["proj"].items()) if t >= timedelta(minutes=1)}
    for a in sorted({a for a, _ in keys}, key=area_order):
        area_time = lambda w: sum((t for (aa, _), t in w["proj"].items() if aa == a), timedelta())
        row(f"**{a}**", area_time(now_w), area_time(before_w), lambda t: f"**{duration(t)}**")
        for _, p in sorted((k for k in keys if k[0] == a),
                           key=lambda k: (-now_w["proj"].get(k, timedelta()), -before_w["proj"].get(k, timedelta()))):
            row(f"– {cell(p)}", now_w["proj"].get((a, p), timedelta()), before_w["proj"].get((a, p), timedelta()), duration)

    problem, now_c, now_total = timetable(start_date, end_date)
    if not problem:
        _, before_c, before_total = timetable(p_start, p_end)
        row("**Uni attendance (timetable)**", now_total, before_total, lambda t: f"**{duration(t)}**")
        now_s, before_s = timetable_by_subject(now_c), timetable_by_subject(before_c)
        for s in sorted(set(now_s) | set(before_s), key=lambda s: -now_s.get(s, timedelta())):
            row(f"– {cell(s)}", now_s.get(s, timedelta()), before_s.get(s, timedelta()), duration)

    now_git, before_git = collect_git(start_date, end_date)[0], collect_git(p_start, p_end)[0]
    row("Commits", len(now_git), len(before_git))
    now_chats, before_chats = collect_chats(start_date, end_date), collect_chats(p_start, p_end)
    if now_chats is not None:
        row("claude.ai chats", len(now_chats), len(before_chats or {}))

    heavier = []
    if strava is not None:
        now_acts, _ = sport_activities(start_date, end_date, strava)
        before_acts, _ = sport_activities(p_start, p_end, strava)
        now_sp, before_sp = per_sport(now_acts), per_sport(before_acts)
        row("**Sport sessions**", len(now_acts), len(before_acts), lambda n: f"**{n}**")
        row("Sport time", timedelta(minutes=sum(v[1] for v in now_sp.values())),
            timedelta(minutes=sum(v[1] for v in before_sp.values())), duration)
        for sport in sorted(set(now_sp) | set(before_sp), key=lambda s: -now_sp.get(s, [0, 0, 0])[1]):
            n, b = now_sp.get(sport, [0, 0, 0.0]), before_sp.get(sport, [0, 0, 0.0])
            z.append(f"| – {cell(sport)} | {sport_text(*n)} | {sport_text(*b)} | {change(n[0], b[0])} |")
        now_best, before_best = heaviest_sets(now_acts), heaviest_sets(before_acts)
        heavier = [f"{cell(name)} {set_text(*before_best[name])} → {set_text(*best)}"
                   for name, best in sorted(now_best.items())
                   if name in before_best and best > before_best[name]]
    if heavier:
        z += ["", f"Gym, heaviest set better than in the previous {word} (more weight or more reps): "
              + "; ".join(heavier)]

    notes = []
    first = first_claude_day()
    if p_start < keep_from(today) or (first and first > p_start):
        since = max(keep_from(today), first or keep_from(today))
        notes.append(f"_Claude Code data only goes back to {short_day(since)}, so the previous {word} is "
                     "incomplete; do not read its lower hours as a real change._")
    try:
        since = activitywatch.first_entry()
    except Exception:
        since = None
    if since and since > p_start:
        notes.append(f"_Apps on the Mac are only tracked since {short_day(since)}, so the previous {word} "
                     "has less app time; compare Claude Code time with care._")
    if strava is None:
        notes.append("_No Strava data, so no sport comparison._")
    return z + ([""] + notes if notes else [])


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
    today = today or date.today()
    start_date, end_date, title, path = period(kind, value, today)
    names = {**{a: a for a in AREAS}, **(SETTINGS.get("area_names") or {})}
    z = [f"# Data for the review · {title}", "",
         f"Review language: {SETTINGS.get('review_language') or 'English'}. Area names to use: "
         + ", ".join(f"{a} = {names[a]}" for a in AREAS) + ".",
         f"Save the review as: `{os.path.relpath(path, BASE)}`", ""]
    for section in (lambda: section_working_time(start_date, end_date, kind == "month"),
                    lambda: section_chats(start_date, end_date),
                    lambda: section_git(start_date, end_date),
                    lambda: section_uni_files(start_date, end_date),
                    lambda: section_timetable(start_date, end_date),
                    lambda: section_sport(start_date, end_date, strava),
                    lambda: section_comparison(kind, start_date, end_date, strava, today)):
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


PENDING = os.path.join(BASE, "titles-pending.json")  # number -> title of the last titles list


def titles(kind, value=None, today=None):
    """Prints the window titles of a period that no rule could assign, or only to an area (at
    least 1 minute each), with the course running at that time and what you did around it, so
    Claude can sort them. Each gets a number for save-titles (titles can be very long)."""
    today = today or date.today()
    start_date, end_date, label, _ = period(kind, value, today)
    try:
        aw = activitywatch.spans(start_date, end_date)
    except Exception as e:
        print(f"ActivityWatch not reachable ({type(e).__name__}): nothing to sort.")
        return
    courses = course_times(start_date, end_date)
    known = [(s, e, k) for s, e, k in claude_spans(start_date, end_date)]
    known += [(s, e, k) for s, e, k, _ in aw["assigned"]]

    def rows(found):
        out = []
        for (app, title), sp in found.items():
            seconds = sum((e - s).total_seconds() for s, e in sp)
            if seconds < 60:
                continue
            during, around = defaultdict(float), defaultdict(float)
            for s, e in sp:
                for cs, ce, subject in courses:
                    if cs < e and s < ce:
                        during[subject or "(course without subject)"] += (min(e, ce) - max(s, cs)).total_seconds()
                k = nearest(s, e, [x for x in known if x[0] != s])
                if k and k[1] != "(general)":
                    around["/".join(k)] += (e - s).total_seconds()
            top = lambda d: ", ".join(k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:2]) or "–"
            out.append((seconds, title, app, {s.date() for s, _ in sp}, top(during), top(around)))
        return sorted(out, key=lambda r: -r[0])

    unassigned, general = rows(aw["titles"]), rows(aw["general"])
    print(f"# Window titles to sort · {label}\n")
    if not unassigned and not general:
        print("Nothing to sort.")
        return
    pending = {}
    for heading, found in (("Not assigned", unassigned), ("Only assigned to an area (give the subject or project if it is clear)", general)):
        if not found:
            continue
        print(f"## {heading}\n\n| No. | Title | App | Time | Days | During a course | Around it |\n|---|---|---|---:|---|---|---|")
        for seconds, title, app, ds, during, around in found:
            n = str(len(pending) + 1)
            pending[n] = title
            short = title if len(title) <= 160 else title[:157] + "…"
            print(f"| {n} | {cell(short)} | {cell(app)} | {duration(timedelta(seconds=seconds))} | {days_text(ds)} "
                  f"| {cell(during)} | {cell(around)} |")
        print()
    tmp = PENDING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pending, fh, ensure_ascii=False)
    os.replace(tmp, PENDING)
    print("Known projects and subjects:")
    projects(today)


def save_titles(entries, today=None):
    """Remembers decisions [{"no": number from the titles list, "target": ...}] in titles.csv."""
    today = today or date.today()
    try:
        with open(PENDING, encoding="utf-8") as fh:
            pending = json.load(fh)
    except (OSError, ValueError):
        pending = {}
    rows = activitywatch.load_decisions()
    new, skipped = 0, []
    for e in entries:
        title = pending.get(str(e.get("no", "")).strip()) or str(e.get("title", "")).strip()
        decision = str(e.get("target", "")).strip()
        if not title or not activitywatch.valid_decision(decision):
            skipped.append(e.get("no") or e.get("title"))
            continue
        new += title not in rows
        rows[title] = (today.isoformat(), decision)
    write_csv(activitywatch.TITLES, activitywatch.TITLE_COLUMNS,
              sorted((t, d, when) for t, (when, d) in rows.items()))
    if os.path.exists(PENDING):
        os.remove(PENDING)
    print(f"{new} new decisions, {len(rows)} in total in titles.csv"
          + (f"; skipped (unknown number, or target not none, context or an area like Uni/Subject): {skipped}"
             if skipped else ""))


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


SHOWN_WEEK = re.compile(r"^\.shown-(\d{4})-W(\d{2})$")
SHOWN_MONTH = re.compile(r"^\.shown-(\d{4})-(\d{2})$")


def remove_old_markers(today):
    """A chat task that shows the reviews may leave markers like .shown-2026-W45 in weeks/ and
    .shown-2026-10 in months/; it only needs the one of the latest period."""
    for folder, pattern in ((WEEKS, SHOWN_WEEK), (MONTHS, SHOWN_MONTH)):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            m = pattern.match(name)
            if not m:
                continue
            try:
                if pattern is SHOWN_WEEK:
                    old = (today - date.fromisocalendar(int(m[1]), int(m[2]), 7)).days > 35
                else:
                    old = (int(m[1]), int(m[2])) < months_back(today.year, today.month, 2)
            except ValueError:
                continue
            if old:
                os.remove(os.path.join(folder, name))


def cleanup(today=None):
    today = today or date.today()
    previous_month = months_back(today.year, today.month, 1)
    cutoff = keep_from(today)
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
            # Keep a week for at least 7 days, so it can still be shown even if the monthly
            # review of its month runs the same morning
            if (month_done and (today - sunday).days > 7) or (sunday.year, sunday.month) < previous_month:
                os.remove(os.path.join(WEEKS, name))
    remove_old_markers(today)
    if os.path.isdir(MONTHS):
        oldest = months_back(today.year, today.month, KEEP_MONTHS)
        for name in os.listdir(MONTHS):
            m = MONTH_FILE.match(name)
            if m and (int(m[1]), int(m[2])) < oldest:
                os.remove(os.path.join(MONTHS, name))
    decisions = activitywatch.load_decisions()
    keep = {t: v for t, v in decisions.items() if v[0] >= cutoff.isoformat()}
    if len(keep) < len(decisions):
        write_csv(activitywatch.TITLES, activitywatch.TITLE_COLUMNS, sorted((t, d, w) for t, (w, d) in keep.items()))
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
        start_date = previous_period(a[1], start_date)[0]  # the previous period too, for the comparison
        print(STRAVA_JS.replace("__FROM__", start_date.isoformat()).replace("__TO__", end_date.isoformat()))
    elif a[:1] == ["titles"] and len(a) >= 2 and a[1] in ("week", "month"):
        titles(a[1], a[2] if len(a) > 2 else None)
    elif a == ["save-titles"]:
        save_titles(json.load(sys.stdin))
    elif a == ["save-chats"]:
        save_chats(json.load(sys.stdin))
    elif a == ["projects"]:
        projects()
    elif a == ["cleanup"]:
        cleanup()
    else:
        sys.exit(__doc__)
