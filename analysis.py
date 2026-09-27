"""Data prep and charts from dataset_analysis.ipynb, packaged for the Streamlit dashboard.

Each `compute_*` function returns DataFrames/numbers; each `plot_*` function returns a
matplotlib Figure instead of calling plt.show().
"""
import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # no GUI backend when running inside Streamlit
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
MIN_N = 50  # hide groups with too few alumni to trust the median

SERIES_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']  # colorblind-safe, fixed order
INK, INK_MUTED, GRID = '#1f1f1e', '#6b6a64', '#e6e5df'

usd_k = FuncFormatter(lambda v, _: f"${v/1000:.0f}K")


def _style(ax, grid_axis='y'):
    ax.tick_params(colors=INK_MUTED, length=0)
    ax.grid(axis=grid_axis, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ['top', 'right', 'left']:
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(GRID)


def _color_boxes(bp, color):
    for box in bp['boxes']:
        box.set_facecolor(color)


def _box_props(color, markersize=4):
    return dict(patch_artist=True,
                medianprops=dict(color='white', linewidth=2), boxprops=dict(linewidth=0),
                whiskerprops=dict(color=color, linewidth=1.5),
                capprops=dict(color=color, linewidth=1.5),
                flierprops=dict(marker='o', markersize=markersize, alpha=0.4,
                                markerfacecolor=color, markeredgecolor='none'))


# ---------------------------------------------------------------- loading

def load():
    names = ["alumni", "course_catalog", "employment_history",
             "student_experience", "students_current", "transcripts"]
    dfs = {n: pd.read_csv(os.path.join(DATA_DIR, f"{n}.csv"), na_values="Not Applicable") for n in names}
    for c in ['first_job_annual_salary_usd', 'months_to_first_job']:
        dfs['alumni'][c] = pd.to_numeric(dfs['alumni'][c], errors='coerce')
    return dfs


# ---------------------------------------------------------------- majors

def compute_majors(dfs):
    df_alum, df_employ = dfs['alumni'], dfs['employment_history']
    alumcareer = df_employ.merge(df_alum, on='campus_id', how='inner')

    alumcareerinfo = (alumcareer.groupby(['campus_id', 'major', 'degree_level'])
                      .agg(max_salary=('annual_salary_usd', 'max'),
                           avg_salary=('annual_salary_usd', 'mean'),
                           job_count=('job_id', 'count'))
                      .reset_index())
    majorcareerinfo = (alumcareerinfo.groupby(['major', 'degree_level'])
                       .agg(job_count_avg=('job_count', 'mean'),
                            avg_salary=('avg_salary', 'mean'),
                            max_salary=('max_salary', 'max'),
                            alum_count=('campus_id', 'count'))
                       .reset_index())

    # salary each alum was earning at each full year since their first job started
    career = alumcareer.copy()
    career['start_date'] = pd.to_datetime(career['start_date'])
    career['end_date'] = pd.to_datetime(career['end_date']).fillna(pd.Timestamp.today())  # current jobs
    first_start = career.groupby('campus_id')['start_date'].transform('min')
    career['start_yr'] = (career['start_date'] - first_start).dt.days / 365.25
    career['end_yr'] = (career['end_date'] - first_start).dt.days / 365.25

    rows = []
    for (cid, major), jobs in career.sort_values('start_date').groupby(['campus_id', 'major']):
        for year in range(int(jobs['end_yr'].max()) + 1):
            held = jobs[jobs['start_yr'] <= year]  # jobs started by this point
            rows.append((cid, major, year, held['annual_salary_usd'].iloc[-1]))
    by_alum_year = pd.DataFrame(rows, columns=['campus_id', 'major', 'years_in', 'annual_salary_usd'])

    trend = (by_alum_year.groupby(['major', 'years_in'])['annual_salary_usd']
             .agg(median='median', p25=lambda s: s.quantile(.25),
                  p75=lambda s: s.quantile(.75), n='count')
             .reset_index())
    trend = trend[trend['n'] >= MIN_N]

    df_corr = df_alum.dropna(subset=['first_job_annual_salary_usd'])
    is_info_systems = (df_corr['major'] == 'Information Systems').astype(int)
    major_corr = is_info_systems.corr(df_corr['first_job_annual_salary_usd'])

    return dict(alumcareerinfo=alumcareerinfo, majorcareerinfo=majorcareerinfo,
                trend=trend, major_corr=major_corr)


def plot_salary_by_major_degree(alumcareerinfo):
    majors = sorted(alumcareerinfo['major'].unique())
    degrees = sorted(alumcareerinfo['degree_level'].unique())
    colors = dict(zip(degrees, SERIES_COLORS))

    fig, ax = plt.subplots(figsize=(10, 6))
    width = 0.8 / len(degrees)
    for i, degree in enumerate(degrees):
        # offset each degree's box within its major group
        positions = [m + (i - (len(degrees) - 1) / 2) * width for m in range(len(majors))]
        data = [alumcareerinfo.loc[(alumcareerinfo['major'] == m) &
                                   (alumcareerinfo['degree_level'] == degree), 'avg_salary']
                for m in majors]
        bp = ax.boxplot(data, positions=positions, widths=width * 0.85, **_box_props(colors[degree]))
        _color_boxes(bp, colors[degree])
        for pos, d in zip(positions, data):
            if len(d):
                ax.text(pos, d.median(), f"${d.median()/1000:.0f}K", ha='center', va='bottom',
                        fontsize=9, color='white', fontweight='bold')
            ax.text(pos, 0, f"n={len(d)}", ha='center', va='top', fontsize=8, color=INK_MUTED,
                    transform=ax.get_xaxis_transform())

    ax.set_xticks(range(len(majors)), majors, fontsize=11, color=INK)
    ax.yaxis.set_major_formatter(usd_k)
    _style(ax)
    ax.tick_params(axis='x', length=0, pad=18, colors=INK)
    ax.set_title('Average salary per alum, by major and degree level', loc='left',
                 fontsize=14, color=INK, pad=28)
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=colors[d]) for d in degrees],
              labels=degrees, loc='lower left', bbox_to_anchor=(0, 1.0), ncol=len(degrees),
              frameon=False, fontsize=10, labelcolor=INK)
    fig.tight_layout()
    return fig


