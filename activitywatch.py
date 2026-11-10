#!/usr/bin/env python3
"""Reads from ActivityWatch (runs locally, http://localhost:5600) which app and window you were
active in and when, and maps that to an area and project or subject.
Only time in which you were at the computer counts (ActivityWatch status "not-afk").

Mapping, in this order:
1. Claude does not count here; that already comes from Claude Code (export.py).
2. File, folder or project names in the window title (IDEs, Preview, Finder, ...) -> the folder
   they are in (study, work and private folders from settings.json). ONLYOFFICE shows no file
   name in its title; there the document you opened last before counts (documents.csv,
   recorded hourly by export.py from ONLYOFFICE's recent files list). Files in the work folder
   outside the projects count as Work/Admin (e.g. invoices).
3. App rules from settings.json "apps", e.g. {"sql developer": "Uni/Databases", "clion": "Uni/C"}
   (a part of the app name -> area/project).
4. Keywords in the window title (browser tabs, chat apps): subject names, work project names and
   settings.json "keywords", e.g. {"Uni/Databases": ["SQL"], "Uni": ["moodle"]}.
Anything that does not match stays "unassigned" and does not count as working time.

cleanup() deletes entries before a cutoff date directly through the ActivityWatch API,
at most DELETE_PER_RUN per call (the hourly run catches up on the rest)."""
import json, os, re, urllib.request
from collections import defaultdict
from datetime import datetime, time, timedelta

from common import (HOME, PRIVATE_DIRS, SETTINGS, UNI_DIRS, WORK_DIR, WORK_PROJECTS, classify,
                    target)
from export import DOCUMENTS, ONLYOFFICE_RECENTS

API = "http://localhost:5600/api/0"
DELETE_PER_RUN = 3000
IGNORE = {"claude"}
APPS = {app.lower(): target(t) for app, t in (SETTINGS.get("apps") or {}).items()}
SEPARATOR = re.compile(r"\s+[–—|·-]\s+|\s+[-–—]$")
SKIP_DIRS = {"node_modules", "build", "dist", "out", "target", "__pycache__", "venv", ".git"}
# Names too generic to identify a project (including the German "Übung" = exercise)
TOO_GENERIC = {"main", "index", "readme", "src", "test", "tests", "lib", "include", "docs", "doc",
               "assets", "img", "images", "style", "styles", "app", "data", "untitled", "notes",
               "solution", "cmakelists", "makefile", "public", "config", "scripts", "übung"}


# ---------- API ----------

def _request(path, data=None, method=None, timeout=10):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode()
    return json.loads(text) if text.strip() else None


def _buckets():
    b = _request("/buckets/")
    windows = [k for k in b if k.startswith("aw-watcher-window_")]
    afk = [k for k in b if k.startswith("aw-watcher-afk_")]
    return windows, afk, b


def _active_windows(from_dt, to_dt):
    """Window events, trimmed to the time in which you were at the computer."""
    windows, afk, _ = _buckets()
    result = []
    for w in windows:
        host = w[len("aw-watcher-window_"):]
        a = f"aw-watcher-afk_{host}"
        if a not in afk:
            continue
        query = [f'afk = flood(query_bucket("{a}"));',
                 'afk = filter_keyvals(afk, "status", ["not-afk"]);',
                 f'win = flood(query_bucket("{w}"));',
                 'win = filter_period_intersect(win, afk);',
                 'RETURN = win;']
        period = f"{from_dt.isoformat()}/{to_dt.isoformat()}"
        answer = _request("/query/", {"timeperiods": [period], "query": query}, "POST", timeout=60)
        result += answer[0] if answer else []
    return result


def first_entry():
    """Date of the oldest window entry (data exists from then on), or None."""
    windows, _, b = _buckets()
    created = [b[w].get("created") for w in windows if b[w].get("created")]
    if not created:
        return None
    return datetime.fromisoformat(min(created).replace("Z", "+00:00")).astimezone().date()


# ---------- Mapping ----------

