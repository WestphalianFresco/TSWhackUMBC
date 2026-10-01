"""Build the Success Metrics page from analysis.py.

    python site/build.py        ->  public/index.html  (+ public/enter-my-data.html)

Every number comes from the same compute_* results app.py shows. Each function
in CHARTS turns those results into a JSON spec for one chart type in charts.js;
page.html places it with {{CELL:<name>|<code shown in the cell>}}, optionally
followed by caption HTML and {{/CELL}} to put that text inside the chart's card. The finished
page is a single file: charts.js and the chart data are inlined. public/ is what Vercel serves
as static files, so commit the rebuilt pages.
"""
import base64
import json
import mimetypes
import os
import re
import sys

import numpy as np
import pandas as pd
from matplotlib import cbook

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import analysis as a  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "public", "index.html")
OUT_ENTER = os.path.join(os.path.dirname(HERE), "public", "enter-my-data.html")


def box(values):
    """Five-number summary with the same 1.5×IQR whiskers and fliers matplotlib draws."""
    values = np.asarray(pd.Series(values).dropna(), dtype=float)
    s = cbook.boxplot_stats(values)[0]
    r = lambda v: round(float(v))
    return dict(n=int(len(values)), q1=r(s["q1"]), med=r(s["med"]), q3=r(s["q3"]),
                lo=r(s["whislo"]), hi=r(s["whishi"]), fliers=[r(v) for v in s["fliers"]])


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- chart specs

CHARTS = {}


def chart(fn):
    CHARTS[fn.__name__] = fn
    return fn


@chart
def salary_by_major_degree(r):
    aci = r["majors"]["alumcareerinfo"]
    majors, degrees = sorted(aci["major"].unique()), sorted(aci["degree_level"].unique())
    return dict(type="box", title="Average salary per alum, by major and degree level", series=degrees,
                groupName="Major", groups=[dict(label=m, boxes=[
                    dict(s=i, **box(aci.loc[(aci["major"] == m) & (aci["degree_level"] == d), "avg_salary"]))
                    for i, d in enumerate(degrees)]) for m in majors])


@chart
def salary_trend(r):
    t = r["majors"]["trend"]
    years = sorted(int(y) for y in t["years_in"].unique())
    series = []
    for m in sorted(t["major"].unique()):
        s = t[t["major"] == m].set_index("years_in").reindex(years)
        col = lambda c: [None if pd.isna(v) else round(float(v)) for v in s[c]]
        series.append(dict(label=m, short="".join(w[0] for w in m.split()),
                           median=col("median"), p25=col("p25"), p75=col("p75")))
    return dict(type="line-band", title="Median alumni salary over their career", x=years, xName="Year",
                xLabel="Years since first job", series=series,
                note=f"Shaded band = middle 50% of alumni. Years with fewer than {a.MIN_N} alumni hidden.")


@chart
def salary_by_internships(r):
    di = r["intern"]["df_intern"]
    return dict(type="box", title="First-job salary by number of internships", xLabel="Internships completed",
                groupName="Internships", groups=[
                    dict(label=str(c), boxes=[dict(s=0, **box(di.loc[di["internship_count"] == c, "first_job_annual_salary_usd"]))])
                    for c in a._intern_counts(di)])


@chart
def time_to_hire(r):
    di = r["intern"]["df_intern"]
    series = []
    for c in a._intern_counts(di):
        m = np.sort(di.loc[di["internship_count"] == c, "months_to_first_job"].dropna().to_numpy())
        xs = np.unique(m)
        series.append(dict(label=plural(int(c), "internship"), x=[round(float(v), 2) for v in xs],
                           y=[round(float(v), 4) for v in np.searchsorted(m, xs, side="right") / len(m)]))
    return dict(type="step", title="Share of employed alumni hired by each month", xName="Month",
                xLabel="Months after graduation", series=series)


@chart
def return_offers(r):
    groups = [("no_offer", "No return offer"), ("offer", "Return offer")]
    pies = []
    for _, row in r["intern"]["offer_table"].iterrows():
        slices = [dict(s=i, count=int(row[f"count_{g}"]), median=float(row[f"median_{g}"])) for i, (g, _) in enumerate(groups)]
        pies.append(dict(title=f"{plural(int(row['internship_count']), 'internship')}  (n={sum(s['count'] for s in slices)})", slices=slices))
    return dict(type="pie", title="Median first-job salary: return offer vs. no return offer", series=[l for _, l in groups],
                pies=pies, minWidth=560,
                note="Slice size (and %) = share of alumni in each group. Dollar label = that group's median first-job salary.")


def first_job(r, summary_key, col, title, row_name):
    fj = r["first_job"]
    summary, career = fj[summary_key], fj["career"]
    return dict(type="hbox-diverge", title=title, rowName=row_name, minWidth=720,
                leftTitle="First-job salary", rightTitle="Now vs. peers (median)",
                rows=[dict(label=g, n=int(summary.loc[g, "alum_count"]), diff=round(float(summary.loc[g, "median_vs_peers"])),
                           box=box(career.loc[career[col] == g, "first_salary"])) for g in summary.index],
                note=f"Groups with fewer than {a.MIN_N} alumni hidden. Right panel: 0 = typical alum with the same years of experience.")


@chart
def first_job_family(r):
    return first_job(r, "family_summary", "first_family", "Outcomes by first job family", "First job family")


@chart
def first_job_industry(r):
    return first_job(r, "industry_summary", "first_industry", "Outcomes by first employer industry", "First employer industry")


@chart
def unemployment(r):
    u = r["unemp"]
    bm = u["by_major"]
    return dict(type="bar", title="Share of alumni still seeking work after graduation", xName="Internships:",
                xLabel="Internships completed", valueFmt="pct1",
                bars=[dict(label=str(i), value=round(float(row["rate"]), 4), n=int(row["alum_count"])) for i, row in u["unemp"].iterrows()],
                ref=dict(value=round(float(u["overall_rate"]), 4), label="All alumni"),
                note="Among alumni in the labor force (employed, military, or still seeking); excludes grad school, no response, "
                     f"and 2026 grads. By major: CS {bm['Computer Science']:.1%}, IS {bm['Information Systems']:.1%}.")


@chart
def experience_share(r):
    s = r["activities"]["pct_curr"].sort_values(ascending=False)
    return dict(type="hbar", title="Share of current students with each experience type", rowName="Experience type",
                xLabel="% of current students", valueFmt="pct0",
                rows=[dict(label=k, value=round(float(v) / 100, 4)) for k, v in s.items()])


@chart
def top_orgs(r):
    act = r["activities"]
    base = act["baseline"]
    return dict(type="hbar", title="Top 10 organizations by alumni first-job salary", rowName="Organization",
                valueFmt="signedUsdK1", xFmt="usdK",
                xLabel=f"Median first-job salary above the all-alumni median (${base:,.0f})",
                rows=[dict(label=o, n=int(row["n"]), value=round(float(row["median_salary"] - base)))
                      for o, row in act["top10"].iterrows()])


# ---------------------------------------------------------------- Student Life charts (dataset_analysis.ipynb, Student Activities)
# Same logic as the notebook's cells, computed from the CSVs. The notebook reads two of them (share of
# participation by year, entry salary heatmap) from Tiger Data continuous aggregates; here the same
# aggregates are recomputed: experience_yearly counts activities per year of the term, and
# salary_by_year takes the entry-level median per year the job started (exact here, approximate in Tiger).

