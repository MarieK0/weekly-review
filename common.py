"""Settings, paths and the folder -> (area, project) mapping shared by all scripts.

Everything personal (which folders are work, study or private, app rules, keywords, the
timetable link) lives in settings.json next to the scripts; see settings.example.json and
the README. Without settings.json everything counts as "Other"."""
import json, os, re
from datetime import date, datetime, timezone

HOME = os.path.expanduser("~")
BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE, "settings.json")
AREAS = ["Work", "Uni", "Private", "Other"]
CLAUDE_APP_DATA = "/Library/Application Support/Claude/"  # Claude desktop scratch folders


def _load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def _path(p):
    return os.path.normpath(os.path.expanduser(p)) if p else None


SETTINGS = _load_settings()
WORK_DIR = _path(SETTINGS.get("work_dir"))
WORK_PROJECTS = _path(SETTINGS.get("work_projects_dir")) or WORK_DIR
# Study folders -> default subject ("" = the subject is the name of the folder below)
UNI_DIRS = {_path(d): s or "" for d, s in (SETTINGS.get("uni_dirs") or {}).items()}
PRIVATE_DIRS = [_path(d) for d in SETTINGS.get("private_dirs") or []]
# Folder or repo name prefix -> subject, e.g. GitHub Classroom repos {"prog2-": "Programming 2"}
REPO_SUBJECTS = SETTINGS.get("repo_subjects") or {}
TERM_FOLDER = re.compile(SETTINGS.get("term_folder_pattern") or r"^(semester|term)\s*\d+$", re.I)
# Containers whose subfolders are projects in their own right (e.g. a project copy on the Desktop)
CONTAINERS = {"Desktop", "Documents", "Downloads"}


def target(text):
    """"Uni/Databases" -> ("Uni", "Databases"), "Uni" -> ("Uni", "(general)")."""
    area, _, project = text.partition("/")
    return area, project or "(general)"


def _inside(path, folder):
    return bool(folder) and path.startswith(folder + "/")


def _below(path, folder):
    return path[len(folder) + 1:].split("/")


def work_project(name):
    """Project folder name, with copies like "MyApp-feature" counted as "MyApp"."""
    base = name.split("-")[0]
    if base != name and WORK_PROJECTS and os.path.isdir(os.path.join(WORK_PROJECTS, base)):
        return base
    return name


def classify(path):
    """(area, project) for a folder; for Uni the project is the subject."""
    if not path:
        return "Other", "(unknown)"
    path = os.path.normpath(path.split("/.claude/worktrees/")[0])  # worktree -> main project
    if CLAUDE_APP_DATA in path or not path.startswith(HOME + "/"):
        return "Other", "(no project folder)"
    if path == BASE or _inside(path, BASE):
        return "Other", "Weekly review"
    for folder, default in UNI_DIRS.items():
        if path == folder or _inside(path, folder):
            rest = _below(path, folder) if path != folder else []
            if rest and TERM_FOLDER.match(rest[0]):  # Uni/Semester3/Java -> Java
                rest = rest[1:]
            if not rest:
                return "Uni", default or "(general)"
            name = rest[0].strip()
            for prefix, subject in REPO_SUBJECTS.items():
                if name.startswith(prefix):
                    return "Uni", subject
            return "Uni", default or name
    for folder in PRIVATE_DIRS:
        if path == folder or _inside(path, folder):
            return "Private", os.path.basename(folder)
    if _inside(path, WORK_DIR):
        if path == WORK_PROJECTS:
            return "Work", "(general)"
        projects = WORK_PROJECTS if _inside(path, WORK_PROJECTS) else WORK_DIR
        return "Work", work_project(_below(path, projects)[0])
    t = _below(path, HOME)
    name = t[1] if t[0] in CONTAINERS and len(t) > 1 else t[0]
    name = work_project(name)
    if WORK_PROJECTS and os.path.isdir(os.path.join(WORK_PROJECTS, name)):
        return "Work", name
    return "Other", name


def area_order(a):
    return (AREAS.index(a) if a in AREAS else len(AREAS), a)


def local_date(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
    except ValueError:
        return None


def parse_time(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def keep_from(today):
    """First day that collected data is kept for: the 1st of the month two months back, so the
    monthly review can still compare the past month with the one before it."""
    i = today.year * 12 + today.month - 1 - 2
    return date(i // 12, i % 12 + 1, 1)