def _pattern(word):
    """Keyword as a whole word, ignoring separators and CamelCase: "MyWebPlayer" also matches
    "My Web Player", but "Java" does not match "JavaScript"."""
    parts = []
    for p in re.split(r"[\s_\-./]+", word):
        parts += re.sub(r"(?<=[a-z])(?=[A-Z])", " ", p).split()
    if len("".join(parts)) < 3:
        return None
    core = r"[\s_\-./]*".join(re.escape(p.lower()) for p in parts)
    return re.compile(r"(?<![a-zäöüß])" + core + r"(?![a-zäöüß])")  # "SQL4" matches, "JavaScript" does not


def _document_history():
    """Sorted list of (opened, path) from documents.csv and the current ONLYOFFICE list."""
    import csv
    import xml.etree.ElementTree as ET
    entries = set()
    try:
        with open(DOCUMENTS, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                entries.add((datetime.fromisoformat(r["opened"]), r["path"]))
    except (OSError, ValueError, KeyError):
        pass
    try:
        for f in ET.parse(ONLYOFFICE_RECENTS).getroot().iter("file"):
            try:
                entries.add((datetime.strptime(f.get("date", ""), "%d.%m.%Y %H:%M"), f.get("path") or ""))
            except ValueError:
                pass
    except (OSError, ET.ParseError):
        pass
    return sorted(e for e in entries if e[1])


class Mapper:
    def __init__(self):
        self.names = defaultdict(dict)   # file/folder name (lower case) -> {(area, project): latest change}
        self.keywords = []               # (pattern, (area, project))
        self.cache = {}
        self.documents = _document_history()
        self._build_index()
        self._build_keywords()

    def _remember(self, name, target_, path):
        lower = name.lower().strip()
        stem = os.path.splitext(lower)[0]
        try:
            changed = os.path.getmtime(path)
        except OSError:
            changed = 0
        for n in {lower, stem}:
            if len(n) >= 4 and n not in TOO_GENERIC:
                self.names[n][target_] = max(changed, self.names[n].get(target_, 0))

    def _build_index(self):
        for root in UNI_DIRS:
            for folder, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith((".", "cmake-build")) and d not in SKIP_DIRS]
                t = classify(folder)
                if folder != root:
                    self._remember(os.path.basename(folder), t, folder)
                for f in files:
                    if not f.startswith("."):
                        self._remember(f, t, os.path.join(folder, f))
        for root in dict.fromkeys(r for r in (WORK_PROJECTS, WORK_DIR) if r):
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for name in entries:
                path = os.path.join(root, name)
                if not name.startswith(".") and path != WORK_PROJECTS and os.path.isdir(path):
                    self._remember(name, classify(path), path)
        for folder in PRIVATE_DIRS:
            self._remember(os.path.basename(folder), classify(folder), folder)
        # If a name exists in several subjects (e.g. UE04 in two semesters), the most recently
        # changed folder wins, which is usually the current semester
        self.names = {n: max(t.items(), key=lambda tc: tc[1])[0] for n, t in self.names.items()}

    def _build_keywords(self):
        words = []  # (word, target)
        for a, p in set(self.names.values()):
            if p.startswith("(") or p.lower() in TOO_GENERIC:
                continue
            if a == "Uni":
                words.append((p, (a, p)))
                if "_" in p:  # C_C++ -> also "C/C++" and "C++"
                    words += [(p.replace("_", "/"), (a, p)), (p.split("_")[-1], (a, p))]
            elif a == "Work" and len(p) >= 6:
                words.append((p, (a, p)))
        for t, keyword_list in (SETTINGS.get("keywords") or {}).items():
            words += [(w, target(t)) for w in keyword_list]
        # Specific targets before general ones ("Uni" alone), then longer keywords first
        words.sort(key=lambda wt: (wt[1][1] == "(general)", -len(wt[0])))
        for word, t in words:
            m = _pattern(word)
            if m:
                self.keywords.append((m, t))

    def _from_title(self, title):
        parts = [p.strip(" •*") for p in SEPARATOR.split(title) if p and p.strip()]
        for p in parts:
            if "/" in p and ("~/" in p or p.startswith(HOME)):
                t = classify(os.path.expanduser(p.split(" ")[0]))
                if t[0] != "Other":
                    return t
            lower = p.lower()
            for n in (lower, os.path.splitext(lower)[0]):
                if n in self.names:
                    return self.names[n]
        return None

    def _from_keyword(self, title):
        lower = title.lower()
        for pattern, t in self.keywords:
            if pattern.search(lower):
                return t
        return None

    def file(self, path):
        """(area, project) for a file: by its folder first, otherwise by its name."""
        folder = os.path.dirname(path)
        t = classify(folder)
        if WORK_DIR and (folder == WORK_DIR or folder.startswith(WORK_DIR + "/")):
            if t[0] == "Work" and (WORK_PROJECTS == WORK_DIR or os.path.isdir(os.path.join(WORK_PROJECTS, t[1]))):
                return t
            return ("Work", "Admin")
        if t[0] in ("Uni", "Private"):
            return t
        name = os.path.basename(path)
        return self._from_title(name) or self._from_keyword(os.path.splitext(name)[0])

    def document_at(self, moment):
        """Target of the document last opened in ONLYOFFICE before this moment (on the same day)."""
        last = None
        for opened, path in self.documents:
            if opened > moment + timedelta(minutes=1):
                break
            last = (opened, path)
        if last and last[0].date() == moment.date():
            return self.file(last[1])
        return None

    def assign(self, app, title):
        """(area, project), "ignore" or None (unassigned)."""
        key = (app, title)
        if key not in self.cache:
            self.cache[key] = self._assign(app or "", title or "")
        return self.cache[key]

    def _assign(self, app, title):
        a = app.lower().replace("\xad", "")
        if a in IGNORE:
            return "ignore"
        t = self._from_title(title) if title else None
        if t:
            return t
        for name, t in APPS.items():
            if name in a:
                return t
        return self._from_keyword(title) if title else None