S1_LIGHT = "color-mix(in srgb, var(--s1) 45%, var(--surface))"
GREY = "color-mix(in srgb, var(--muted) 30%, var(--surface))"


def life_frames(r):
    d = r["dfs"]
    se = d["student_experience"]
    year = se["term"].str[-4:].astype(int)
    period = pd.cut(year, [2017, 2020, 2023, 2026], labels=["2018–20", "2021–23", "2024–26"])
    org = se["organization"].where(se["organization"] != "UMBC", se["experience_name"])   # clubs list "UMBC"; use the club
    ids = pd.concat([d["students_current"]["campus_id"], d["alumni"]["campus_id"]])
    breadth = se.groupby("campus_id")["experience_type"].nunique().reindex(ids, fill_value=0)
    return se, year, period, org, breadth


@chart
def life_experience_share(r):
    se = r["dfs"]["student_experience"]; sc = r["dfs"]["students_current"]
    pct = se[se["campus_id"].isin(sc["campus_id"])].groupby("experience_type")["campus_id"].nunique() / len(sc)
    return dict(type="hbar", title="Share of current students with each experience type", rowName="Experience type",
                valueFmt="pct0", xLabel="% of current students",
                rows=[dict(label=k, value=round(float(v), 4)) for k, v in pct.sort_values(ascending=False).items()])


@chart
def life_breadth(r):
    breadth = life_frames(r)[4]
    counts = breadth.value_counts().reindex(range(11), fill_value=0)
    return dict(type="bar", title=f"{breadth.between(2, 4).mean():.0%} of students try 2–4 types of experience",
                xName="Types tried:", valueFmt="int", xLabel="Number of different experience types (out of 10)",
                bars=[dict(label=str(k), value=int(v)) for k, v in counts.items()])


@chart
def life_type_change(r):
    se, year, period, org, _ = life_frames(r)
    joins = pd.DataFrame({"type": se["experience_type"], "period": period, "campus_id": se["campus_id"]}).drop_duplicates()
    share = pd.crosstab(joins["type"], joins["period"], normalize="columns") * 100
    change = (share["2024–26"] - share["2018–20"]).sort_values(ascending=False)
    return dict(type="hbar", title="Experience types gaining or losing ground, 2018–20 → 2024–26", rowName="Experience type",
                valueFmt="pts1", xFmt="signed0", xLabel="Change in share of all participation (percentage points)",
                rows=[dict(label=k, value=round(float(v), 2)) for k, v in change.items()])


@chart
def life_share_by_year(r):
    se, year, *_ = life_frames(r)
    recent = year >= 2013
    counts = se[recent].groupby([year[recent], se.loc[recent, "experience_type"]]).size().unstack(fill_value=0)
    share = counts.div(counts.sum(axis=1), axis=0) * 100
    highlight = {"Internship": 0, "Certification": 1}
    series = [dict(label=t, s=highlight.get(t), values=[round(float(v), 2) for v in share[t]]) for t in share.columns]
    series.sort(key=lambda s: s["s"] is not None)   # grey lines first, highlighted drawn on top
    return dict(type="lines", title="Internships are shrinking while certifications grow", x=[int(y) for y in share.index],
                xName="Year", yFmt="pctRaw0", tableFmt="pctRaw1", otherLabel="Other 8 types", series=series,
                xLabel="Year of the term", note="Share of all activity records that year.",
                legend=[dict(label="Internship", color="var(--s1)"), dict(label="Certification", color="var(--s2)"),
                        dict(label="Other 8 types", color=GREY)])


@chart
def life_top_people(r):
    se, _, _, org, _ = life_frames(r)
    people = se.groupby(org).agg(people=("campus_id", "nunique"), kind=("experience_type", lambda s: " / ".join(s.unique())))
    top = people.nlargest(10, "people")
    return dict(type="hbar", style="lollipop", minWidth=640, title="Top 10 organizations by number of people who joined", rowName="Organization",
                valueFmt="int", xLabel="People who joined (current students + alumni)",
                rows=[dict(label=o, sub=row["kind"], value=int(row["people"])) for o, row in top.iterrows()])


@chart
def life_fastest_growing(r):
    se, _, period, org, _ = life_frames(r)
    joins = pd.DataFrame({"org": org, "period": period, "campus_id": se["campus_id"]}).drop_duplicates()
    share = pd.crosstab(joins["org"], joins["period"], normalize="columns")[["2018–20", "2024–26"]] * 100
    share["change"] = share["2024–26"] - share["2018–20"]
    top = share.nlargest(10, "change")
    return dict(type="dumbbell", minWidth=460, title="Top 10 fastest-growing organizations", rowName="Organization", series=["2018–20", "2024–26"],
                xFmt="pctRaw1", diffFmt="pts2", xLabel="% of all participation in the period",
                legend=[dict(label="2018–20", color=S1_LIGHT), dict(label="2024–26", color="var(--s1)")],
                rows=[dict(label=o, **{"from": round(float(row["2018–20"]), 3), "to": round(float(row["2024–26"]), 3)}) for o, row in top.iterrows()])


@chart
def life_club_retention(r):
    se = r["dfs"]["student_experience"]
    clubs = se[se["experience_type"] == "Student Organization"]
    stayed = (clubs["outcome"] != "Left after one term").groupby(clubs["experience_name"]).mean() * 100
    return dict(type="stack100", minWidth=460, title="Top 10 student clubs by member retention", rowName="Club",
                series=["Stayed past first term", "Left after one term"], xLabel="% of members",
                legend=[dict(label="Stayed past first term", color="var(--s1)"), dict(label="Left after one term", color=GREY)],
                rows=[dict(label=k, value=round(float(v), 2)) for k, v in stayed.nlargest(10).items()])


@chart
def life_leadership(r):
    se, _, _, org, _ = life_frames(r)
    groups = se["experience_type"].isin(["Student Organization", "Competitive Team"])
    is_leader = se.loc[groups, "role_level"].isin(["Officer", "President", "Lead"])
    rate = is_leader.groupby(org[groups]).mean()
    return dict(type="bar", minWidth=640, title="Top 10 clubs and teams where members most often lead", xName="", valueFmt="pct0",
                ref=dict(value=round(float(is_leader.mean()), 4), label="All clubs and teams:"),
                xLabel="% of members who held a leadership role (Officer, President or Lead)",
                bars=[dict(label=k, value=round(float(v), 4)) for k, v in rate.nlargest(10).items()])


@chart
def life_first_gen(r):
    se, _, _, org, _ = life_frames(r)
    sc = r["dfs"]["students_current"]
    cur = (se.assign(org=org)[se["campus_id"].isin(sc["campus_id"])].drop_duplicates(["campus_id", "org"])
           .merge(sc[["campus_id", "is_first_generation"]], on="campus_id"))
    rate = cur.groupby("org")["is_first_generation"].agg(pct="mean", members="size")
    rate["pct"] *= 100
    top = rate[rate["members"] >= 50].nlargest(10, "pct")
    ci = 1.96 * np.sqrt(top["pct"] * (100 - top["pct"]) / top["members"])
    return dict(type="forest", minWidth=640, title="Top 10 organizations by share of first-generation members", rowName="Organization",
                valueFmt="pctRaw0", ref=dict(value=round(float(sc["is_first_generation"].mean() * 100), 2), label="All current students"),
                xLabel="% of members who are first-generation college students",
                note="Current students, organizations with at least 50 current members. Lines are 95% confidence intervals; one that crosses the campus-wide line could be chance.",
                rows=[dict(label=o, n=int(row["members"]), value=round(float(row["pct"]), 2),
                           lo=round(float(row["pct"] - ci[o]), 2), hi=round(float(row["pct"] + ci[o]), 2)) for o, row in top.iterrows()])


