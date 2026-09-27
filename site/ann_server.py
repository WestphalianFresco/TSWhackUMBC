"""Advisor Ann's server: her brain (Gemini + the advisor pipeline) and her voice (ElevenLabs).

    python site/ann_server.py        ->  open http://localhost:8000

GET  /           the "Enter my data" chat, assembled like build.py does (page.html head + enter.html + advisor.js),
                 so an edit to advisor.js shows up on refresh without rebuilding
POST /api/ann    {conversation, messages, attachments} -> {reply}
                 Gemini turns what the student typed, said (audio) or uploaded into fields the advisor understands;
                 the advisor validates them, Ann asks for anything missing, and once the student confirms,
                 the tested pipeline (not Gemini) produces the advice
POST /api/voice  {text} -> audio/mpeg from ElevenLabs; 503 when no key, and the page falls back to the browser voice
GET  /api/stats  Ann's live usage for the last 24 hours, from the Tiger Data continuous aggregate

Ann's memory lives in Tiger Data (TIGER_URL in .env): each student's draft profile in the plain table ann_profiles,
and every turn as a row in the hypertable ann_events (columnstore after 7 days), rolled up hourly by the continuous
aggregate ann_usage_hourly. Without TIGER_URL she keeps drafts in memory and logs nothing.

Keys stay here, read from .env: GEMINI_API_KEY (required); ELEVENLABS_API_KEY for Ann's voice, and optionally
ELEVENLABS_VOICE_ID and ELEVENLABS_MODEL to change it.
"""
import base64
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "advisor"))
import advisor as adv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
# newest Flash first; when one is overloaded (503/429/500) or closed to this key (404), the next one answers
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")   # the low-latency model, so Ann answers quickly
REQUIRED = {"major": "your major (Computer Science or Information Systems)", "track": "your track",
            "credits_earned": "how many credits you've earned", "cumulative_gpa": "your cumulative GPA"}

ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "XrExE9yKIg1WjnnlVkGX")   # Ann's voice, chosen by the team; .env can override
voiceNote = f"ElevenLabs voice {ELEVEN_VOICE}" if os.getenv("ELEVENLABS_API_KEY") else "no ElevenLabs key: the browser voice is used"
print(f"Ann's voice: {voiceNote}")
print("Learning from the data (about 10 seconds)...")
CTX = adv.build()
CATALOG = "\n".join(f"{r.course_id}: {r.course_title}" for r in CTX["cat"].itertuples())
TRACKS = sorted({t for ts in CTX["tracks"].values() for t in ts})
TIGER_URL = os.getenv("TIGER_URL")
MEMORY = {}   # drafts when there is no Tiger Data
_conn, _lock = None, threading.Lock()


def db(sql, params=()):
    """One shared connection, used by one request at a time; reconnects once if Tiger Cloud dropped it."""
    global _conn
    with _lock:
        for attempt in (1, 2):
            try:
                if _conn is None or _conn.closed:
                    _conn = psycopg.connect(TIGER_URL, autocommit=True)
                cur = _conn.execute(sql, params)
                return cur.fetchall() if cur.description else None
            except psycopg.OperationalError:
                _conn = None
                if attempt == 2:
                    raise


def ensure_tables():
    """Created once, the first time the server runs against a database (re-running is a no-op)."""
    if db("SELECT to_regclass('ann_events')")[0][0]:
        return
    for sql in [
        """CREATE TABLE IF NOT EXISTS ann_profiles (
               conversation text PRIMARY KEY,
               profile      jsonb NOT NULL,
               updated_at   timestamptz NOT NULL DEFAULT now())""",
        """CREATE TABLE ann_events (
               time         timestamptz NOT NULL DEFAULT now(),
               conversation text NOT NULL,
               kind         text NOT NULL,   -- intake | advice | voice | error
               model        text,
               latency_ms   integer,
               has_audio    boolean,
               fields_known smallint)""",
        "SELECT create_hypertable('ann_events', by_range('time', INTERVAL '1 day'))",
        "ALTER TABLE ann_events SET (timescaledb.enable_columnstore = true, timescaledb.segmentby = 'kind', timescaledb.orderby = 'time DESC')",
        "CALL add_columnstore_policy('ann_events', after => INTERVAL '7 days')",
        # materialized_only = false: the rollup also includes rows newer than its last refresh (real-time aggregation)
        """CREATE MATERIALIZED VIEW ann_usage_hourly WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
           SELECT time_bucket('1 hour', time) AS hour, kind,
                  count(*)                      AS turns,
                  hyperloglog(1024, conversation) AS conversations_hll,
                  avg(latency_ms)               AS avg_latency_ms,
                  percentile_agg(latency_ms)    AS latency_pct
           FROM ann_events GROUP BY 1, 2 WITH NO DATA""",
        """SELECT add_continuous_aggregate_policy('ann_usage_hourly', start_offset => INTERVAL '3 days',
               end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '5 minutes')""",
    ]:
        db(sql)
    print("Created ann_profiles, the ann_events hypertable and the ann_usage_hourly continuous aggregate")


