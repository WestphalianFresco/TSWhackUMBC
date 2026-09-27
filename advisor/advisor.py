"""Student advisor pipeline: fit a new student's profile to the dataset, compare them with peers and alumni,
and recommend strategies backed only by relationships that passed significance tests.

Steps, one function each, run in order by advise():
  1. fit_student  validate the input against the dataset's vocabulary and compute the features the analysis uses
  2. progress     where the student stands vs current students in the same major and class level,
                  and how far they are from what high-earning alumni had by graduation
  3. future       projected job outcomes: regression trained on alumni + the 50 most similar alumni
  4. strategy     what-if levers (statistically supported only), ranked by expected salary gain

Usage:  python advisor/advisor.py [path/to/student.json]      (defaults to advisor/mock_student.json)
Data comes from Tiger Data when TIGER_URL is in .env, otherwise from the local CSVs.
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from dotenv import load_dotenv
from scipy import stats
from sqlalchemy import create_engine
from statsmodels.stats.proportion import proportion_confint

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FEATURES = ["internship_count", "final_gpa", "breadth", "credential_count"]   # the factors that survived the regression
GRADES = {"A", "B", "C", "D", "F", "W", "IP"}
CREDITS_PER_TERM = 15   # ponytail: assumes a full-time load; use the student's own pace if the input ever carries it


def load_tables():
    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.getenv("TIGER_URL")
    engine = create_engine(url.replace("postgres://", "postgresql+psycopg://", 1)) if url else None
    names = ["students_current", "alumni", "student_experience", "transcripts", "course_catalog"]
    if engine is None:
        return {n: pd.read_csv(os.path.join(ROOT, "data", f"{n}.csv"), na_values="Not Applicable") for n in names}
    return {n: pd.read_sql(f"SELECT * FROM {n} ORDER BY 1", engine) for n in names}


def class_level(credits):
    return "Freshman" if credits < 30 else "Sophomore" if credits < 60 else "Junior" if credits < 90 else "Senior"


def build():
    """Everything learned from the dataset once: features, models, tested levers. Returns a context dict."""
    t = load_tables()
    cur, alum, exp, tr, cat = (t[k] for k in ["students_current", "alumni", "student_experience", "transcripts", "course_catalog"])

    breadth = exp.groupby("campus_id")["experience_type"].nunique()
    cur["breadth"] = cur["campus_id"].map(breadth).fillna(0)
    alum["breadth"] = alum["campus_id"].map(breadth).fillna(0)
    sal = alum["first_job_annual_salary_usd"]
    alum["sal_adj"] = sal - sal.groupby([alum["major"], alum["graduation_year"]]).transform("median")
    graded = tr[tr["grade"] != "IP"]
    alum["dfw"] = alum["campus_id"].map(graded[graded["grade"].isin(["D", "F", "W"])].groupby("campus_id").size()).fillna(0)

    withSalary = alum[alum["sal_adj"].notna()]
    hired = alum[alum["months_to_first_job"].notna()]
    rhs = " + ".join(FEATURES)
    models = {"premium": smf.ols(f"sal_adj ~ {rhs}", data=withSalary).fit(),
              "months": smf.ols(f"months_to_first_job ~ {rhs}", data=hired).fit()}
    dfwYears = smf.ols("time_to_degree_years ~ dfw + C(degree_level) + C(entry_type)", data=alum).fit().params["dfw"]

    # experience types whose did-vs-didn't salary gap is significant (Mann-Whitney, Bonferroni over all types)
    types = sorted(exp["experience_type"].unique())
    payingTypes = []
    for tp in types:
        did = withSalary["campus_id"].isin(exp.loc[exp["experience_type"] == tp, "campus_id"])
        if stats.mannwhitneyu(withSalary["sal_adj"][did], withSalary["sal_adj"][~did]).pvalue * len(types) < 0.05:
            payingTypes.append(tp)

    # electives whose effect survives GPA, internships, credentials and track (Bonferroni over all electives tested)
    electives = cat[cat["course_type"] == "Elective"].set_index("course_id")["course_title"]
    takers = tr[tr["course_id"].isin(electives.index)].groupby("course_id")["campus_id"].agg(set)
    rows = []
    for major, d in withSalary.groupby("major"):
        for cid, ids in takers.items():
            took = d["campus_id"].isin(ids).astype(int)
            if 50 <= took.sum() <= len(d) - 50:
                f = smf.ols("sal_adj ~ took + final_gpa + internship_count + credential_count + C(track)",
                            data=d.assign(took=took)).fit()
                rows.append((major, cid, electives[cid], f.params["took"], f.pvalues["took"]))
    el = pd.DataFrame(rows, columns=["major", "course_id", "title", "effect", "p"])
    el = el[el["p"] * len(el) < 0.05]

    # companies whose return-offer rate is significantly above average (95% Wilson interval above the overall rate)
    work = exp[exp["experience_type"].isin(["Internship", "Co-op"])]
    offer = work["outcome"].isin(["Return offer accepted", "Return offer declined"])
    byCo = offer.groupby(work["organization"]).agg(["sum", "size"])
    byCo = byCo[byCo["size"] >= 30]
    byCo["rate"] = byCo["sum"] / byCo["size"]
    byCo["low"] = proportion_confint(byCo["sum"], byCo["size"], method="wilson")[0]
    offerCos = byCo[byCo["low"] > offer.mean()].sort_values("rate", ascending=False)

    core = graded.merge(cat[["course_id", "course_type", "course_title"]], on="course_id")
    core = core[core["course_type"] == "Core"]
    hardCore = (core["grade"].isin(["D", "F", "W"]).groupby(core["course_id"]).mean()
                .nlargest(6).rename("dfw_rate").to_frame().join(cat.set_index("course_id")["course_title"]))

    return dict(cur=cur, alum=alum, exp=exp, cat=cat, models=models, dfwYears=dfwYears, payingTypes=payingTypes,
                electives=el, offerCos=offerCos, overallOffer=offer.mean(), hardCore=hardCore,
                tracks=pd.concat([alum, cur]).groupby("major")["track"].agg(set).to_dict(), types=set(types))


# ---------------------------------------------------------------- step 1
def ask_grade(course_id):
    """Ask the student for a missing grade until the answer is a valid one."""
    while True:
        grade = input(f"The grade for {course_id} is missing. Enter A, B, C, D, F, W or IP: ").strip().upper()
        if grade in GRADES:
            return grade
        print(f"  {grade!r} is not a valid grade, please try again.")


def fit_student(profile, ctx, ask=ask_grade):
    """Validate the raw profile and turn it into the same features the analysis uses.
    A course without a grade is not guessed: `ask(course_id)` is called to get it (the keyboard by default)."""
    if profile["major"] not in ctx["tracks"]:
        raise ValueError(f"Unknown major {profile['major']!r}; expected one of {sorted(ctx['tracks'])}")
    if profile["track"] not in ctx["tracks"][profile["major"]]:
        raise ValueError(f"Unknown track {profile['track']!r} for {profile['major']}")
    exps = profile.get("experiences", [])
    for e in exps:
        if e["experience_type"] not in ctx["types"]:
            raise ValueError(f"Unknown experience_type {e['experience_type']!r}; expected one of {sorted(ctx['types'])}")
    courses = [dict(c) for c in profile.get("courses", [])]   # copies, so filling a grade never edits the caller's data
    known = set(ctx["cat"]["course_id"])
    for c in courses:
        if c["course_id"] not in known:
            raise ValueError(f"Unknown course_id {c['course_id']!r}")
        if not str(c.get("grade") or "").strip():   # no grade key, None, or blank
            c["grade"] = ask(c["course_id"])
        c["grade"] = c["grade"].strip().upper()
        if c["grade"] not in GRADES:
            raise ValueError(f"Bad grade {c['grade']!r} for {c['course_id']}")
    kinds = [e["experience_type"] for e in exps]
    credits = profile["credits_earned"]
    return {
        "major": profile["major"], "track": profile["track"],
        "class_level": class_level(credits),
        "credits_earned": credits, "credits_required": profile.get("credits_required", 120),
        "terms_left": math.ceil(max(profile.get("credits_required", 120) - credits, 0) / CREDITS_PER_TERM),
        "final_gpa": profile["cumulative_gpa"],   # named like the alumni column so the models can use it directly
        "internship_count": sum(k in ("Internship", "Co-op") for k in kinds),
        "credential_count": kinds.count("Certification"),
        "engagement_activity_count": sum(k not in ("Internship", "Co-op", "Certification") for k in kinds),
        "breadth": len(set(kinds)),
        "types_done": set(kinds),
        "dfw": sum(c["grade"] in ("D", "F", "W") for c in courses),
        "courses_taken": {c["course_id"] for c in courses if c["grade"] in ("A", "B", "C", "D", "IP")},
        "work_hours_per_week": profile.get("work_hours_per_week"),   # None when the student didn't say
    }


# ---------------------------------------------------------------- step 2
def progress(s, ctx):
    """Percentile vs current students in the same major and class level, plus the gap to high-earning alumni."""
    peers = ctx["cur"][(ctx["cur"]["major"] == s["major"]) & (ctx["cur"]["class_level"] == s["class_level"])]
    rows = []
    # work hours is context, not a score: more of it is neither better nor worse, so it gets no behind/ahead label
    for name, col, scored in [("GPA", "cumulative_gpa", True), ("Internships/co-ops", "internship_count", True),
                              ("Certifications", "credential_count", True), ("Other activities", "engagement_activity_count", True),
                              ("Experience types", "breadth", True), ("Work hours/week", "work_hours_per_week", False)]:
        key = "final_gpa" if col == "cumulative_gpa" else col
        if s[key] is None:   # not given: leave the row out rather than treat it as zero
            continue
        vals = peers[col].dropna()
        pct = stats.percentileofscore(vals, s[key], kind="mean")
        status = ("behind" if pct < 25 else "ahead" if pct > 75 else "typical") if scored else "context"
        rows.append((name, s[key], vals.median(), round(pct), status))
    vsPeers = pd.DataFrame(rows, columns=["metric", "student", "peer median", "percentile", "status"])

    alum = ctx["alum"][(ctx["alum"]["major"] == s["major"]) & ctx["alum"]["sal_adj"].notna()]
    top = alum[alum["sal_adj"] >= alum["sal_adj"].quantile(0.75)]
    target = pd.DataFrame({"student now": [s[f] for f in FEATURES],
                           "top-25% earners at graduation (median)": [top[f].median() for f in FEATURES]}, index=FEATURES)
    target["gap"] = (target.iloc[:, 1] - target.iloc[:, 0]).clip(lower=0)
    return {"peers_n": len(peers), "vs_peers": vsPeers, "vs_top_alumni": target}


# ---------------------------------------------------------------- step 3
def future(s, ctx, k=50):
    """Regression projection 'if nothing changes' + what the k most similar alumni went on to do."""
    x = pd.DataFrame([{f: s[f] for f in FEATURES}])
    proj = {}
    for name, m in ctx["models"].items():
        p = m.get_prediction(x).summary_frame(alpha=0.2).iloc[0]   # 80% prediction interval for one person
        proj[name] = {"expected": p["mean"], "low": p["obs_ci_lower"], "high": p["obs_ci_upper"]}

    # track is significantly tied to job family (chi-square p < 0.01 in both majors), so match within the same track
    alum = ctx["alum"][ctx["alum"]["major"] == s["major"]]
    sameTrack = alum[alum["track"] == s["track"]]
    pool = "same major and track" if len(sameTrack) >= 100 else "same major (too few alumni in this track)"
    alum = sameTrack if len(sameTrack) >= 100 else alum
    z = (alum[FEATURES] - alum[FEATURES].mean()) / alum[FEATURES].std()
    zs = (x.iloc[0] - alum[FEATURES].mean()) / alum[FEATURES].std()
    near = alum.loc[((z - zs) ** 2).sum(axis=1).nsmallest(k).index]
    responded = near[near["first_destination"] != "No Response"]
    hired = near[near["first_job_family"].notna()]
    top = lambda col: (hired[col].value_counts(normalize=True).head(3) * 100).round(0)
    familyPay = ctx["alum"].groupby("first_job_family")["sal_adj"].median()   # families differ in pay: Kruskal-Wallis p < 0.001
    families = top("first_job_family").to_frame("% of similar alumni").assign(
        **{"median premium in that family (all alumni)": lambda d: familyPay.reindex(d.index).round(0)})
    return {
        "projection": proj,
        "similar_alumni": {
            "n": len(near),
            "matched within": pool,
            "employed full-time %": round(float((responded["first_destination"] == "Employed Full-Time").mean()) * 100),
            "median premium ($)": round(float(near["sal_adj"].median())),
            "median months to first job": round(float(hired["months_to_first_job"].median()), 1),
        },
        "job_families": families,
        "industries": top("first_employer_industry"),
        "employers": top("first_employer"),
    }


# ---------------------------------------------------------------- step 4
def strategy(s, ctx):
    """What-if levers from the tested models, ranked by expected salary gain; plus risks and what not to bother with."""
    prem, months = ctx["models"]["premium"], ctx["models"]["months"]
    effect = lambda f, d: (prem.params[f] * d, months.params[f] * d if months.pvalues[f] < 0.05 else 0.0)
    levers = []
    if s["terms_left"] >= 1:
        levers.append(("internship", "Do one more internship or co-op", *effect("internship_count", 1), "regression, p < 0.001"))
    if s["final_gpa"] <= 3.8:
        levers.append(("gpa", "Raise GPA by 0.2", *effect("final_gpa", 0.2), "regression, p < 0.001"))
    levers.append(("certification", "Earn one certification", *effect("credential_count", 1), "regression, p < 0.001"))
    newTypes = [t for t in ctx["payingTypes"] if t not in s["types_done"] and t not in ("Internship", "Co-op", "Certification")]
    if newTypes:
        levers.append(("breadth", f"Add a new experience type ({', '.join(newTypes[:3])})", *effect("breadth", 1), "regression, p < 0.05"))
    for r in ctx["electives"][ctx["electives"]["major"] == s["major"]].itertuples():
        if r.course_id not in s["courses_taken"]:
            levers.append(("elective", f"Take elective {r.course_id} {r.title}", r.effect, 0.0, f"controlled OLS, p = {r.p:.0e}"))
    plan = (pd.DataFrame(levers, columns=["kind", "lever", "salary gain ($)", "months to first job", "evidence"])
            .sort_values("salary gain ($)", ascending=False, ignore_index=True).round(2))
    # electives were each estimated alone and their takers overlap, so adding several would double count:
    # the recommended plan takes the best lever of each kind, then the top 3
    recommended = plan.drop_duplicates("kind").head(3).reset_index(drop=True)

    risks = []
    hardLeft = ctx["hardCore"][~ctx["hardCore"].index.isin(s["courses_taken"])]
    if len(hardLeft):
        risks.append(f"Hardest core courses still ahead: {', '.join(hardLeft.index)}. Spread them across terms; "
                     f"each D/F/W adds about {ctx['dfwYears']:.2f} years to graduation.")
    if s["dfw"]:
        risks.append(f"{s['dfw']} D/F/W so far: roughly +{s['dfw'] * ctx['dfwYears']:.1f} years vs a clean record.")
    where = (f"Companies with return-offer rates significantly above the {ctx['overallOffer']:.0%} average: "
             + ", ".join(f"{c} ({r:.0%})" for c, r in ctx["offerCos"]["rate"].items())) if len(ctx["offerCos"]) else \
            (f"No company's return-offer rate is significantly above the {ctx['overallOffer']:.0%} average, "
             "so any internship is a good internship.")
    skip = ["Club leadership titles (no effect once internships and GPA are controlled)",
            "Hackathons, campus jobs, competitive teams (no significant salary gap)",
            "Switching track for pay (no significant difference between tracks)"]
    return {"plan": plan, "recommended": recommended, "risks": risks, "where_to_intern": where, "not_worth_it_for_pay": skip}


def advise(profile, ctx=None):
    ctx = ctx or build()
    s = fit_student(profile, ctx)
    return {"student": s, "progress": progress(s, ctx), "future": future(s, ctx), "strategy": strategy(s, ctx)}


def report(r):
    s, p, f, st = r["student"], r["progress"], r["future"], r["strategy"]
    print(f"== 1. Fit: {s['major']} / {s['track']}, {s['class_level']}, about {s['terms_left']} terms left")
    print(f"\n== 2. Progress vs {p['peers_n']} current {s['major']} {s['class_level']}s")
    print(p["vs_peers"].to_string(index=False))
    print("\n   Gap to what top-25% earning alumni had by graduation:")
    print(p["vs_top_alumni"].to_string())
    pr = f["projection"]
    print("\n== 3. Future if nothing changes (80% range for one person)")
    print(f"   salary vs similar grads: {pr['premium']['expected']:+,.0f} ({pr['premium']['low']:+,.0f} to {pr['premium']['high']:+,.0f})")
    print(f"   months to first job: {pr['months']['expected']:.1f} ({max(pr['months']['low'], 0):.1f} to {pr['months']['high']:.1f})")
    print("   The 50 most similar alumni: " + ", ".join(f"{k} {v}" for k, v in f["similar_alumni"].items()))
    print("   Where similar alumni went:"); print(f["job_families"].to_string())
    print("\n== 4. Strategy: every supported lever, ranked by expected salary gain")
    print(st["plan"].drop(columns="kind").to_string(index=False))
    rec = st["recommended"]
    print(f"\n   Recommended plan (best lever of each kind, top 3): {'; '.join(rec['lever'])}")
    print(f"   projected premium {pr['premium']['expected']:+,.0f} -> {pr['premium']['expected'] + rec['salary gain ($)'].sum():+,.0f}, "
          f"months to first job {pr['months']['expected']:.1f} -> {max(pr['months']['expected'] + rec['months to first job'].sum(), 0):.1f}")
    for line in st["risks"] + [st["where_to_intern"]]:
        print(f"   - {line}")
    print("   Not worth it for pay: " + "; ".join(st["not_worth_it_for_pay"]))


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_student.json")
    ctx = build()
    result = advise(json.load(open(path)), ctx)
    report(result)
    # self-check: the invariants every run must satisfy
    assert result["progress"]["vs_peers"]["percentile"].between(0, 100).all()
    assert (result["strategy"]["plan"]["salary gain ($)"] > 0).all()
    try:
        fit_student({**json.load(open(path)), "experiences": [{"experience_type": "Bowling"}]}, ctx)
        raise AssertionError("an unknown experience_type must be rejected")
    except ValueError:
        pass
    # a missing grade is asked for (here by a stand-in that answers "w"), never guessed
    asked = []
    s = fit_student({**json.load(open(path)), "courses": [{"course_id": "CMSC341"}, {"course_id": "CMSC201", "grade": ""}]},
                    ctx, ask=lambda cid: asked.append(cid) or "w")
    assert asked == ["CMSC341", "CMSC201"] and s["dfw"] == 2 and not s["courses_taken"]
    print("\nself-check passed")
