# TSWhackUMBC

What makes a UMBC computing student successful? This project looks at student and alumni data (majors, internships, first jobs, activities) against outcomes like starting salary, time to a first job and unemployment. It turns the results into a one-page site, a dashboard, and Advisor Ann, a chat advisor that builds a personal plan from a student's transcript.

**Live:** https://successmatrixdashboard.vercel.app

## Features

- **Success Metrics page:** about 40 animated charts on majors, internships, first jobs, unemployment and student activities, plus Student Life, Smart Moves and Job Trajectory sections. Every chart has tooltips and a table view.
- **Advisor Ann:** students type, upload a transcript or resume (PDF, image or text file), or send a voice note. Ann builds their profile and reads it back to confirm. Then she shows where they line up among peers, their top moves ranked by expected salary gain, and how their starting salary could compare with similar grads, each with a chart. She says her answers out loud, and three sample transcripts are there to try.
- **Dashboard:** a Streamlit app with a tab per topic.
- **Notebooks:** the analysis behind it all (`dataset_analysis.ipynb`, `job_hunting.ipynb`, `strategies.ipynb`).

## Architecture

```
data/                six CSVs: alumni, employment history, current students, experiences, transcripts, course catalog
analysis.py          the analysis as functions, used by app.py (the dashboard) and site/build.py
advisor/advisor.py   Ann's advice: peer comparison, outcome projection, ranked levers
site/build.py        builds public/index.html (the page) and public/enter-my-data.html (Ann's chat)
site/ann_server.py   Ann's backend: /api/ann (Claude + advisor.py), /api/voice (ElevenLabs), /api/stats
public/              the built pages
tiger/load_tiger.py  loads the CSVs into Tiger Data (TimescaleDB)
```

When a student messages Ann, the chat page posts it to `/api/ann`. Claude turns the message into profile fields, code checks every course against the catalog, and Ann asks about anything missing or unclear. Once the student confirms the profile, `advisor.py` computes the advice and its charts. With `TIGER_URL` set, Ann reads the data from Tiger Data and keeps each student's draft profile and a usage log there.

The site runs on Vercel (project `successmatrixdashboard`): `public/` is served as static files, and everything else goes to `site/ann_server.py`. Pushing to `main` deploys to production. Page and chart details are in [site/README.md](site/README.md).

## Models

- **Claude Opus 5.5** (`claude-opus-5-5`, through the Anthropic SDK) reads what the student types or uploads and fills in their profile. Structured outputs limit it to majors, tracks, courses and grades that exist in the data. It does not write the advice.
- **The advice** comes from `advisor/advisor.py`: a regression trained on alumni plus the 50 most similar alumni, and only levers that passed significance tests.
- **ElevenLabs:** `eleven_flash_v2_5` for Ann's voice, and Scribe (`scribe_v2`) to transcribe voice notes.

## Getting started

Python 3.13 or newer.

```bash
pip install -r requirements.txt
streamlit run app.py          # dashboard at http://localhost:8501
```

The site build and Advisor Ann need a few more packages:

```bash
pip install scipy statsmodels anthropic SQLAlchemy "psycopg[binary]" python-dotenv
python site/build.py          # rebuild public/ after changing the analysis or the page
python site/ann_server.py     # Advisor Ann at http://localhost:8000
```

Ann reads her keys from `.env` at the repo root. `ANTHROPIC_API_KEY` is required. `ELEVENLABS_API_KEY` adds her voice and voice notes (without it, the browser's voice is used). `TIGER_URL` connects Tiger Data (without it, she reads the CSVs and keeps drafts in memory).

## License

[MIT](LICENSE)