def load_draft(conversation):
    if not TIGER_URL:
        return MEMORY.setdefault(conversation, {"experiences": [], "courses": []})
    rows = db("SELECT profile FROM ann_profiles WHERE conversation = %s", [conversation])
    return rows[0][0] if rows else {"experiences": [], "courses": []}


def save_draft(conversation, draft):
    if TIGER_URL:
        db("""INSERT INTO ann_profiles (conversation, profile) VALUES (%s, %s)
              ON CONFLICT (conversation) DO UPDATE SET profile = EXCLUDED.profile, updated_at = now()""",
           [conversation, Jsonb(draft)])


def log_event(conversation, kind, started, model=None, has_audio=None, fields_known=None):
    if TIGER_URL:
        db("INSERT INTO ann_events (conversation, kind, model, latency_ms, has_audio, fields_known) VALUES (%s, %s, %s, %s, %s, %s)",
           [conversation, kind, model, round((time.time() - started) * 1000), has_audio, fields_known])


if TIGER_URL:
    ensure_tables()


def nullable(schema):
    return {**schema, "nullable": True}


# Gemini may only answer with values that exist in the dataset: every enum below comes from the data itself
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "transcript": nullable({"type": "STRING"}),
        "major": nullable({"type": "STRING", "enum": sorted(CTX["tracks"])}),
        "track": nullable({"type": "STRING", "enum": TRACKS}),
        "entry_type": nullable({"type": "STRING", "enum": ["First-Time Freshman", "Transfer"]}),
        "credits_earned": nullable({"type": "INTEGER"}),
        "cumulative_gpa": nullable({"type": "NUMBER"}),
        "is_first_generation": nullable({"type": "BOOLEAN"}),
        "work_hours_per_week": nullable({"type": "INTEGER"}),
        "experiences": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["experience_type"], "properties": {
            "experience_type": {"type": "STRING", "enum": sorted(CTX["types"])},
            "organization": nullable({"type": "STRING"}),
            "role_level": nullable({"type": "STRING"})}}},
        "courses": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["course_id"], "properties": {
            "course_id": {"type": "STRING", "enum": sorted(CTX["cat"]["course_id"])},
            "grade": nullable({"type": "STRING", "enum": sorted(adv.GRADES)})}}},
        "unclear": {"type": "ARRAY", "items": {"type": "STRING"}},
        "confirms_profile": {"type": "BOOLEAN"},
    },
    "required": ["experiences", "courses", "unclear", "confirms_profile"],
}

INSTRUCTIONS = f"""You turn what a UMBC computing student types, says in an audio recording, or shows in an uploaded
transcript or resume into structured fields for an advising tool. Rules:
- Fill a field only when the student clearly states it. Never guess; leave anything unknown null.
- Report only what is NEW in the student's latest message and attachments; the known profile is given for context.
- Map course names or numbers to a course_id from the catalog below. If you cannot match one confidently,
  do not invent it: put the student's words in `unclear`.
- Grades are letters only: A B C D F, W for a withdrawal, IP for a course in progress.
- If there is audio, write what the student said, word for word, in `transcript`.
- `confirms_profile` is true only when the latest message clearly agrees that the summary Ann just read back is correct.
Course catalog:
{CATALOG}"""