@chart
def life_return_offer(r):
    se = r["dfs"]["student_experience"]
    work = se[se["experience_type"].isin(["Internship", "Co-op"])]
    got = work["outcome"].isin(["Return offer accepted", "Return offer declined"])
    rate = got.groupby(work["organization"]).agg(["mean", "size"])
    top = rate[rate["size"] >= 30].nlargest(10, "mean")
    return dict(type="forest", labels=True, minWidth=460, title="Top 10 companies by return-offer rate", rowName="Company", valueFmt="pctRaw0",
                ref=dict(value=round(float(got.mean() * 100), 2), label="All companies"),
                xLabel="% of internships/co-ops ending in a return offer",
                note="Companies with at least 30 internship or co-op records.",
                rows=[dict(label=o, n=int(row["size"]), value=round(float(row["mean"] * 100), 2)) for o, row in top.iterrows()])


@chart
def life_salary_premium(r):
    se, al = r["dfs"]["student_experience"], r["dfs"]["alumni"]
    sal = al["first_job_annual_salary_usd"]
    adj = sal - sal.groupby([al["major"], al["graduation_year"]]).transform("median")
    m = (se[se["campus_id"].isin(al["campus_id"])].drop_duplicates(["campus_id", "organization"])
         .merge(al[["campus_id"]].assign(adj=adj), on="campus_id"))
    rank = m.groupby("organization").agg(n=("adj", "count"), premium=("adj", "median"))
    top = rank[rank["n"] >= 30].nlargest(10, "premium")
    return dict(type="hbar", minWidth=460, title="Top 10 organizations by alumni first-job salary premium", rowName="Organization",
                valueFmt="signedUsdK1", xFmt="usdK", xLabel="Salary above similar grads (same major and year)",
                rows=[dict(label=o, n=int(row["n"]), value=round(float(row["premium"]))) for o, row in top.iterrows()])


@chart
def life_breadth_salary(r):
    al = r["dfs"]["alumni"]
    breadth = life_frames(r)[4]
    sal = al["first_job_annual_salary_usd"]
    adj = sal - sal.groupby([al["major"], al["graduation_year"]]).transform("median")
    grp = al["campus_id"].map(breadth)
    groups = []
    for k in range(11):
        vals = adj[(grp == k) & adj.notna()]
        groups.append(dict(label=str(k), boxes=[dict(s=0, **box(vals))] if len(vals) else []))
    return dict(type="box", minWidth=560, title="Alumni with more types of experience earn more than similar grads", groupName="Types tried",
                yFmt="signedUsdK", fliers=False, zero=True, medianLabels=False, groups=groups,
                xLabel="Number of different experience types (first-job salary vs grads with the same major and year)")


@chart
def life_entry_salary(r):
    eh = r["dfs"]["employment_history"]
    entry = eh[eh["seniority_level"] == "Entry"].assign(year=pd.to_datetime(eh["start_date"]).dt.year)
    med = entry.groupby(["job_family", "year"])["annual_salary_usd"].median().unstack()
    med = med.loc[med.mean(axis=1).sort_values(ascending=False).index]   # highest-paid family on top
    return dict(type="heatmap", minWidth=640, title="Median entry-level salary by job family (nominal $)", rowName="First job family",
                valueFmt="usdK", valueName="Median entry salary", rows=list(med.index), cols=[str(c) for c in med.columns],
                values=[[None if pd.isna(v) else round(float(v)) for v in row] for row in med.to_numpy()],
                xLabel="Year the job started")


# ---------------------------------------------------------------- Job Trajectory charts (job_hunting.ipynb)
# Alumni only: first-job facts from alumni, everything after the first job from employment_history.

def job_frames(r):
    d = r["dfs"]
    al = d["alumni"]
    jobs = d["employment_history"].assign(start_date=lambda j: pd.to_datetime(j["start_date"])).sort_values(["campus_id", "start_date"])
    hired = al[al["first_employer"].notna()]   # alumni who took a first job
    first = jobs[jobs["change_type"] == "First Job"]
    first = first.assign(adjusted=first["annual_salary_usd"] * 100 / first["cost_of_living_index"])
    raise_pct = (jobs["annual_salary_usd"] / jobs.groupby("campus_id")["annual_salary_usd"].shift() - 1) * 100
    return al, jobs, hired, first, raise_pct


@chart
def job_destinations(r):
    al = r["dfs"]["alumni"]
    groups = {"Employed Full-Time": "Employed full-time", "Employed Part-Time": "Part-time or military",
              "Military": "Part-time or military", "Continuing Education": "Grad school", "Still Seeking": "Still seeking"}
    order = [("Employed full-time", 0), ("Part-time or military", 3), ("Grad school", 2), ("Still seeking", 1)]   # bottom band first
    responded = al[al["first_destination"] != "No Response"]
    mix = pd.crosstab(responded["graduation_year"], responded["first_destination"].map(groups), normalize="index") * 100
    return dict(type="area100", title="Most grads are working 6 months out; 2020 was the hardest year", xName="Class of",
                x=[int(y) for y in mix.index], xLabel="Graduation year",
                series=[dict(label=name, s=s, values=[round(float(v), 2) for v in mix[name]]) for name, s in order],
                legend=[dict(label=name, color=f"var(--s{s + 1})") for name, s in order],
                note="What alumni were doing about six months after graduating, among those who answered. 2026 grads were asked soon after graduating, so “still seeking” runs high.")


@chart
def job_time_to_hire(r):
    hired = job_frames(r)[2]
    months = np.sort(hired["months_to_first_job"].dropna().to_numpy())
    xs = np.unique(months)
    at_grad = (months <= 0).mean()
    marks = [dict(x=0, y=round(float(at_grad), 4), label=f"{at_grad:.0%} start right at graduation")]
    for q in (0.5, 0.75, 0.9):
        v = float(np.quantile(months, q))
        marks.append(dict(x=round(v, 2), y=q, label=f"{q:.0%} started by {v:.1f} months"))
    return dict(type="step", title="Half of grads start their first job within a month", xName="Month",
                xLabel="Months from graduation to first job", marks=marks,
                series=[dict(label="Hired alumni", x=[round(float(v), 2) for v in xs],
                             y=[round(float(v), 4) for v in np.searchsorted(months, xs, side="right") / len(months)])])


@chart
def job_channels(r):
    hired = job_frames(r)[2]
    by = hired.groupby("first_job_found_via").agg(share=("campus_id", "size"), months=("months_to_first_job", "median"))
    by["share"] = by["share"] / len(hired) * 100
    return dict(type="hbar", minWidth=460, title="Return offers are the most common route and by far the fastest", rowName="How they found it",
                valueFmt="pctRaw0", xLabel="% of first jobs  (label: share · median months to start)",
                rows=[dict(label=c, value=round(float(row["share"]), 2), hl=c == "Return Offer from Internship",
                           text=f"{row['share']:.0f}% · {row['months']:.1f} mo") for c, row in by.sort_values("share", ascending=False).iterrows()])


