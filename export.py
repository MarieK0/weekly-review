#!/usr/bin/env python3
"""Exports the time spans in which YOU were active in Claude Code (no content) to
claude-activity.csv next to this script.
Columns: start_utc, end_utc, area, project

Active time = the span leading up to each of your messages (or answers to Claude's questions),
counted from the moment it was your turn:
- If Claude had finished, it counts from Claude's reply: reading, testing, trying, writing.
  Time in which Claude worked on its own does not count.
- If you write while Claude is still working (e.g. feedback while testing), it counts from your
  previous message, because you were clearly involved.
- If the span is longer than BREAK_MIN, you were away in between; then only FLAT_MIN count for
  reading and writing. The same applies to the first message of a session.
- Automated runs (scheduled tasks) and system messages do not count.
- Area is Work, Uni, Private or Other (see common.py); for Uni the project is the subject.
- Worktrees count towards their main project, sessions without a folder towards "(no project folder)".
- The CSV keeps only the current and the two previous months; the monthly review compares
  the past month with the one before and needs no more. Older rows are removed on every run.
- Until then rows are kept even if Claude has already deleted the transcript.

Also records which documents were opened in ONLYOFFICE (documents.csv), see remember_documents()."""
import csv, glob, json, os, re, sys
from datetime import date, datetime, timedelta

sys.dont_write_bytecode = True  # no __pycache__ folder next to the scripts
from common import BASE, HOME, classify, iso, local_date, parse_time, keep_from

TRANSCRIPTS = os.path.join(HOME, ".claude", "projects")
CSV = os.path.join(BASE, "claude-activity.csv")
DOCUMENTS = os.path.join(BASE, "documents.csv")  # when which document was opened in ONLYOFFICE
ONLYOFFICE_RECENTS = os.path.join(HOME, "Library", "Application Support", "asc.onlyoffice.ONLYOFFICE", "recents.xml")
BREAK_MIN = 30
FLAT_MIN = 5
QUESTION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}  # answers to these come from you


def plain_text(c):
    if isinstance(c, list):
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return ""
    return re.sub(r"(?s)<system-reminder>.*?</system-reminder>", "", c).strip()


def is_yours(e, tool_names):
    """True if the entry is an action of yours (a message or an answer to a question from Claude)."""
    kind = e.get("type")
    if kind == "attachment":  # a message you sent while Claude was working
        a = e.get("attachment") or {}
        text = plain_text(a.get("prompt"))
        return a.get("type") == "queued_command" and bool(text) and not text.startswith("<")
    if kind != "user" or e.get("isSidechain"):
        return False
    c = (e.get("message") or {}).get("content")
    results = [b for b in c if isinstance(b, dict) and b.get("type") == "tool_result"] if isinstance(c, list) else []
    if results:
        return any(tool_names.get(b.get("tool_use_id")) in QUESTION_TOOLS for b in results)
    if "origin" in e:  # newer transcripts say directly whether a message comes from a human
        return isinstance(e["origin"], dict) and e["origin"].get("kind") == "human"
    if e.get("isMeta") or e.get("isCompactSummary"):
        return False
    text = plain_text(c)
    return bool(text) and not text.startswith("<") and not text.startswith("This session is being continued")


def active_spans():
    """Returns ({end_utc: (start_utc, area, project)}, all timestamps of user/attachment entries)."""
    spans, seen = {}, set()
    pause, flat = timedelta(minutes=BREAK_MIN), timedelta(minutes=FLAT_MIN)
    for f in glob.glob(os.path.join(TRANSCRIPTS, "*", "*.jsonl")):
        entries = []
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    t = parse_time(e.get("timestamp")) if e.get("timestamp") else None
                    if t and t.tzinfo:
                        entries.append((t, e))
        except OSError:
            continue
        entries.sort(key=lambda te: te[0])
        # finished: Claude ended its reply; asked: Claude is waiting for your answer
        tool_names, cwd, finished, asked, mine_last = {}, None, None, None, None
        for t, e in entries:
            cwd = e.get("cwd") or cwd
            kind = e.get("type")
            if kind == "assistant" and not e.get("isSidechain"):
                m = e.get("message") or {}
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tool_names[b.get("id")] = b.get("name")
                        if b.get("name") in QUESTION_TOOLS:
                            asked = t
                if m.get("stop_reason") in ("end_turn", "stop_sequence"):
                    finished = t
            elif kind == "system" and e.get("subtype") == "api_error":
                finished = t
            if kind in ("user", "attachment"):
                seen.add(e["timestamp"])
            if is_yours(e, tool_names):
                before = max((x for x in (finished, asked, mine_last) if x and x < t), default=None)
                start = before if before and t - before <= pause else t - flat
                # The same message can appear twice (e.g. in resumed sessions): keep the longer span
                old = spans.get(e["timestamp"])
                if not old or iso(start) < old[0]:
                    spans[e["timestamp"]] = (iso(start), *classify(cwd))
                mine_last = t
    return spans, seen


def remember_documents(today):
    """ONLYOFFICE only keeps the last opening per file; this records every opening
    (opened, path) so activitywatch.py can attribute ONLYOFFICE time to the right document."""
    import xml.etree.ElementTree as ET
    rows = set()
    if os.path.exists(DOCUMENTS):
        with open(DOCUMENTS, newline="", encoding="utf-8") as fh:
            rows = {(r["opened"], r["path"]) for r in csv.DictReader(fh)}
    before = set(rows)
    try:
        for f in ET.parse(ONLYOFFICE_RECENTS).getroot().iter("file"):
            try:
                t = datetime.strptime(f.get("date", ""), "%d.%m.%Y %H:%M")
            except ValueError:
                continue
            if f.get("path"):
                rows.add((t.isoformat(timespec="minutes"), f.get("path")))
    except (OSError, ET.ParseError):
        pass
    cutoff = keep_from(today).isoformat()
    keep = sorted(r for r in rows if r[0] >= cutoff)
    if set(keep) != before:
        tmp = DOCUMENTS + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["opened", "path"])
            w.writerows(keep)
        os.replace(tmp, DOCUMENTS)


def main(today=None):
    today = today or date.today()
    remember_documents(today)
    new, seen = active_spans()

    # Keep old rows whose transcript no longer exists
    rows = {}
    if os.path.exists(CSV):
        with open(CSV, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r.get("end_utc"):
                    continue
                end, start = r["end_utc"], r.get("start_utc") or r["end_utc"]
                if end in seen:
                    continue  # transcript still exists: recalculated above
                rows[end] = (start, r.get("area") or "Other", r.get("project") or "")
    rows.update(new)

    cutoff = keep_from(today)
    keep = sorted((s, end, a, p) for end, (s, a, p) in rows.items()
                  if (local_date(end) or date.min) >= cutoff)
    tmp = CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["start_utc", "end_utc", "area", "project"])
        w.writerows(keep)
    os.replace(tmp, CSV)


if __name__ == "__main__":
    main()