# ---------- Evaluation ----------

def _local_naive(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def spans(start_date, end_date):
    """{"assigned": [(start, end, (area, project), app)], "unassigned": {app: seconds}}.
    Times are local time without time zone; events across midnight are split.
    Raises an exception if ActivityWatch cannot be reached."""
    tz = datetime.now().astimezone().tzinfo
    from_dt = datetime.combine(start_date, time.min).replace(tzinfo=tz)
    to_dt = datetime.combine(end_date + timedelta(days=1), time.min).replace(tzinfo=tz)
    m = Mapper()
    assigned, unassigned = [], defaultdict(float)
    for e in _active_windows(from_dt, to_dt):
        duration = float(e.get("duration") or 0)
        if duration < 1:
            continue
        start = _local_naive(e["timestamp"])
        end = start + timedelta(seconds=duration)
        d = e.get("data") or {}
        app = (d.get("app") or "?").replace("\xad", "")
        target = m.assign(d.get("app"), d.get("title"))
        if target is None and "onlyoffice" in app.lower():
            target = m.document_at(start)
        if target == "ignore":
            continue
        if target is None:
            unassigned[app] += duration
            continue
        while start.date() < end.date():  # split at midnight
            midnight = datetime.combine(start.date() + timedelta(days=1), time.min)
            assigned.append((start, midnight, target, app))
            start = midnight
        assigned.append((start, end, target, app))
    return {"assigned": assigned, "unassigned": dict(unassigned)}


def cleanup(cutoff):
    """Deletes window and AFK entries that end before the cutoff (a date). Returns the count."""
    try:
        windows, afk, _ = _buckets()
    except Exception:
        return 0  # ActivityWatch is not running right now: next run
    limit = datetime.combine(cutoff, time.min).astimezone().isoformat()
    deleted = 0
    for b in windows + afk:
        left = DELETE_PER_RUN - deleted
        if left <= 0:
            break
        q = urllib.request.quote(limit)
        for e in _request(f"/buckets/{b}/events?end={q}&limit={left}") or []:
            _request(f"/buckets/{b}/events/{e['id']}", method="DELETE")
            deleted += 1
    return deleted
