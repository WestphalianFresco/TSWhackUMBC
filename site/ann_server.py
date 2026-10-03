"""Advisor Ann's server: her brain (Claude + the advisor pipeline) and her voice (ElevenLabs).

    python site/ann_server.py        ->  open http://localhost:8000

GET  /           the "Enter my data" chat, assembled like build.py does (page.html head + enter.html + charts.js + advisor.js),
                 so an edit to advisor.js shows up on refresh without rebuilding
POST /api/ann    {conversation, messages, attachments} -> {reply, charts}
                 Claude turns what the student typed, said (a voice note, transcribed by ElevenLabs) or uploaded
                 (pictures, PDFs, text files) into fields the advisor understands;
                 the advisor validates them, Ann asks for anything missing, and once the student confirms,
                 the tested pipeline (not Claude) produces the advice, with charts.js specs drawn under it:
                 where the student lines up among peers, their top moves, and where they could start
POST /api/voice  {text} -> audio/mpeg from ElevenLabs; 503 when no key, and the page falls back to the browser voice
GET  /api/stats  Ann's live usage for the last 24 hours, from the Tiger Data continuous aggregate

Ann's memory lives in Tiger Data (TIGER_URL in .env): each student's draft profile in the plain table ann_profiles,
and every turn as a row in the hypertable ann_events (columnstore after 7 days), rolled up hourly by the continuous
aggregate ann_usage_hourly. Without TIGER_URL she keeps drafts in memory and logs nothing.

Keys stay here, read from .env: ANTHROPIC_API_KEY (required); ELEVENLABS_API_KEY for Ann's voice and for hearing
voice notes, and optionally ELEVENLABS_VOICE_ID and ELEVENLABS_MODEL to change her voice.
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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(ROOT, "advisor")]   # advisor.py sits in ../advisor locally, next to this file on Vercel
import advisor as adv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
# Claude Opus 5.5 always thinks before it answers; low effort keeps that short, so a chat turn stays quick.
# The SDK retries a busy (429/5xx) answer once; resolve_course() (code, not the model) still decides which catalog
# course a student meant.
CLAUDE_MODEL, CLAUDE_EFFORT = "claude-opus-5-5", "low"
CLAUDE = None   # made by init(), with the key from .env
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")   # the low-latency model, so Ann answers quickly
ELEVEN_STT = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")   # Claude reads but doesn't listen: voice notes go through Scribe
REQUIRED = {"major": "your major (Computer Science or Information Systems)", "track": "your track",
            "credits_earned": "how many credits you've earned", "cumulative_gpa": "your cumulative GPA"}

ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "XrExE9yKIg1WjnnlVkGX")   # Ann's voice, chosen by the team; .env can override
voiceNote = f"ElevenLabs voice {ELEVEN_VOICE}" if os.getenv("ELEVENLABS_API_KEY") else "no ElevenLabs key: the browser voice is used"
print(f"Ann's voice: {voiceNote}")
TIGER_URL = os.getenv("TIGER_URL")
MEMORY = {}   # drafts when there is no Tiger Data
_conn, _lock = None, threading.Lock()
CTX = SCHEMA = INSTRUCTIONS = TITLES = None   # filled by init(): learning from the data takes ~10 s, so once, not at import
_initLock = threading.Lock()


def init():
    """Learn from the data, make sure Ann's tables exist, and prepare Claude's schema, once per process:
    at startup when run locally, on the first request of each Vercel instance."""
    global CTX, SCHEMA, INSTRUCTIONS, TITLES, CLAUDE
    with _initLock:
        if CTX is not None:
            return
        print("Learning from the data (about 10 seconds)...")
        ctx = adv.build()
        if TIGER_URL:
            ensure_tables()
        # 50 s and one retry stay inside the 120 s vercel.json gives a turn, cold start included
        CLAUDE = anthropic.Anthropic(timeout=50, max_retries=1)
        SCHEMA, INSTRUCTIONS = schema_for(ctx), instructions_for(ctx)
        TITLES = dict(zip(ctx["cat"]["course_id"], ctx["cat"]["course_title"]))
        CTX = ctx


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


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties):
    """Claude's structured outputs take closed objects; every field is required, and null when unknown."""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def schema_for(ctx):
    """Claude may only answer with values that exist in the dataset: every enum below comes from the data itself."""
    tracks = sorted({t for ts in ctx["tracks"].values() for t in ts})
    return obj({
        "major": nullable({"type": "string", "enum": sorted(ctx["tracks"])}),
        "track": nullable({"type": "string", "enum": tracks}),
        "entry_type": nullable({"type": "string", "enum": ["First-Time Freshman", "Transfer"]}),
        "credits_earned": nullable({"type": "integer"}),
        "cumulative_gpa": nullable({"type": "number"}),
        "is_first_generation": nullable({"type": "boolean"}),
        "work_hours_per_week": nullable({"type": "integer"}),
        "experiences": {"type": "array", "items": obj({
            "experience_type": {"type": "string", "enum": sorted(ctx["types"])},
            "organization": nullable({"type": "string"}),
            "role_level": nullable({"type": "string"})})},
        "courses": {"type": "array", "items": obj({
            "said": {"type": "string"},
            "course_id": nullable({"type": "string", "enum": sorted(ctx["cat"]["course_id"])}),
            "grade": nullable({"type": "string", "enum": sorted(adv.GRADES)})})},
        "remove": {"type": "array", "items": {"type": "string"}},
        "unclear": {"type": "array", "items": {"type": "string"}},
        "confirms_profile": {"type": "boolean"},
    })