def gemini_extract(draft, messages, attachments):
    lastAnn = next((m["text"] for m in reversed(messages[:-1]) if m["role"] == "ann"), "")
    parts = [{"text": f"Known profile so far: {json.dumps(draft)}\nAnn's last message: {lastAnn}\n"
                      f"Student's latest message: {messages[-1]['text'] or '(no text, see attachments)'}"}]
    for a in attachments:   # dataUrl = "data:<mime>;base64,<data>"
        mime, _, data = a["dataUrl"].partition(";base64,")
        parts.append({"inline_data": {"mime_type": mime.removeprefix("data:"), "data": data}})
    body = {"system_instruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA,
                                 "temperature": 0}}
    for model in GEMINI_MODELS:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(json.load(r)["candidates"][0]["content"]["parts"][0]["text"]), model
        except urllib.error.HTTPError as e:
            if e.code not in (404, 429, 500, 503) or model == GEMINI_MODELS[-1]:
                raise
            print(f"{model} answered {e.code}, trying the next model", file=sys.stderr)
        except (TimeoutError, urllib.error.URLError) as e:   # a model that hangs is as good as a busy one
            if model == GEMINI_MODELS[-1]:
                raise
            print(f"{model} timed out ({e}), trying the next model", file=sys.stderr)


def merge(draft, new):
    """New facts win; courses merge by course_id so a later grade fills an earlier blank."""
    for k in ["major", "track", "entry_type", "credits_earned", "cumulative_gpa", "is_first_generation", "work_hours_per_week"]:
        if new.get(k) is not None:
            draft[k] = new[k]
    seen = {(e["experience_type"], e.get("organization")) for e in draft.setdefault("experiences", [])}
    draft["experiences"] += [e for e in new["experiences"] if (e["experience_type"], e.get("organization")) not in seen]
    courses = {c["course_id"]: c for c in draft.setdefault("courses", [])}
    for c in new["courses"]:
        if c["course_id"] not in courses or c.get("grade"):
            courses[c["course_id"]] = c
    draft["courses"] = list(courses.values())
    return draft


def summary(d):
    kinds = [e["experience_type"] for e in d.get("experiences", [])]
    graded = [f"{c['course_id']} {c['grade']}" for c in d.get("courses", []) if c.get("grade")]
    return (f"{d['major']}, {d['track']} track, {d['credits_earned']} credits, GPA {d['cumulative_gpa']}. "
            f"Experiences: {', '.join(kinds) or 'none yet'}. Courses: {', '.join(graded) or 'none yet'}.")


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def advice(d):
    """Only numbers from the tested pipeline: the advisor's own progress, projection and ranked levers."""
    r = adv.advise(d, CTX)
    s, p, f, st = r["student"], r["progress"], r["future"], r["strategy"]
    behind = p["vs_peers"][p["vs_peers"]["status"] == "behind"]
    standing = ("you're behind on " + ", ".join(f"{m.lower()} ({ordinal(pct)} percentile)" for m, pct in zip(behind["metric"], behind["percentile"]))
                if len(behind) else "you're on track in every area I measure")
    prem = f["projection"]["premium"]["expected"]
    rec = st["recommended"]
    moves = " ".join(f"{i}) {lever} (about {gain:+,.0f} dollars" + (f", {-months:.1f} months faster" if months < -0.05 else "") + ")."
                     for i, (lever, gain, months) in enumerate(zip(rec["lever"], rec["salary gain ($)"], rec["months to first job"]), 1))
    risk = f" Heads-up: {st['risks'][0]}" if st["risks"] else ""
    return (f"Thanks! Compared with {p['peers_n']} current {s['major']} {s['class_level']}s, {standing}. "
            f"If nothing changes, alumni like you started about {abs(prem):,.0f} dollars "
            f"{'above' if prem >= 0 else 'below'} similar grads. My top moves for you: {moves}{risk} "
            "These are patterns from past alumni, not guarantees. Tell me if anything changes and I'll update your plan.")


def reply_for(conversation, messages, attachments):
    started = time.time()
    draft = load_draft(conversation)
    before = json.dumps(draft, sort_keys=True)
    new, model = gemini_extract(draft, messages, attachments)
    merge(draft, new)
    changed = json.dumps(draft, sort_keys=True) != before
    kind, reply = next_reply(draft, new, changed)
    save_draft(conversation, draft)
    known = sum(draft.get(k) is not None for k in REQUIRED) + len(draft["experiences"]) + len(draft["courses"])
    log_event(conversation, kind, started, model, any(a["type"].startswith("audio/") for a in attachments), known)
    return reply