def plot_salary_trend(trend):
    majors = sorted(trend['major'].unique())
    colors = dict(zip(majors, SERIES_COLORS))

    fig, ax = plt.subplots(figsize=(10, 6))
    for major in majors:
        t = trend[trend['major'] == major]
        ax.fill_between(t['years_in'], t['p25'], t['p75'], color=colors[major], alpha=0.12, linewidth=0)
        ax.plot(t['years_in'], t['median'], color=colors[major], linewidth=2,
                marker='o', markersize=6, markeredgecolor='white', markeredgewidth=1.5, label=major)

    # direct-label each line's end; nudge labels apart so they don't collide
    ends = trend.loc[trend.groupby('major')['years_in'].idxmax()].sort_values('median')
    for i, (_, row) in enumerate(ends.iterrows()):
        ax.annotate(f"{row['major']}  ${row['median']/1000:.0f}K", (row['years_in'], row['median']),
                    xytext=(8, (i - (len(ends) - 1) / 2) * 16), textcoords='offset points',
                    va='center', fontsize=9, color=INK)

    ax.set_xlabel('Years since first job', color=INK_MUTED)
    ax.set_xticks(sorted(trend['years_in'].unique()))
    ax.yaxis.set_major_formatter(usd_k)
    _style(ax)
    ax.margins(x=0.2)
    ax.set_title('Median alumni salary over their career', loc='left', fontsize=14, color=INK, pad=28)
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=len(majors), frameon=False,
              fontsize=10, labelcolor=INK)
    ax.text(0, -0.14, f"Shaded band = middle 50% of alumni. Years with fewer than {MIN_N} alumni hidden.",
            transform=ax.transAxes, fontsize=8, color=INK_MUTED)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- internships

