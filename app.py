"""Streamlit dashboard for the UMBC alumni success analysis.

Run from the repo root:  streamlit run app.py
"""
import matplotlib.pyplot as plt
import streamlit as st

import analysis as a

st.set_page_config(page_title="What Makes a Student Successful?", page_icon="🎓", layout="wide")


@st.cache_data
def get_results():
    dfs = a.load()
    return dict(majors=a.compute_majors(dfs), intern=a.compute_internships(dfs),
                first_job=a.compute_first_job(dfs), unemp=a.compute_unemployment(dfs),
                activities=a.compute_activities(dfs))


def show(fig):
    st.pyplot(fig, width='stretch')
    plt.close(fig)  # free memory on reruns


r = get_results()
majors, intern, first_job, unemp, activities = (r['majors'], r['intern'], r['first_job'],
                                                r['unemp'], r['activities'])
share = first_job['variance_share']

# ---------------------------------------------------------------- header

st.title("What makes a student successful?")
st.markdown(
    '"Success" means something different to different people. Here, we use **salary** as the measure of '
    "success and look at how much of the variation in alumni salaries each factor explains."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Salary variance explained by major", f"{share.loc['major', 'first_salary']:.0%}")
c2.metric("…by first job family", f"{share.loc['first_family', 'first_salary']:.0%}")
c3.metric("…by first employer industry", f"{share.loc['first_industry', 'first_salary']:.0%}")
c4.metric("Alumni still seeking work", f"{unemp['overall_rate']:.1%}")

tabs = st.tabs(["Majors", "Internships", "First Job", "Unemployment", "Student Activities"])

# ---------------------------------------------------------------- majors

with tabs[0]:
    st.header("Majors and their effect on success")
    left, right = st.columns(2)
    with left:
        show(a.plot_salary_by_major_degree(majors['alumcareerinfo']))
        st.markdown(
            "Most alumni in the dataset only hold a bachelor's degree (2,032 vs 115 with a master's), so the "
            "master's comparisons are built on small samples. The typical salary gap between Computer Science "
            "and Information Systems is small: CS alumni earn a median of about 3% more, and the two "
            "distributions overlap almost entirely."
        )
    with right:
        show(a.plot_salary_trend(majors['trend']))
        st.markdown(
            "IS majors close the initial gap by around the **5-year mark**. Fewer alumni remain beyond year 8, "
            "so the gap at the end of the chart is a less certain result."
        )
    corr = majors['major_corr']
    st.info(
        f"**Correlation between major (IS = 1) and starting salary: {corr:.3f}.** Squared, that is "
        f"{corr**2:.3f}: only about **{corr**2:.0%}** of the variation in starting salary is explained by major."
    )
    with st.expander("Salary summary by major and degree"):
        st.dataframe(majors['majorcareerinfo'], hide_index=True)

# ---------------------------------------------------------------- internships

with tabs[1]:
    st.header("Internships and their effect on success")
    left, right = st.columns(2)
    with left:
        show(a.plot_salary_by_internships(intern['df_intern']))
        st.markdown(
            "There is a large difference in starting salary between alumni with 0 and 3 internships, but the "
            "2- and 3-internship distributions overlap heavily. **Salary gains level off after 2 internships.**"
        )
    with right:
        show(a.plot_time_to_hire(intern['df_intern']))
        st.markdown(
            "Most employed alumni with 2 or 3 internships were hired within a month of graduation, while those "
            "with 0 or 1 took much longer. A 3rd internship adds little over a 2nd for both salary and hire time."
        )
    show(a.plot_return_offers(intern['offer_table']))
    st.markdown(
        "At every internship count, alumni whose first job was a **return offer** earned a higher median starting "
        "salary: +\\$5,250 with 1 internship and +\\$6,750 with 2 or 3. Return offers also become much more common "
        "with more internships (25% with 1, 47% with 2, 66% with 3), so part of the value of more internships is a "
        "better chance of converting one into a higher-paying offer."
    )
    with st.expander("Outcomes by internship count"):
        st.dataframe(intern['by_intern'], hide_index=True)
    with st.expander("Return offer premium"):
        st.dataframe(intern['offer_table'], hide_index=True)

# ---------------------------------------------------------------- first job

with tabs[2]:
    st.header("First job family and industry")
    st.markdown(
        "No alumni switched job families or industries, so the first job sets the track. Both factors explain "
        "far more salary variation than major does."
    )
    st.subheader("Share of salary variance explained (η², ANOVA)")
    st.dataframe(share.style.format("{:.1%}"))

    show(a.plot_first_job(first_job['career'], first_job['family_summary'], 'first_family',
                          'Outcomes by first job family'))
    st.markdown(
        "**Cybersecurity** and **Infrastructure & Cloud** earn much more than peers with the same experience, "
        "while **IT Support & Operations** earns much less."
    )
    show(a.plot_first_job(first_job['career'], first_job['industry_summary'], 'first_industry',
                          'Outcomes by first employer industry'))
    st.markdown(
        "**Cloud & Infrastructure** and **Telecommunications** earn much more than **Federal Government** "
        "compared to peers. Cloud & Infrastructure comes out ahead in both views."
    )
    with st.expander("Summary tables"):
        st.dataframe(first_job['family_summary'])
        st.dataframe(first_job['industry_summary'])

# ---------------------------------------------------------------- unemployment

with tabs[3]:
    st.header("Unemployment rates")
    rates, by_major = unemp['unemp']['rate'], unemp['by_major']
    left, right = st.columns([2, 1])
    with left:
        show(a.plot_unemployment(unemp['unemp'], unemp['overall_rate'], by_major))
    with right:
        st.markdown(
            f"Unlike the other tabs, this includes alumni still seeking work. Overall, **{unemp['overall_rate']:.1%}** "
            f"of alumni in the labor force were still seeking work after graduation, and major makes almost no "
            f"difference (CS {by_major['Computer Science']:.1%} vs IS {by_major['Information Systems']:.1%})."
        )
        st.markdown(
            "**Internships make a much bigger difference:** the rate drops steadily from "
            + ", ".join(f"{r:.1%} with {n}" for n, r in rates.items()) + " internships."
        )
        st.markdown(
            "Salary and hiring speed leveled off after 2 internships, but unemployment **keeps dropping with a 3rd**, "
            f"although that group is smaller (n={unemp['unemp'].loc[3, 'alum_count']})."
        )

# ---------------------------------------------------------------- activities

with tabs[4]:
    st.header("Student activities")
    left, right = st.columns(2)
    with left:
        show(a.plot_experience_share(activities['pct_curr']))
        st.markdown(
            "**Student organizations** are by far the most common experience (66%), followed by internships (44%) "
            "and campus jobs (42%). Since internships are tied to higher salaries, faster hiring, and lower "
            "unemployment, **over half of current students not having one yet** is a clear opportunity, though "
            "some of this is expected since about half are freshmen or sophomores."
        )
    with right:
        show(a.plot_top_orgs(activities['top10'], activities['baseline']))
        st.markdown(
            "**3 of the top 4 are certification providers**, and most of the rest are internship employers in "
            "industries that also paid well as a first job, like Cloud & Infrastructure, Telecommunications, and "
            "Cybersecurity Services. This shows correlation, not causation: students who pursue these may already "
            "be heading toward higher-paying fields."
        )
    with st.expander("Experience types"):
        st.markdown(
            "Internships, co-ops, and hackathons are the most time-intensive (28–40 hours/week), while student "
            "organizations, tutoring, and peer mentoring take under 8. Internships and co-ops are mostly paid; "
            "student organizations, hackathons, and competitive teams are unpaid. Blank cells mean hours and pay "
            "don't apply (certifications)."
        )
        st.dataframe(activities['exp_by_type'])