def next_reply(draft, new, changed):
    """What Ann says next, from the merged draft, and what kind of turn it was: ask, read back, or advise."""
    heard = f'I heard: "{new["transcript"].strip()}" ' if new.get("transcript") else ""
    missing = [label for k, label in REQUIRED.items() if draft.get(k) is None]
    if missing:
        return "intake", (f"{heard}To build your plan I still need {', '.join(missing)}. "
                "You can type it, send a voice note, or upload your unofficial transcript.")
    needGrade = []   # fit_student's `ask` hook: collect every course without a grade instead of prompting on a keyboard
    try:
        adv.fit_student(draft, CTX, ask=lambda cid: needGrade.append(cid) or "IP")   # the "IP" stand-in is never used for advice
    except ValueError as e:
        return "intake", f"{heard}Something doesn't add up: {e}. Could you check that for me?"
    asks = []
    if needGrade:
        asks.append(f"What grade did you get in {', '.join(needGrade)}?")
    if new["unclear"]:
        asks.append(f"I couldn't match: {', '.join(new['unclear'])}. Which course or activity is that?")
    if asks:
        return "intake", heard + " ".join(asks)
    if not draft.get("confirmed"):
        if not new["confirms_profile"]:
            return "intake", f"{heard}Here's what I have: {summary(draft)} Is that right?"
        draft["confirmed"] = True
    elif not changed:   # already advised and nothing new: don't repeat the whole plan
        return "intake", f"{heard}Tell me if anything changes, like a new grade, internship or course, and I'll update your plan."
    return "advice", advice({k: v for k, v in draft.items() if k != "confirmed"})


def enter_page():
    raw = open(os.path.join(HERE, "page.html")).read()   # same assembly as the end of build.py
    head = re.sub(r"<title>.*?</title>\n", "", raw.split('<header class="nav">')[0])
    d0 = raw.index("<!-- Crayon collage filters.")
    defs = raw[d0:raw.index("</svg>", d0) + len("</svg>")]
    page = open(os.path.join(HERE, "enter.html")).read()
    for k, v in {"HEAD": head, "DEFS": defs, "ADVISOR_JS": open(os.path.join(HERE, "advisor.js")).read()}.items():
        page = page.replace("{{" + k + "}}", v)
    return page


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send(200, enter_page().encode(), "text/html; charset=utf-8")
        elif self.path == "/api/stats":
            rows = db("""SELECT hour, kind, turns, distinct_count(conversations_hll), round(avg_latency_ms),
                                round(approx_percentile(0.95, latency_pct))
                         FROM ann_usage_hourly WHERE hour > now() - INTERVAL '24 hours' ORDER BY hour, kind""") if TIGER_URL else []
            stats = [{"hour": h.isoformat(), "kind": k, "turns": n, "conversations": c, "avg_ms": a, "p95_ms": p}
                     for h, k, n, c, a, p in rows]
            self.send(200, json.dumps(stats, default=float).encode(), "application/json")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            if self.path == "/api/ann":
                reply = reply_for(req["conversation"], req["messages"], req.get("attachments", []))
                self.send(200, json.dumps({"reply": reply}).encode(), "application/json")
            elif self.path == "/api/voice":
                key = os.getenv("ELEVENLABS_API_KEY")
                if not key:
                    return self.send(503, json.dumps({"error": voiceNote}).encode(), "application/json")
                tts = urllib.request.Request(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}",
                    data=json.dumps({"text": req["text"], "model_id": ELEVEN_MODEL}).encode(),
                    headers={"Content-Type": "application/json", "xi-api-key": key, "Accept": "audio/mpeg"})
                started = time.time()
                with urllib.request.urlopen(tts, timeout=60) as r:
                    audio = r.read()
                log_event(req.get("conversation", "unknown"), "voice", started, ELEVEN_MODEL)
                self.send(200, audio, "audio/mpeg")
            else:
                self.send(404, b"not found", "text/plain")
        except (urllib.error.URLError, KeyError, ValueError) as e:   # the page shows its own "something went wrong" line
            detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
            print(f"{self.path} failed: {detail}", file=sys.stderr)
            try:
                log_event(req.get("conversation", "unknown"), "error", time.time())
            except psycopg.Error:
                pass
            self.send(502, json.dumps({"error": detail}).encode(), "application/json")

    def log_message(self, *args):   # keep the terminal quiet; errors are printed above
        pass


if __name__ == "__main__":
    port = int(os.getenv("ANN_PORT", "8000"))
    print(f"Advisor Ann is at http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