def compute_internships(dfs):
    df_intern = dfs['alumni'].copy()
    df_intern['employed_full_time'] = df_intern['first_destination'] == 'Employed Full-Time'
    df_intern['return_offer'] = df_intern['first_job_found_via'] == 'Return Offer from Internship'

    by_intern = (df_intern.groupby('internship_count')
                 .agg(alum_count=('campus_id', 'count'),
                      median_salary=('first_job_annual_salary_usd', 'median'),
                      median_months=('months_to_first_job', 'median'),
                      pct_full_time=('employed_full_time', 'mean'),
                      pct_return_offer=('return_offer', 'mean'))
                 .reset_index())

    had_intern = df_intern[(df_intern['internship_count'] > 0) & df_intern['first_job_annual_salary_usd'].notna()]
    offer_table = (had_intern.pivot_table(index='internship_count', columns='return_offer',
                                          values='first_job_annual_salary_usd', aggfunc=['median', 'count'])
                   .rename(columns={False: 'no_offer', True: 'offer'}))
    offer_table.columns = [f'{stat}_{grp}' for stat, grp in offer_table.columns]  # flatten the 2-level header
    offer_table['offer_premium'] = offer_table['median_offer'] - offer_table['median_no_offer']
    offer_table = offer_table[offer_table['count_offer'] >= 10].reset_index()  # 4 internships has only 4 offers

    return dict(df_intern=df_intern, by_intern=by_intern, offer_table=offer_table)


def _intern_counts(df_intern):
    return [c for c in sorted(df_intern['internship_count'].unique())
            if (df_intern['internship_count'] == c).sum() >= MIN_N]


def plot_salary_by_internships(df_intern):
    counts = _intern_counts(df_intern)
    data = [df_intern.loc[df_intern['internship_count'] == c, 'first_job_annual_salary_usd'].dropna() for c in counts]

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(data, positions=range(len(counts)), widths=0.5, **_box_props(SERIES_COLORS[0]))
    _color_boxes(bp, SERIES_COLORS[0])
    for pos, d in enumerate(data):
        ax.text(pos, d.median(), f"${d.median()/1000:.0f}K", ha='center', va='bottom',
                fontsize=9, color='white', fontweight='bold')
        ax.text(pos, 0, f"n={len(d)}", ha='center', va='top', fontsize=8, color=INK_MUTED,
                transform=ax.get_xaxis_transform())

    ax.set_xticks(range(len(counts)), counts, fontsize=11, color=INK)
    ax.set_xlabel('Internships completed', color=INK_MUTED)
    ax.yaxis.set_major_formatter(usd_k)
    _style(ax)
    ax.tick_params(axis='x', length=0, pad=18, colors=INK)
    ax.set_title('First-job salary by number of internships', loc='left', fontsize=14, color=INK, pad=16)
    fig.tight_layout()
    return fig


def plot_time_to_hire(df_intern):
    counts = _intern_counts(df_intern)

    fig, ax = plt.subplots(figsize=(9, 5))
    for i, c in enumerate(counts):
        months = np.sort(df_intern.loc[df_intern['internship_count'] == c, 'months_to_first_job'].dropna())
        pct_hired = np.arange(1, len(months) + 1) / len(months)
        ax.step(months, pct_hired, where='post', color=SERIES_COLORS[i], linewidth=2)

    ax.set_xlabel('Months after graduation', color=INK_MUTED)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0%}"))
    _style(ax)
    ax.set_xlim(0, None)
    ax.set_ylim(0, 1.02)
    ax.set_title('Share of employed alumni hired by each month', loc='left', fontsize=14, color=INK, pad=28)
    ax.legend([f"{c} internship{'s' if c != 1 else ''}" for c in counts], loc='lower left',
              bbox_to_anchor=(0, 1.0), ncol=len(counts), frameon=False, fontsize=10, labelcolor=INK)
    fig.tight_layout()
    return fig


