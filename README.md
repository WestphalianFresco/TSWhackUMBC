# TSWhackUMBC

An exploratory analysis of UMBC student and alumni data, asking one question: **what makes a student "successful"?**

"Success" means different things depending on a student's career and educational goals, so rather than pick one definition, the analysis looks at how different factors (major, degree level, internships) relate to different outcomes (starting salary, salary growth, time to first job).

## Repository layout

```
.
├── dataset_analysis.ipynb   # all analysis and charts
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

Requires Python 3 with pandas, NumPy, matplotlib, and Jupyter.

```bash
pip install pandas numpy matplotlib jupyter
jupyter notebook dataset_analysis.ipynb
```

Run the notebook from the repository root. It reads the CSVs from the relative `data/` path.

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

To keep medians reliable, the salary-trend and internship charts leave out groups with fewer than 50 alumni, and the return-offer comparison leaves out groups with fewer than 10 return offers (which drops 4 internships).

## License

[MIT](LICENSE)