@chart
def job_industries(r):
    hired = job_frames(r)[2]
    federal = ["Federal Contracting", "Federal Government", "Defense & Aerospace"]
    share = hired["first_employer_industry"].value_counts(normalize=True)
    share = pd.concat([share.head(9), pd.Series({"Other": share.iloc[9:].sum()})])
    return dict(type="bar", minWidth=640, title=f"{share[federal].sum():.0%} of first jobs are in the federal ecosystem (blue)",
                xName="", valueFmt="pct0",
                bars=[dict(label=k, value=round(float(v), 4), hl=k in federal) for k, v in share.items()],
                note="Blue: federal contracting, federal government, and defense & aerospace.")


@chart
def job_regions(r):
    first = job_frames(r)[3]
    by = first.groupby("region").agg(n=("job_id", "size"), paycheck=("annual_salary_usd", "median"), adjusted=("adjusted", "median"))
    by = by[by["n"] >= 100].sort_values("adjusted", ascending=False)
    return dict(type="dumbbell", minWidth=460, title="DC pays more on paper; after cost of living, remote pays most and Baltimore beats DC",
                rowName="Region", series=["Paycheck", "Adjusted for cost of living"], xFmt="usdK1", pointLabel="to", fromZero=False,
                legend=[dict(label="Paycheck", color=S1_LIGHT), dict(label="Adjusted for cost of living", color="var(--s1)")],
                xLabel="Median first-job salary",
                rows=[dict(label=k, n=int(row["n"]), **{"from": round(float(row["paycheck"])), "to": round(float(row["adjusted"]))}) for k, row in by.iterrows()],
                note="Adjusted = salary × 100 ÷ cost-of-living index (100 = national average; Baltimore is exactly 100). Regions with at least 100 first jobs.")


@chart
def job_raises(r):
    _, jobs, _, _, raise_pct = job_frames(r)
    up = ["Internal Promotion", "New Employer - Advance"]
    by = raise_pct.groupby(jobs["change_type"]).agg(["median", "size"]).dropna().sort_values("median", ascending=False)
    return dict(type="hbar", style="lollipop", minWidth=460, title="Move up, not sideways: step-up moves pay about 20%, lateral ones about 4%",
                rowName="Type of move", valueFmt="pctRaw0", xLabel="Median raise vs previous job (%)",
                rows=[dict(label=k, n=int(row["size"]), value=round(float(row["median"]), 2), hl=k in up, text=f"+{row['median']:.0f}%")
                      for k, row in by.iterrows()])


@chart
def job_ladder(r):
    _, jobs, *_ = job_frames(r)
    first_start = jobs[jobs["change_type"] == "First Job"].set_index("campus_id")["start_date"]
    years_in = ((jobs["start_date"] - jobs["campus_id"].map(first_start)).dt.days / 365.25).round().clip(upper=8).astype(int)
    level = jobs["seniority_level"].replace({"Lead": "Lead+", "Manager": "Lead+", "Director": "Lead+"})
    levels = ["Entry", "Mid", "Senior", "Lead+"]
    colors = ["#cde2fb", "#86b6ef", "#2a78d6", "#0d366b"]   # light to dark = junior to senior
    ladder = pd.crosstab(years_in, level, normalize="index")[levels] * 100
    senior5 = ladder.loc[5, "Senior"] + ladder.loc[5, "Lead+"]
    return dict(type="stackcols", title=f"Five years in, {senior5:.0f}% of new roles are Senior or higher", xName="Year",
                levels=levels, colors=colors, xLabel="Years after the first job started (share of the jobs started that year)",
                legend=[dict(label=l, color=c) for l, c in reversed(list(zip(levels, colors)))],
                cols=[dict(label=f"{y}+" if y == 8 else str(y), values=[round(float(v), 2) for v in row]) for y, row in ladder.iterrows()])