def instructions_for(ctx):
    catalog = "\n".join(f"{r.course_id}: {r.course_title}" for r in ctx["cat"].itertuples())
    return f"""You turn what a UMBC computing student types, says in a voice note (given to you as its transcription),
or shows in an uploaded transcript or resume into structured fields for an advising tool. Rules:
- Fill a field only when the student clearly states it. Never guess; leave anything unknown null.
- Report only what is NEW in the student's latest message and attachments; the known profile is given for context.
- For each course, put the student's own words in `said` exactly as they said them (e.g. "IS 210", "data structures"),
  and your best matching course_id from the catalog below in `course_id`, or null if unsure. Never change a number.
- `remove` lists courses or activities the student says are wrong, a mistake, or that they did not take or do,
  in their words or as course codes.
- Grades are letters only: A B C D F, W for a withdrawal, IP for a course in progress.
- `confirms_profile` is true only when the latest message clearly agrees that the summary Ann just read back is correct.
Course catalog:
{catalog}"""


IMAGES = {"image/jpeg", "image/png", "image/gif", "image/webp"}   # the picture types Claude reads
TEXT_FILES = (".txt", ".csv", ".json", ".md")


def transcribe(name, mime, data):
    """A voice note's words, from ElevenLabs Scribe (multipart upload: the model id and the file)."""
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\n{ELEVEN_STT}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name.replace(chr(34), "")}"\r\n'
            f"Content-Type: {mime}\r\n\r\n").encode() + base64.b64decode(data) + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=body, headers={
        "xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["text"].strip()


def read_attachments(attachments):
    """The student's files as Claude content blocks, their voice notes as words, and the names of files Ann can't open."""
    blocks, heard, skipped = [], [], []
    for a in attachments:   # dataUrl = "data:<mime>;base64,<data>"
        mime, _, data = a["dataUrl"].partition(";base64,")
        mime = mime.removeprefix("data:")
        if mime in IMAGES:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        elif mime == "application/pdf":
            blocks.append({"type": "document", "title": a["name"],
                           "source": {"type": "base64", "media_type": mime, "data": data}})
        elif (mime.startswith("text/") or mime == "application/json" or a["name"].lower().endswith(TEXT_FILES)) \
                and (text := base64.b64decode(data).decode("utf-8", "replace")).strip():
            blocks.append({"type": "document", "title": a["name"],
                           "source": {"type": "text", "media_type": "text/plain", "data": text}})
        elif mime.startswith("audio/") and os.getenv("ELEVENLABS_API_KEY"):
            heard.append(transcribe(a["name"], mime, data))
        else:   # Word files, unsupported pictures, audio without an ElevenLabs key
            skipped.append(a["name"])
    return blocks, " ".join(heard), skipped


def claude_extract(draft, messages, blocks, heard):
    """What's new in the student's latest turn, as fields that follow SCHEMA, and the model that answered."""
    lastAnn = next((m["text"] for m in reversed(messages[:-1]) if m["role"] == "ann"), "")
    said = " ".join(filter(None, [messages[-1]["text"], heard and f"(voice note) {heard}"]))
    prompt = (f"Known profile so far: {json.dumps(draft)}\nAnn's last message: {lastAnn}\n"
              f"Student's latest message: {said or '(no text, see attachments)'}")
    r = CLAUDE.beta.messages.create(
        model=CLAUDE_MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",   # a declined turn is re-run on Anthropic's pick
        output_config={"effort": CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        system=[{"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}],   # same catalog every turn
        messages=[{"role": "user", "content": blocks + [{"type": "text", "text": prompt}]}])
    if r.stop_reason in ("refusal", "max_tokens"):   # the JSON would be missing or cut off
        raise ValueError(f"Claude stopped early ({r.stop_reason})")
    return json.loads(next(b.text for b in r.content if b.type == "text")), r.model


COURSE_CODE = re.compile(r"\b([A-Za-z]{2,4})\s*-?\s*(\d{3}[A-Za-z]?)\b")
FILLER = {"class", "course", "the", "and", "for", "with", "intro", "introduction"}


def words_match(said, title):
    """At least half of the student's key words appear in the title (a word may be a prefix: "calc" -> "Calculus")."""
    words = {w for w in re.findall(r"[a-z]+", said.lower()) if len(w) > 2 and w not in FILLER}
    tw = re.findall(r"[a-z]+", title.lower())
    return bool(words) and sum(any(t == w or (len(w) >= 4 and t.startswith(w)) for t in tw) for w in words) / len(words) >= 0.5


def resolve_course(said, guess, titles):
    """Which catalog course the student meant, or None so Ann asks. Code decides, not the model:
    a course code the student said must exist exactly as said; otherwise the model's guess must share their key words."""
    code = COURSE_CODE.search(said or "")
    if code:
        cid = (code.group(1) + code.group(2)).upper()
        return cid if cid in titles else None
    return guess if guess in titles and words_match(said or "", titles[guess]) else None


def resolve(new, draft, titles=None):
    """Swap Claude's course guesses for code-checked ids; anything unresolved becomes a question for the student."""
    titles = titles or TITLES
    courses = []
    for c in new["courses"]:
        cid = resolve_course(c["said"], c.get("course_id"), titles)
        if cid:
            courses.append({"course_id": cid, "grade": c.get("grade")})
        elif c["said"] not in new["unclear"]:
            new["unclear"].append(c["said"])
    new["courses"] = courses
    # corrections: "that's wrong", "I never took IS 295", "I'm not a tutor"
    for r in new["remove"]:
        cid = resolve_course(r, None, titles) or next(
            (c["course_id"] for c in draft["courses"] if words_match(r, titles.get(c["course_id"], ""))), None)
        draft["courses"] = [c for c in draft["courses"] if c["course_id"] != cid]
        draft["experiences"] = [e for e in draft["experiences"] if not words_match(r, e["experience_type"])]
    return new


def selfcheck():
    """The rules that decide courses; run before the server starts and before every Vercel build."""
    t = {"CMSC201": "Foundations of Computer Science I", "CMSC341": "Data Structures", "IS295": "Business Communications for IS",
         "IS310": "Structured Systems Analysis and Design", "MATH151": "Calculus and Analytic Geometry I",
         "CMSC441": "Design and Analysis of Algorithms"}
    assert resolve_course("CMSC 201", None, t) == "CMSC201"
    assert resolve_course("is310", "IS295", t) == "IS310"               # the number the student said beats the model's guess
    assert resolve_course("IS 210", "IS295", t) is None                  # a course that doesn't exist is asked about, never swapped
    assert resolve_course("data structures class", "CMSC341", t) == "CMSC341"
    assert resolve_course("algorithms", "CMSC441", t) == "CMSC441"
    assert resolve_course("calc 1", "MATH151", t) == "MATH151"
    assert resolve_course("intro to programming", "CMSC201", t) is None  # no key word in common: ask instead of guessing
    draft = {"courses": [{"course_id": "IS295", "grade": "B"}, {"course_id": "IS310", "grade": "B"}],
             "experiences": [{"experience_type": "Tutoring"}, {"experience_type": "Internship"}]}
    new = resolve({"courses": [{"said": "IS 210", "course_id": "IS295", "grade": "B"}], "remove": ["IS 295", "I'm not a tutor"],
                   "unclear": []}, draft, t)
    assert new["courses"] == [] and new["unclear"] == ["IS 210"]
    assert draft["courses"] == [{"course_id": "IS310", "grade": "B"}] and draft["experiences"] == [{"experience_type": "Internship"}]


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
    """Only numbers from the tested pipeline: the advisor's own progress, projection and ranked levers,
    said in words and drawn in the charts that go with them."""
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
    text = (f"Thanks! Here's where you line up: compared with {p['peers_n']} current {s['major']} {s['class_level']}s, {standing}. "
            f"If nothing changes, alumni like you started about {abs(prem):,.0f} dollars "
            f"{'above' if prem >= 0 else 'below'} similar grads. My top moves for you: {moves}{risk} "
            "These are patterns from past alumni, not guarantees. Tell me if anything changes and I'll update your plan.")
    return text, [lineup_chart(s, p), moves_chart(rec), outlook_chart(f["projection"]["premium"], rec)]


# ---------------------------------------------------------------- the charts Ann sends (types in charts.js)

def lineup_chart(s, p):
    """Where you line up: the student's percentile among current peers on each measure progress() compares."""
    series = {"typical": 0, "behind": 1, "ahead": 2}   # the site's blue, orange and green; a context row (no score) is grey
    value = lambda metric, v: f"{v:.2f}" if metric == "GPA" else f"{float(v):g}"
    rows = [dict(label=m, pct=int(pct), you=value(m, you), median=value(m, med), status=status, s=series.get(status))
            for m, you, med, pct, status in p["vs_peers"].itertuples(index=False)]
    shown = {r["status"] for r in rows}
    legend = [dict(label=label, color=f"var(--s{series[k] + 1})") for k, label in
              [("behind", "Behind (under the 25th)"), ("typical", "Typical"), ("ahead", "Ahead (over the 75th)")] if k in shown]
    return dict(type="lineup", title="Where you line up", legend=legend, rows=rows,
                subtitle=f"Your percentile among {p['peers_n']} current {s['major']} {s['class_level'].lower()}s",
                note="Shaded: the middle half of your peers (25th to 75th percentile)."
                     + (" Work hours are context, not a score." if "context" in shown else ""))


def moves_chart(rec):
    """The recommended levers, by expected salary gain (the same three Ann reads out)."""
    rows = [dict(label=lever, value=round(float(gain)), sub=f"{-months:.1f} months faster to a first job" if months < -0.05 else None)
            for lever, gain, months in zip(rec["lever"], rec["salary gain ($)"], rec["months to first job"])]
    return dict(type="ranked", title="Your top moves", rowName="Move", valueName="Expected salary gain", valueFmt="signedUsdK1",
                subtitle="Expected change in first-job salary, learned from past alumni", rows=rows)


def outlook_chart(premium, rec):
    """The projection's 80% range for one person, as it is and moved by the recommended levers' combined gain."""
    gain = float(rec["salary gain ($)"].sum())
    row = lambda label, d: dict(label=label, value=round(float(premium["expected"]) + d),
                                lo=round(float(premium["low"]) + d), hi=round(float(premium["high"]) + d))
    return dict(type="range", title="Where you could start", rowName="Path", valueFmt="signedUsdK", rangeName="80% range",
                subtitle="First-job salary compared with similar grads (same major and graduation year)",
                ref=dict(label="Similar grads", value=0), rows=[row("If nothing changes", 0), row("With these moves", gain)],
                note="Dot: the expected salary. Line: the range 8 in 10 people like you land in. Patterns from past alumni, not guarantees.")


def reply_for(conversation, messages, attachments):
    init()
    started = time.time()
    draft = load_draft(conversation)
    before = json.dumps(draft, sort_keys=True)
    blocks, heard, skipped = read_attachments(attachments)
    new, model = claude_extract(draft, messages, blocks, heard)
    new["transcript"] = heard   # what next_reply reads back as "I heard: ..."
    merge(draft, resolve(new, draft))
    changed = json.dumps(draft, sort_keys=True) != before
    kind, reply = next_reply(draft, new, changed)
    reply, charts = reply if kind == "advice" else (reply, [])
    if skipped:
        reply = f"I can't open {', '.join(skipped)} yet, so I left {'it' if len(skipped) == 1 else 'them'} out. {reply}"
    save_draft(conversation, draft)
    known = sum(draft.get(k) is not None for k in REQUIRED) + len(draft["experiences"]) + len(draft["courses"])
    log_event(conversation, kind, started, model, any(a["type"].startswith("audio/") for a in attachments), known)
    return reply, charts


def next_reply(draft, new, changed):
    """What Ann says next, from the merged draft, and what kind of turn it was: ask, read back, or advise
    (advice comes as its text and the charts that go with it)."""
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
    prebuilt = os.path.join(HERE, "public", "enter-my-data.html")   # the Vercel bundle ships the chat page already built
    if os.path.exists(prebuilt):
        return open(prebuilt).read()
    raw = open(os.path.join(HERE, "page.html")).read()   # same assembly as the end of build.py
    head = raw.split('<header class="nav">')[0].split("</title>\n", 1)[1]   # enter.html has its own doctype, metas and title
    d0 = raw.index("<!-- Crayon collage filters.")
    defs = raw[d0:raw.index("</svg>", d0) + len("</svg>")]
    page = open(os.path.join(HERE, "enter.html")).read()
    for k, v in {"HEAD": head, "DEFS": defs, "CHARTS_JS": open(os.path.join(HERE, "charts.js")).read(),
                 "ADVISOR_JS": open(os.path.join(HERE, "advisor.js")).read()}.items():
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
            init()   # makes sure the tables exist on a fresh database
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
                reply, charts = reply_for(req["conversation"], req["messages"], req.get("attachments", []))
                self.send(200, json.dumps({"reply": reply, "charts": charts}).encode(), "application/json")
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
        except (urllib.error.URLError, anthropic.APIError, KeyError, ValueError) as e:   # the page shows its own "something went wrong" line
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
    selfcheck()
    init()
    port = int(os.getenv("ANN_PORT", "8000"))
    print(f"Advisor Ann is at http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
