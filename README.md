# TSWhackUMBC

An exploratory analysis of UMBC student and alumni data, asking one question: **what makes a student "successful"?**

"Success" means different things depending on a student's career and educational goals, so rather than pick one definition, the analysis looks at how different factors (major, degree level, internships, first job, student activities) relate to different outcomes (starting salary, salary growth, time to first job, unemployment).

The results can be explored in the Jupyter notebook or in an interactive Streamlit dashboard.

## Repository layout

```
.
├── dataset_analysis.ipynb   # exploratory analysis and charts
├── analysis.py              # the notebook's calculations and charts as reusable functions
├── app.py                   # Streamlit dashboard built on analysis.py
├── requirements.txt
└── data/
    ├── alumni.csv               # graduates: degree, GPA, cost, first-job outcome (~3.2K rows)
    ├── employment_history.csv   # every job held by alumni after graduating (~6K rows)
    ├── students_current.csv     # currently enrolled students (~1.8K rows)
    ├── student_experience.csv   # internships, research, and other experiences (~20K rows)
    ├── transcripts.csv          # course-level grades for each student (~140K rows)
    └── course_catalog.csv       # courses, prerequisites, and skill tags (~70 rows)
```

All tables join on `campus_id`. `transcripts.csv` also joins to `course_catalog.csv` on `course_id`.

## Getting started

Requires Python 3.

```bash
pip install -r requirements.txt
```

### Dashboard

```bash
streamlit run app.py
```

Then open http://localhost:8501. The first load takes about 10 seconds while the data is processed; later loads are cached. Keep the terminal open while using the dashboard (Ctrl+C stops it).

The dashboard shows the headline numbers at the top, then one tab per topic (Majors, Internships, First Job, Unemployment, Student Activities). Each tab has its charts with the conclusions below them, and the supporting tables in expandable sections.

### Notebook

```bash
pip install jupyter
jupyter notebook dataset_analysis.ipynb
```

Run the notebook from the repository root. It reads the CSVs from the relative `data/` path.

### Editing the analysis

Add or change calculations and charts in `analysis.py`: `compute_*` functions return tables and numbers, and `plot_*` functions return matplotlib figures. Then display them in `app.py`. Conclusion text in `app.py` is written by hand, so update it if the data changes.

## Findings so far

### Major
- **Computer Science and Information Systems lead to very similar salaries.** CS alumni earn a median of about 3% more than IS alumni, and the two distributions overlap almost entirely.
- **The early gap closes.** When median salary is tracked by years since the first job, IS alumni catch up to CS alumni around the 5-year mark.
- **Major explains very little of starting salary.** The correlation between major and first-job salary is about −0.135, so major accounts for roughly 2% of the variation.
- Most alumni hold only a bachelor's degree (2,032 vs. 115 with a master's), so the master's comparisons rest on small samples.

### Internships
- **More internships mean higher starting salaries and faster hiring, up to a point.** The gains level off between 2 and 3 internships, both in salary and in how quickly alumni are hired.
- **Most alumni with 2 or 3 internships were hired within a month of graduating.** Those with 0 or 1 took noticeably longer.
- **Return offers pay more.** Among alumni who had internships, those whose first job was a return offer earned a higher median starting salary:

  | Internships | Median salary, no return offer | Median salary, return offer | Difference |
  |---:|---:|---:|---:|
  | 1 | $77,500 | $82,750 | +$5,250 |
  | 2 | $81,000 | $87,750 | +$6,750 |
  | 3 | $83,500 | $90,250 | +$6,750 |

### First job family and industry
- **The first job matters far more than major.** An ANOVA (η²) test shows first job family explains about 20% of the variation in starting salary and first employer industry about 16%, compared with about 2% for major.
- **No alumni switched job families or industries**, so the first job sets the career track.
- **Job family:** Cybersecurity has the highest median starting salary ($91,250) and earns about $11.6K more than peers with the same experience. IT Support & Operations has the lowest ($63,000) and earns about $23K less than peers.
- **Industry:** Cloud & Infrastructure has the highest median starting salary ($90,500), and it and Telecommunications both earn about $9K more than peers. Federal Government has the lowest ($68,250) and earns about $12.7K less than peers.

### Unemployment
- **9.8% of alumni in the labor force were still seeking work** after graduation (CS 9.5%, IS 10.2%).
- **More internships mean lower unemployment:** 12.6% with no internships, 10.6% with 1, 7.2% with 2, and 4.9% with 3.
- These rates exclude alumni in grad school, those who didn't respond, and 2026 graduates, who haven't had time to search yet.

### Student activities
- **Student organizations are the most common experience** (66% of current students), followed by internships (44%) and campus jobs (42%). Co-ops are the least common (8%).
- **Alumni involved with certain organizations earned more.** The top 10 organizations by alumni first-job salary (at least 30 alumni each) have medians $8K–$14K above the all-alumni median of $78,250.

### Notes on sample sizes
To keep medians reliable, the salary-trend, internship, first-job, and unemployment charts leave out groups with fewer than 50 alumni. The return-offer comparison leaves out groups with fewer than 10 return offers (which drops 4 internships).

## License

[MIT](LICENSE)