def plot_return_offers(offer_table):
    groups = [('no_offer', 'No return offer', SERIES_COLORS[0]),
              ('offer', 'Return offer', SERIES_COLORS[1])]

    fig, axes = plt.subplots(1, len(offer_table), figsize=(4.5 * len(offer_table), 5), squeeze=False)
    for ax, (_, row) in zip(axes[0], offer_table.iterrows()):
        counts = [int(row[f'count_{g}']) for g, _, _ in groups]
        wedges, _ = ax.pie(counts, colors=[c for _, _, c in groups], startangle=90, counterclock=False,
                           wedgeprops=dict(edgecolor='white', linewidth=2))
        total = sum(counts)

        # place each label at the middle of its slice, 55% of the way out from the center
        for wedge, (g, _, _), n in zip(wedges, groups, counts):
            angle = np.deg2rad((wedge.theta1 + wedge.theta2) / 2)
            x, y = 0.55 * np.cos(angle), 0.55 * np.sin(angle)
            ax.text(x, y, f"${row[f'median_{g}']/1000:.1f}K\n({n / total:.0%})",
                    ha='center', va='center', fontsize=10, color='white', fontweight='bold')

        n_int = int(row['internship_count'])
        ax.set_title(f"{n_int} internship{'s' if n_int != 1 else ''}  (n={total})", fontsize=12, color=INK)

    fig.suptitle('Median first-job salary: return offer vs. no return offer', x=0.01, ha='left',
                 fontsize=14, color=INK)
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in groups],
               labels=[label for _, label, _ in groups], loc='upper left', bbox_to_anchor=(0.01, 0.93),
               ncol=2, frameon=False, fontsize=10, labelcolor=INK)
    fig.text(0.01, 0.01, "Slice size (and %) = share of alumni in each group. "
                         "Dollar label = that group's median first-job salary.",
             fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 0.88))
    return fig


# ---------------------------------------------------------------- first job family / industry

def eta_squared(df, group_col, value_col):
    # share of the variance in value_col explained by which group each alum is in
    grand_mean = df[value_col].mean()
    between = df.groupby(group_col)[value_col].agg(lambda s: len(s) * (s.mean() - grand_mean) ** 2).sum()
    return between / ((df[value_col] - grand_mean) ** 2).sum()


def compute_first_job(dfs):
    df_employ, df_alum = dfs['employment_history'], dfs['alumni']
    jobs = df_employ.assign(start_date=pd.to_datetime(df_employ['start_date'])).sort_values('start_date')
    career = jobs.groupby('campus_id').agg(
        first_family=('job_family', 'first'),
        first_industry=('employer_industry', 'first'),
        first_salary=('annual_salary_usd', 'first'),
        latest_salary=('annual_salary_usd', 'last'),
        first_start=('start_date', 'first'),
        last_start=('start_date', 'last'),
        last_tenure=('tenure_months', 'last'))
    career['years_experience'] = ((career['last_start'] - career['first_start']).dt.days / 365.25
                                  + career['last_tenure'] / 12)

    # how far above/below a typical alum with the same years of experience each alum earns now
    slope, intercept = np.polyfit(career['years_experience'], career['latest_salary'], 1)
    career['salary_vs_peers'] = career['latest_salary'] - (intercept + slope * career['years_experience'])

    def summarize(col):
        return (career.groupby(col)
                .agg(alum_count=('first_salary', 'count'),
                     median_first_salary=('first_salary', 'median'),
                     median_vs_peers=('salary_vs_peers', 'median'))
                .query('alum_count >= @MIN_N')
                .sort_values('median_first_salary', ascending=False))

    career_major = career.join(df_alum.set_index('campus_id')['major'])
    variance_share = pd.DataFrame({outcome: {grp: eta_squared(career_major, grp, outcome)
                                             for grp in ['major', 'first_family', 'first_industry']}
                                   for outcome in ['first_salary', 'salary_vs_peers']})

    return dict(career=career, family_summary=summarize('first_family'),
                industry_summary=summarize('first_industry'), variance_share=variance_share)