def job_checks_table(r):
    """The notebook's checks behind each chart title: chi-square for rates, Mann-Whitney for skewed money and time gaps."""
    from scipy import stats
    al, jobs, hired, first, raise_pct = job_frames(r)
    mw = lambda x, y: stats.mannwhitneyu(x.dropna(), y.dropna()).pvalue
    responded = al[al["first_destination"] != "No Response"]
    full_time = responded["first_destination"] == "Employed Full-Time"
    via_offer = hired["first_job_found_via"] == "Return Offer from Internship"
    pay = {k: g for k, g in first.groupby("region")}
    up = jobs["change_type"].isin(["Internal Promotion", "New Employer - Advance"])
    lateral = jobs["change_type"].isin(["Internal Lateral", "New Employer - Lateral"])
    tests = [
        ("2020 grads were less often employed full-time", "Chi-square",
         stats.chi2_contingency(pd.crosstab(responded["graduation_year"] == 2020, full_time)).pvalue),
        ("Return offers start faster than other routes", "Mann-Whitney",
         mw(hired["months_to_first_job"][via_offer], hired["months_to_first_job"][~via_offer])),
        ("DC paychecks are higher than Baltimore's", "Mann-Whitney",
         mw(pay["Washington, DC Metro"]["annual_salary_usd"], pay["Baltimore, MD"]["annual_salary_usd"])),
        ("After cost of living, remote beats Baltimore", "Mann-Whitney ×4", 4 * mw(pay["Remote - United States"]["adjusted"], pay["Baltimore, MD"]["adjusted"])),
    ] + [(f"After cost of living, Baltimore beats {k}", "Mann-Whitney ×4", 4 * mw(pay["Baltimore, MD"]["adjusted"], pay[k]["adjusted"]))
         for k in ["Washington, DC Metro", "Northern Virginia", "Columbia/Howard County, MD"]] + [
        ("Step-up moves raise pay more than lateral ones", "Mann-Whitney", mw(raise_pct[up], raise_pct[lateral])),
    ]
    body = "".join(f"<tr><td>{c}</td><td>{t}</td><td class=num>{min(p, 1):.1e}</td><td>{'Supported' if p < 0.05 else 'Not significant'}</td></tr>"
                   for c, t, p in tests)
    return ('<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Test</th><th class=num>p-value</th><th>Verdict</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


# ---------------------------------------------------------------- Strategies charts (strategies.ipynb)
# Alumni only: they have both campus records and job outcomes. sal_adj = first-job salary minus the median
# of grads with the same major and graduation year. Three of the notebook's charts are the Student Life
# ones (return-offer rate, salary premium by organization, salary by experience types) and reuse those specs.

def strat_frames(r):
    d = r["dfs"]
    se = d["student_experience"]
    al = d["alumni"].copy()
    sal = al["first_job_annual_salary_usd"]
    al["sal_adj"] = sal - sal.groupby([al["major"], al["graduation_year"]]).transform("median")
    breadth = life_frames(r)[4]
    clubs = se[se["experience_type"].isin(["Student Organization", "Competitive Team"])]
    leaders = clubs.loc[clubs["role_level"].isin(["Officer", "President", "Lead"]), "campus_id"]
    al["role"] = np.select([al["campus_id"].isin(leaders), al["campus_id"].isin(clubs["campus_id"])], ["Club leader", "Member only"], "No club")
    al["breadth"] = al["campus_id"].map(breadth)
    return se, al


@chart
def strat_internships(r):
    _, al = strat_frames(r)
    al = al.assign(nIntern=al["internship_count"].clip(upper=3).map({0: "0", 1: "1", 2: "2", 3: "3+"}))
    responded = al[al["first_destination"] != "No Response"]
    employed = (responded["first_destination"] == "Employed Full-Time").groupby(responded["nIntern"]).mean() * 100
    months = al.groupby("nIntern")["months_to_first_job"].median()
    premium = al.groupby("nIntern")["sal_adj"].median()
    panel = lambda title, fmt, s: dict(title=title, fmt=fmt, bars=[dict(label=k, value=round(float(v), 2)) for k, v in s.items()])
    return dict(type="multibar", minWidth=640, title="More internships: more likely hired, hired faster, paid more", xName="Internships/co-ops:",
                xLabel="Internships/co-ops",
                panels=[panel("Employed full-time", "pctRaw0", employed), panel("Months to first job (median)", "mo1", months),
                        panel("Salary vs similar grads (median)", "signedUsdK1", premium)])


@chart
def strat_channels(r):
    _, al = strat_frames(r)
    by = al.groupby("first_job_found_via").agg(premium=("sal_adj", "median"), n=("sal_adj", "count")).sort_values("premium", ascending=False)
    return dict(type="hbar", minWidth=460, title="Return offers are the only job route that pays above similar grads", rowName="How they found it",
                valueFmt="signedUsdK1", xFmt="signedUsdK", xLabel="Median first-job salary vs similar grads",
                rows=[dict(label=k, n=int(row["n"]), value=round(float(row["premium"]))) for k, row in by.iterrows()])


@chart
def strat_activity_payoff(r):
    from scipy import stats
    se, al = strat_frames(r)
    with_salary = al[al["sal_adj"].notna()]
    n_types = se["experience_type"].nunique()
    side = {"Co-op": "left", "Tutoring": "right", "Peer Mentor": "left", "Competitive Team": "right"}   # a crowded corner: these labels go beside the bubble
    points = []
    for t, grp in se.groupby("experience_type"):
        did = with_salary["campus_id"].isin(grp["campus_id"])
        gap = with_salary.loc[did, "sal_adj"].median() - with_salary.loc[~did, "sal_adj"].median()
        p = min(1.0, stats.mannwhitneyu(with_salary.loc[did, "sal_adj"], with_salary.loc[~did, "sal_adj"]).pvalue * n_types)   # Bonferroni
        points.append(dict(label=t.replace("Undergraduate ", ""), x=round(float(did.mean() * 100), 2), y=round(float(gap)),
                           n=int(did.sum()), p=float(f"{p:.3g}"), hl=bool(p < 0.05), side=side.get(t, "top")))
    return dict(type="bubble", minWidth=560, title="Internships pay off most; hackathons and campus jobs barely move pay", rowName="Activity type",
                xName="Alumni who did it", yName="Salary gap", xFmt="pctRaw0", yFmt="signedUsdK", xLabel="% of alumni who did it", points=points,
                legend=[dict(label="Significant gap (p < 0.05)", color="var(--s1)"), dict(label="Could be chance", color=GREY)],
                note="Gap = median salary vs similar grads for alumni who did it, minus for those who didn't. Mann-Whitney, Bonferroni-corrected for 10 types. Bubble area = alumni who did it.")


@chart
def strat_clubs(r):
    _, al = strat_frames(r)
    by = al.groupby("role").agg(premium=("sal_adj", "median"), months=("months_to_first_job", "median"), n=("campus_id", "size"))
    by = by.loc[["No club", "Member only", "Club leader"]]
    return dict(type="dotline", minWidth=460, title="Club leaders earn more, but it's their internships and GPA, not the title", rowName="Club involvement",
                yName="Salary vs similar grads", yFmt="signedUsdK1",
                points=[dict(label=k, n=int(row["n"]), value=round(float(row["premium"])), sub=f"{row['months']:.1f} months to first job") for k, row in by.iterrows()])


@chart
def strat_gpa(r):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _, al = strat_frames(r)
    w = al[al["sal_adj"].notna()]
    x, y = w["final_gpa"].to_numpy(float), w["sal_adj"].to_numpy(float)
    fig, ax = plt.subplots()
    hb = ax.hexbin(x, y, gridsize=25, mincnt=1)   # matplotlib's own grid, so the hexagons match the notebook
    offsets, counts = hb.get_offsets(), hb.get_array()
    plt.close(fig)
    nx = 25; ny = int(nx / np.sqrt(3))
    sx, sy = (x.max() - x.min()) / nx, (y.max() - y.min()) / ny
    bands = pd.cut(w["final_gpa"], np.arange(np.floor(w["final_gpa"].min() * 4) / 4, 4.25, 0.25))
    by = w.groupby(bands, observed=True)["sal_adj"].agg(["median", "size"])
    by = by[by["size"] >= 20]   # thin low-GPA bands are just noise
    return dict(type="hexbin", minWidth=560, title="Higher GPA, higher starting pay", xName="GPA band", lineName="Median per 0.25 GPA",
                xFmt="num1", yFmt="signedUsdK", xLabel="Final GPA", sx=float(sx), sy=float(sy),
                xMin=float(x.min() - sx / 2), xMax=float(x.max() + sx / 2), yMin=float(y.min() - sy * 2 / 3), yMax=float(y.max() + sy * 2 / 3),
                hexes=[dict(x=round(float(ox), 4), y=round(float(oy)), c=int(c)) for (ox, oy), c in zip(offsets, counts)],
                line=[dict(x=round(float(b.mid), 3), y=round(float(row["median"])), n=int(row["size"]), label=f"GPA {b.left:.2f}–{b.right:.2f}") for b, row in by.iterrows()],
                legend=[dict(label="Median per 0.25 GPA", color="var(--s2)"), dict(label="Alumni per hexagon (darker = more)", color="#2a78d6")],
                note="Bands with at least 20 alumni.")


def grade_frames(r):
    """Graded transcript rows (IP left out) and the alumni frame with each person's count of D, F and W grades."""
    tr = r["dfs"]["transcripts"]
    graded = tr[tr["grade"] != "IP"]
    _, al = strat_frames(r)
    al["dfw"] = al["campus_id"].map(graded[graded["grade"].isin(["D", "F", "W"])].groupby("campus_id").size()).fillna(0).astype(int)
    return graded, al


@chart
def strat_dfw(r):
    _, al = grade_frames(r)
    al = al.assign(nDfw=al["dfw"].clip(upper=4).map({0: "0", 1: "1", 2: "2", 3: "3", 4: "4+"}))
    years = al.groupby("nDfw")["time_to_degree_years"].mean()
    premium = al.groupby("nDfw")["sal_adj"].median()
    panel = lambda title, fmt, s: dict(title=title, fmt=fmt, bars=[dict(label=k, value=round(float(v), 2)) for k, v in s.items()])
    return dict(type="multibar", minWidth=460, title="Each D, F or W slows the degree", xName="D/F/W grades:", xLabel="D, F and W grades on the transcript",
                panels=[panel("Years to degree (mean)", "yr1", years), panel("Salary vs similar grads (median)", "signedUsdK1", premium)])


@chart
def strat_core_dfw(r):
    graded, _ = grade_frames(r)
    cat = r["dfs"]["course_catalog"]
    cs = cat[(cat["course_type"] == "Core") & cat["required_for_majors"].str.contains("Computer Science", na=False)]
    core = graded[graded["course_id"].isin(cs["course_id"])]
    rate = core["grade"].isin(["D", "F", "W"]).groupby(core["course_id"]).agg(["mean", "size"]).nlargest(10, "mean")
    ci = 1.96 * np.sqrt(rate["mean"] * (1 - rate["mean"]) / rate["size"])
    titles = cat.set_index("course_id")["course_title"]
    code = lambda c: re.sub(r"^([A-Z]+)", r"\1 ", c)
    return dict(type="forest", minWidth=560, title="The 10 CS core courses with the most D, F and W grades", rowName="Course",
                valueFmt="pctRaw1", ref=dict(value=round(float(graded["grade"].isin(["D", "F", "W"]).mean() * 100), 2), label="All courses"),
                xLabel="% of grades that were D, F or W",
                note="Required Computer Science core courses, all graded enrollments. Lines are 95% confidence intervals.",
                rows=[dict(label=f"{code(c)} {titles[c]}", n=int(row["size"]), value=round(float(row["mean"] * 100), 2),
                           lo=round(float((row["mean"] - ci[c]) * 100), 2), hi=round(float((row["mean"] + ci[c]) * 100), 2)) for c, row in rate.iterrows()])


@chart
def strat_electives(r):
    import statsmodels.formula.api as smf
    graded, al = grade_frames(r)
    cat = r["dfs"]["course_catalog"]
    electives = cat[cat["course_type"] == "Elective"].set_index("course_id")["course_title"]
    takers = graded[graded["course_id"].isin(electives.index)].groupby("course_id")["campus_id"].agg(set)
    ws = al[al["sal_adj"].notna()]
    rows = []   # same test as the advisor: took the elective vs not, holding GPA, internships, credentials and track fixed
    for major, d in ws.groupby("major"):
        for cid, ids in takers.items():
            took = d["campus_id"].isin(ids).astype(int)
            if 50 <= took.sum() <= len(d) - 50:
                f = smf.ols("sal_adj ~ took + final_gpa + internship_count + credential_count + C(track)", data=d.assign(took=took)).fit()
                lo, hi = f.conf_int().loc["took"]
                rows.append(dict(major=major, course=cid, n=int(took.sum()), effect=f.params["took"], lo=lo, hi=hi, p=f.pvalues["took"]))
    el = pd.DataFrame(rows)
    el["sig"] = el["p"] * len(el) < 0.05   # Bonferroni over every elective tested, both majors
    cs = el[el["major"] == "Computer Science"].sort_values("effect", ascending=False)
    code = lambda c: re.sub(r"^([A-Z]+)", r"\1 ", c)
    return dict(type="forest", labels=True, minWidth=640, title="Security electives come with higher pay, even after GPA and internships", rowName="Elective",
                valueFmt="signedUsdK1", ref=dict(value=0, label="No difference"), xLabel="Salary vs similar grads: took the elective vs didn't",
                note=f"Computer Science alumni with a first-job salary. OLS holding GPA, internships, credentials and track constant; lines are 95% confidence intervals. "
                     f"Grey dots don't pass a Bonferroni check over all {len(el)} elective tests.",
                rows=[dict(label=f"{code(row['course'])} {electives[row['course']]}", n=row["n"], value=round(float(row["effect"])),
                           lo=round(float(row["lo"])), hi=round(float(row["hi"])), hl=bool(row["sig"])) for _, row in cs.iterrows()])


def strategy_checks_table(r):
    """One factor at a time: Spearman for ordered factors, Mann-Whitney for two-group gaps (salaries are skewed)."""
    from scipy import stats
    _, al = strat_frames(r)
    ws = al[al["sal_adj"].notna()]
    hired = al[al["months_to_first_job"].notna()]
    responded = al[al["first_destination"] != "No Response"]
    offer = ws["first_job_found_via"] == "Return Offer from Internship"
    mw = lambda a, b: stats.mannwhitneyu(a.dropna(), b.dropna()).pvalue
    tests = [
        ("More internships → more likely employed full-time", "Spearman", stats.spearmanr(responded["internship_count"], responded["first_destination"] == "Employed Full-Time")),
        ("More internships → fewer months to first job", "Spearman", stats.spearmanr(hired["internship_count"], hired["months_to_first_job"])),
        ("More internships → higher salary premium", "Spearman", stats.spearmanr(ws["internship_count"], ws["sal_adj"])),
        ("Return offers pay more than other routes", "Mann-Whitney", mw(ws["sal_adj"][offer], ws["sal_adj"][~offer])),
        ("More experience types → higher premium", "Spearman", stats.spearmanr(ws["breadth"], ws["sal_adj"])),
        ("Club leaders earn more than members (raw)", "Mann-Whitney", mw(ws["sal_adj"][ws["role"] == "Club leader"], ws["sal_adj"][ws["role"] == "Member only"])),
        ("Higher GPA → higher premium", "Spearman", stats.spearmanr(ws["final_gpa"], ws["sal_adj"])),
    ]
    rows = ""
    for claim, test, res in tests:
        rho, p = (res.statistic, res.pvalue) if hasattr(res, "pvalue") else (None, res)
        rows += (f"<tr><td>{claim}</td><td>{test}</td><td class=num>{'' if rho is None else f'{rho:.3f}'}</td>"
                 f"<td class=num>{p:.1e}</td><td>{'Supported' if p < 0.05 else 'Not significant'}</td></tr>")
    return ('<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Test</th><th class=num>ρ</th><th class=num>p-value</th><th>Verdict</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def strategy_ols_table(r):
    """OLS with all the factors together: each coefficient is the change for one more unit, holding the others constant."""
    import statsmodels.formula.api as smf
    _, al = strat_frames(r)
    ws = al[al["sal_adj"].notna()]
    model = ws.assign(leader=(ws["role"] == "Club leader").astype(int), member=(ws["role"] == "Member only").astype(int))
    formula = "internship_count + final_gpa + breadth + leader + member + credential_count"
    names = {"internship_count": "Internships", "final_gpa": "Final GPA", "breadth": "Experience types tried",
             "leader": "Club leader (vs no club)", "member": "Club member only (vs no club)", "credential_count": "Credentials"}
    fits = [smf.ols(f"sal_adj ~ {formula}", data=model).fit(), smf.ols(f"months_to_first_job ~ {formula}", data=model).fit()]
    cell = lambda f, k, money: (f"<td class=num>{('+' if f.params[k] >= 0 else '−') + ('$' + format(abs(f.params[k]), ',.0f') if money else format(abs(f.params[k]), '.2f'))}</td>"
                                f"<td class=num>{f.pvalues[k]:.1e}</td><td>{'Yes' if f.pvalues[k] < 0.05 else 'No'}</td>")
    rows = "".join(f"<tr><td>{label}</td>{cell(fits[0], k, True)}{cell(fits[1], k, False)}</tr>" for k, label in names.items())
    return ('<div class="table-wrap"><table><thead><tr><th>Factor</th><th class=num>Salary premium</th><th class=num>p</th><th>Significant</th>'
            '<th class=num>Months to first job</th><th class=num>p</th><th>Significant</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


# ---------------------------------------------------------------- "Copy code" for every chart
# Each chart's button copies the code that draws it: a notebook's setup cell plus the chart's cell(s),
# or, for the dashboard charts, the analysis.py call plus the functions behind it.

LIFE_SETUP = ["e9bcaedf", "457ba160"]      # dataset_analysis.ipynb: imports + chart colors, CSV loading
TIGER_SETUP = LIFE_SETUP + ["e88ee978"]   # ... plus the Tiger Data connection

NOTEBOOK_CELLS = {   # chart -> (notebook, setup cell ids, chart cell ids; earlier ids define what the last one uses)
    "life_experience_share": ("dataset_analysis.ipynb", LIFE_SETUP, ["fe1eb3ee"]),
    "life_breadth": ("dataset_analysis.ipynb", LIFE_SETUP, ["d4967a77"]),
    "life_type_change": ("dataset_analysis.ipynb", LIFE_SETUP, ["fe5393fc"]),
    "life_share_by_year": ("dataset_analysis.ipynb", TIGER_SETUP, ["153df875"]),
    "life_top_people": ("dataset_analysis.ipynb", LIFE_SETUP, ["22665d88"]),
    "life_fastest_growing": ("dataset_analysis.ipynb", LIFE_SETUP, ["fe5393fc", "22665d88", "a9f363b4"]),
    "life_club_retention": ("dataset_analysis.ipynb", LIFE_SETUP, ["ac30fdd2"]),
    "life_leadership": ("dataset_analysis.ipynb", LIFE_SETUP, ["22665d88", "d52d1bf8"]),
    "life_first_gen": ("dataset_analysis.ipynb", LIFE_SETUP, ["22665d88", "3210bc23"]),
    "life_return_offer": ("dataset_analysis.ipynb", LIFE_SETUP, ["dfacfed9"]),
    "life_salary_premium": ("dataset_analysis.ipynb", LIFE_SETUP, ["b736ba81"]),
    "life_breadth_salary": ("dataset_analysis.ipynb", LIFE_SETUP, ["d4967a77", "3fa5d159"]),
    "life_entry_salary": ("dataset_analysis.ipynb", TIGER_SETUP, ["80114b69"]),
    "strat_internships": ("strategies.ipynb", ["d67dd327"], ["d473f48d"]),
    "strat_channels": ("strategies.ipynb", ["d67dd327"], ["7795b081"]),
    "strat_activity_payoff": ("strategies.ipynb", ["d67dd327"], ["e28955fd"]),
    "strat_clubs": ("strategies.ipynb", ["d67dd327"], ["7433dfc9"]),
    "strat_gpa": ("strategies.ipynb", ["d67dd327"], ["e0b67ded"]),
    "strat_dfw": ("strategies.ipynb", ["d67dd327", "a3c91e07"], ["5b0d2f4e"]),
    "strat_core_dfw": ("strategies.ipynb", ["d67dd327", "a3c91e07"], ["c8e4a6d1"]),
    "strat_electives": ("strategies.ipynb", ["d67dd327", "a3c91e07"], ["e17b93fa"]),
    "job_destinations": ("job_hunting.ipynb", ["bfaac8c4"], ["a7470077"]),
    "job_time_to_hire": ("job_hunting.ipynb", ["bfaac8c4"], ["b52d25aa"]),
    "job_channels": ("job_hunting.ipynb", ["bfaac8c4"], ["0de09a3d"]),
    "job_industries": ("job_hunting.ipynb", ["bfaac8c4"], ["08438015"]),
    "job_regions": ("job_hunting.ipynb", ["bfaac8c4"], ["ef932412"]),
    "job_raises": ("job_hunting.ipynb", ["bfaac8c4"], ["9d8f3e90"]),
    "job_ladder": ("job_hunting.ipynb", ["bfaac8c4"], ["73d9cd85"]),
}

DASHBOARD_CALLS = {   # chart -> (compute function, result variable, plot call), as app.py makes them
    "salary_by_major_degree": ("compute_majors", "majors", 'a.plot_salary_by_major_degree(majors["alumcareerinfo"])'),
    "salary_trend": ("compute_majors", "majors", 'a.plot_salary_trend(majors["trend"])'),
    "salary_by_internships": ("compute_internships", "intern", 'a.plot_salary_by_internships(intern["df_intern"])'),
    "time_to_hire": ("compute_internships", "intern", 'a.plot_time_to_hire(intern["df_intern"])'),
    "return_offers": ("compute_internships", "intern", 'a.plot_return_offers(intern["offer_table"])'),
    "first_job_family": ("compute_first_job", "first_job", 'a.plot_first_job(first_job["career"], first_job["family_summary"], "first_family", "Outcomes by first job family")'),
    "first_job_industry": ("compute_first_job", "first_job", 'a.plot_first_job(first_job["career"], first_job["industry_summary"], "first_industry", "Outcomes by first employer industry")'),
    "unemployment": ("compute_unemployment", "unemp", 'a.plot_unemployment(unemp["unemp"], unemp["overall_rate"], unemp["by_major"])'),
    "experience_share": ("compute_activities", "activities", 'a.plot_experience_share(activities["pct_curr"])'),
    "top_orgs": ("compute_activities", "activities", 'a.plot_top_orgs(activities["top10"], activities["baseline"])'),
}


def chart_code(names, data):
    """name -> the Python that draws it, for the charts placed on the page."""
    import inspect
    root = os.path.dirname(HERE)
    cells = {}
    def cell(nb, cid):
        if nb not in cells:
            cells[nb] = {c.get("id"): "".join(c["source"]).strip() for c in json.load(open(os.path.join(root, nb)))["cells"]}
        return cells[nb][cid]
    code = {}
    for name in names:
        title = data[name].get("title", name)
        if name in NOTEBOOK_CELLS:
            nb, setup, ids = NOTEBOOK_CELLS[name]
            code[name] = (f"# {title}\n# From {nb}: the notebook's setup cells, then the cells that draw the chart.\n\n"
                          + "\n\n".join(cell(nb, i) for i in setup) + "\n\n\n" + "\n\n".join(cell(nb, i) for i in ids) + "\n")
        elif name in DASHBOARD_CALLS:
            compute, var, call = DASHBOARD_CALLS[name]
            plot_fn = getattr(a, call.split("(")[0].split(".")[1])
            code[name] = (f"# {title}\n# From analysis.py (the team dashboard); run next to analysis.py and data/.\n"
                          f"import analysis as a\n\n{var} = a.{compute}(a.load())\nfig = {call}\nfig.savefig(\"{name}.png\", dpi=150)\n\n\n"
                          f"# ---- the functions behind it, from analysis.py ----\n\n"
                          + inspect.getsource(getattr(a, compute)) + "\n\n" + inspect.getsource(plot_fn))
    return code


# ---------------------------------------------------------------- expander tables

usd = lambda v: f"${v:,.0f}"
signed = lambda v: f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"
pct = lambda v: f"{v:.0%}"
count = lambda v: f"{v:,.0f}"


def html_table(df, cols):
    """cols: (column, or None for the index; header; formatter; numeric?)"""
    head = "".join(f'<th{" class=num" if num else ""}>{h}</th>' for _, h, _, num in cols)
    body = ""
    for idx, row in df.iterrows():
        cells = ""
        for c, _, f, num in cols:
            v = idx if c is None else row[c]
            cells += f'<td{" class=num" if num else ""}>{"n/a" if not isinstance(v, str) and pd.isna(v) else f(v)}</td>'
        body += f"<tr>{cells}</tr>"
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def tables(r):
    fj = r["first_job"]
    summary_cols = lambda name: [(None, name, str, False), ("alum_count", "Alumni", count, True),
                                 ("median_first_salary", "Median first salary", usd, True), ("median_vs_peers", "Now vs peers", signed, True)]
    return {
        "job_checks": job_checks_table(r),
        "strategy_checks": strategy_checks_table(r),
        "strategy_ols": strategy_ols_table(r),
        "majorcareerinfo": html_table(r["majors"]["majorcareerinfo"], [
            ("major", "Major", str, False), ("degree_level", "Degree", str, False), ("alum_count", "Alumni", count, True),
            ("job_count_avg", "Avg jobs", lambda v: f"{v:.1f}", True), ("avg_salary", "Avg salary", usd, True),
            ("max_salary", "Max salary", usd, True)]),
        "by_intern": html_table(r["intern"]["by_intern"], [
            ("internship_count", "Internships", str, True), ("alum_count", "Alumni", count, True),
            ("median_salary", "Median first-job salary", usd, True), ("median_months", "Median months to hire", lambda v: f"{v:.1f}", True),
            ("pct_full_time", "Employed full-time", pct, True), ("pct_return_offer", "Return offer", pct, True)]),
        "offer_table": html_table(r["intern"]["offer_table"], [
            ("internship_count", "Internships", str, True), ("median_no_offer", "Median, no offer", usd, True),
            ("median_offer", "Median, return offer", usd, True), ("offer_premium", "Premium", signed, True),
            ("count_no_offer", "Alumni, no offer", count, True), ("count_offer", "Alumni, offer", count, True)]),
        "family_summary": html_table(fj["family_summary"], summary_cols("First job family")),
        "industry_summary": html_table(fj["industry_summary"], summary_cols("First employer industry")),
        "exp_by_type": html_table(r["activities"]["exp_by_type"], [
            (None, "Experience type", str, False), ("records", "Records", count, True), ("students", "People", count, True),
            ("orgs", "Organizations", count, True), ("avg_terms", "Avg terms", lambda v: f"{v:.1f}", True),
            ("avg_hours", "Avg hours/week", lambda v: f"{v:.1f}", True), ("pct_paid", "Paid", lambda v: f"{v:.0f}%", True)]),
    }


# ---------------------------------------------------------------- page

def build():
    dfs = a.load()
    r = dict(majors=a.compute_majors(dfs), intern=a.compute_internships(dfs), first_job=a.compute_first_job(dfs),
             unemp=a.compute_unemployment(dfs), activities=a.compute_activities(dfs), dfs=dfs)
    page = raw = open(os.path.join(HERE, "page.html")).read()

    used = []

    def cell(m):
        name, code, caption = m.group(1), m.group(2), (m.group(3) or "").strip()
        used.append(name)
        cap = f'<div class="chart-caption">{caption}</div>' if caption else ""
        return (f'<figure class="cell"><div class="cell-in"><span class="prompt">In [{len(used) + 1}]:</span>'
                f'<code>{code}</code></div><div class="chart" data-chart="{name}">{cap}</div></figure>')

    # {{CELL:name|code}} places a chart; {{CELL:name|code}} ...html... {{/CELL}} also puts that text inside its card
    page = re.sub(r"\{\{CELL:(\w+)\|(.*?)\}\}(?:((?:(?!\{\{CELL:).)*?)\{\{/CELL\}\})?", cell, page, flags=re.S)
    missing = [c for c in used if c not in CHARTS]
    assert not missing, f"page.html places charts with no spec in CHARTS: {missing}"
    unused = [c for c in CHARTS if c not in used]
    if unused:
        print("note: specs not placed on the page:", unused)
    data = {name: CHARTS[name](r) for name in used}

    tabs = tables(r)
    page = re.sub(r"\{\{T:(\w+)\}\}", lambda m: tabs[m.group(1)], page)

    share = r["first_job"]["variance_share"]
    labels = {"major": "Major", "first_family": "First job family", "first_industry": "First employer industry"}
    vt = "".join(f'<tr><td>{labels[k]}<code>{k}</code></td>'
                 f'<td class="num">{row["first_salary"]:.1%}<span class="vbar" style="--v:{row["first_salary"] * 100:.1f}"></span></td>'
                 f'<td class="num">{row["salary_vs_peers"]:.1%}<span class="vbar" style="--v:{row["salary_vs_peers"] * 100:.1f}"></span></td></tr>'
                 for k, row in share.iterrows())
    u, corr = r["unemp"], r["majors"]["major_corr"]
    rates = u["unemp"]["rate"]
    subs = {
        "VT": vt,
        "S_MAJOR": f"{share.loc['major', 'first_salary'] * 100:.0f}",
        "S_FAMILY": f"{share.loc['first_family', 'first_salary'] * 100:.0f}",
        "S_INDUSTRY": f"{share.loc['first_industry', 'first_salary'] * 100:.0f}",
        "S_UNEMP": f"{u['overall_rate'] * 100:.1f}",
        "CORR": f"{corr:.3f}".replace("-", "−"), "CORR2": f"{corr ** 2:.3f}", "CORR2PCT": f"{corr ** 2:.0%}",
        "U_CS": f"{u['by_major']['Computer Science'] * 100:.1f}", "U_IS": f"{u['by_major']['Information Systems'] * 100:.1f}",
        "U_RATES": ", ".join(f"{v:.1%} with {n}" for n, v in rates.items()),
        "U_N3": str(u["unemp"].loc[3, "alum_count"]),
        "CHART_DATA": json.dumps(data, separators=(",", ":")).replace("</", "<\\/"),
        "CHART_CODE": json.dumps(chart_code(used, data), separators=(",", ":")).replace("</", "<\\/"),
        "CHARTS_JS": open(os.path.join(HERE, "charts.js")).read(),
        "HERO_JS": open(os.path.join(HERE, "hero.js")).read(),
    }
    for k, v in subs.items():
        page = page.replace("{{" + k + "}}", v)
    # images under site/assets/ are embedded, so the built page stays a single file
    def inline_asset(m):
        path = os.path.join(HERE, "assets", m.group(2))
        data = base64.b64encode(open(path, "rb").read()).decode()
        return f'{m.group(1)}="data:{mimetypes.guess_type(path)[0]};base64,{data}"'
    page = re.sub(r'(href|src)="assets/([^"]+)"', inline_asset, page)

    left = re.findall(r"\{\{[A-Z_]+(?::[^}]*)?\}\}", page)
    assert not left, f"unfilled placeholders: {left}"

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(page)
    print(f"built {os.path.relpath(OUT)}: {len(used)} charts, {len(page) / 1024:.0f} KB")

    # the "Free consultation" page (Advisor Ann) shares the main page's fonts, styles and crayon filters
    head = re.sub(r"<title>.*?</title>\n", "", raw.split('<header class="nav">')[0])
    d0 = raw.index("<!-- Crayon collage filters."); defs = raw[d0:raw.index("</svg>", d0) + len("</svg>")]   # the same crayon filters
    enter = open(os.path.join(HERE, "enter.html")).read()
    for k, v in {"HEAD": head, "DEFS": defs, "ADVISOR_JS": open(os.path.join(HERE, "advisor.js")).read()}.items():
        enter = enter.replace("{{" + k + "}}", v)
    open(OUT_ENTER, "w").write(enter)
    print(f"built {os.path.relpath(OUT_ENTER)}")


if __name__ == "__main__":
    build()