def plot_first_job(career, summary, col, title):
    order = summary.index[::-1]  # highest starting salary at the top
    data = [career.loc[career[col] == g, 'first_salary'] for g in order]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 0.55 * len(order) + 1.8), sharey=True,
                                   gridspec_kw={'width_ratios': [1.4, 1]})

    bp = ax1.boxplot(data, positions=range(len(order)), orientation='horizontal', widths=0.6,
                     **_box_props(SERIES_COLORS[0], markersize=3))
    _color_boxes(bp, SERIES_COLORS[0])
    ax1.set_yticks(range(len(order)), [f"{g}  (n={summary.loc[g, 'alum_count']})" for g in order])
    ax1.xaxis.set_major_formatter(usd_k)
    ax1.grid(axis='x', color=GRID, linewidth=1)
    ax1.set_title('First-job salary', loc='left', fontsize=12, color=INK)

    vals = summary.loc[order, 'median_vs_peers']
    ax2.barh(range(len(order)), vals, height=0.6,
             color=[SERIES_COLORS[0] if v >= 0 else SERIES_COLORS[1] for v in vals])
    lim = vals.abs().max() * 1.6
    for i, v in enumerate(vals):
        ax2.text(v + lim * 0.02 * (1 if v >= 0 else -1), i, f"{'+' if v >= 0 else '−'}${abs(v)/1000:.1f}K",
                 va='center', ha='left' if v >= 0 else 'right', fontsize=9, color=INK)
    ax2.axvline(0, color=INK_MUTED, linewidth=1)
    ax2.set_xlim(-lim, lim)
    ax2.set_xticks([])
    ax2.set_title('Current salary vs. peers with same experience (median)', loc='left', fontsize=12, color=INK)

    for ax in (ax1, ax2):
        ax.set_axisbelow(True)
        ax.tick_params(colors=INK, length=0)
        ax.tick_params(axis='x', colors=INK_MUTED)
        for side in ['top', 'right', 'left', 'bottom']:
            ax.spines[side].set_visible(False)

    fig.suptitle(title, x=0.01, ha='left', fontsize=14, color=INK)
    fig.text(0.01, 0.0, f"Groups with fewer than {MIN_N} alumni hidden. "
                        "Right panel: 0 = typical alum with the same years of experience.",
             fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    return fig


# ---------------------------------------------------------------- unemployment

def compute_unemployment(dfs):
    df_alum = dfs['alumni']
    in_labor_force = ['Employed Full-Time', 'Employed Part-Time', 'Military', 'Still Seeking']
    df_lf = df_alum[df_alum['first_destination'].isin(in_labor_force)
                    & (df_alum['graduation_year'] < 2026)].copy()  # 2026 grads haven't had time to search
    df_lf['unemployed'] = df_lf['first_destination'] == 'Still Seeking'

    unemp = (df_lf.groupby('internship_count')['unemployed']
             .agg(rate='mean', alum_count='size')
             .query('alum_count >= @MIN_N'))
    return dict(unemp=unemp, overall_rate=df_lf['unemployed'].mean(),
                by_major=df_lf.groupby('major')['unemployed'].mean())


def plot_unemployment(unemp, overall_rate, by_major):
    fig, ax = plt.subplots(figsize=(8, 5))
    x = unemp.index.astype(str)
    ax.bar(x, unemp['rate'], color=SERIES_COLORS[0], width=0.6)
    for xi, r, n in zip(x, unemp['rate'], unemp['alum_count']):
        ax.text(xi, r, f"{r:.1%}", ha='center', va='bottom', fontsize=10, color=INK)
        ax.text(xi, 0, f"n={n}", ha='center', va='top', fontsize=8, color=INK_MUTED,
                transform=ax.get_xaxis_transform())
    ax.axhline(overall_rate, color=INK_MUTED, linewidth=1, linestyle='--')
    ax.text(len(x) - 0.5, overall_rate, f"All alumni {overall_rate:.1%}", ha='right', va='bottom',
            fontsize=9, color=INK_MUTED)

    ax.set_xlabel('Internships completed', color=INK_MUTED)
    ax.tick_params(axis='x', colors=INK, length=0, pad=16)
    ax.set_yticks([])
    for side in ['top', 'right', 'left']:
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(GRID)
    ax.set_title('Share of alumni still seeking work after graduation', loc='left', fontsize=14, color=INK, pad=16)
    ax.text(0, -0.3, "Among alumni in the labor force (employed, military, or still seeking); excludes grad school, "
                     f"no response, and 2026 grads.\nBy major: CS {by_major['Computer Science']:.1%}, "
                     f"IS {by_major['Information Systems']:.1%}.",
            transform=ax.transAxes, fontsize=8, color=INK_MUTED)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- student activities

def compute_activities(dfs):
    df_studexp, df_studcurr, df_alum = dfs['student_experience'], dfs['students_current'], dfs['alumni']

    exp_by_type = (df_studexp.groupby("experience_type")
                   .agg(records=('record_id', 'count'),
                        students=("campus_id", "nunique"),
                        orgs=("organization", "nunique"),
                        avg_terms=("duration_terms", "mean"),
                        avg_hours=("hours_per_week", "mean"),
                        pct_paid=("is_paid", lambda s: s.dropna().astype(bool).mean() * 100))
                   .round(1).sort_values("records", ascending=False))

    df_expcurr = df_studexp[df_studexp["campus_id"].isin(df_studcurr["campus_id"])]
    pct_curr = df_expcurr.groupby("experience_type")["campus_id"].nunique() / len(df_studcurr) * 100

    # alumni only; one row per person per organization, so repeat entries don't double-count
    df_expalum = (df_studexp[df_studexp["campus_id"].isin(df_alum["campus_id"])]
                  .drop_duplicates(["campus_id", "organization"]))
    df_expsalary = df_expalum.merge(df_alum[["campus_id", "first_job_annual_salary_usd"]], on="campus_id")
    org_rank = df_expsalary.groupby("organization").agg(
        n=("first_job_annual_salary_usd", "count"),  # alumni with a first-job salary
        median_salary=("first_job_annual_salary_usd", "median"),
    )
    baseline = df_alum["first_job_annual_salary_usd"].median()
    top10 = org_rank[org_rank["n"] >= 30].nlargest(10, "median_salary")

    return dict(exp_by_type=exp_by_type, pct_curr=pct_curr, top10=top10, baseline=baseline)


def plot_experience_share(pct_curr):
    fig, ax = plt.subplots(figsize=(8, 5))
    s = pct_curr.sort_values()
    ax.barh(s.index, s.values, color=SERIES_COLORS[0], height=0.7)
    ax.bar_label(ax.containers[0], fmt="%.0f%%", padding=3, color=INK_MUTED)
    ax.set(xlabel="% of current students", title="Share of current students with each experience type")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plot_top_orgs(top10, baseline):
    premium = (top10["median_salary"] - baseline)[::-1]  # reversed so #1 is on top

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh([f"{o} (n={n})" for o, n in top10["n"][::-1].items()], premium, color=SERIES_COLORS[0], height=0.7)
    ax.bar_label(ax.containers[0], labels=[f"+${v/1000:.1f}k" for v in premium], padding=3, color=INK_MUTED)
    ax.set(xlabel=f"Median first-job salary above the all-alumni median (${baseline:,.0f})",
           title="Top 10 organizations by alumni first-job salary")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig
