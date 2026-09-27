"""Assemble the whole site for one Vercel project in site/vercel_app/, then deploy that folder.

    python site/make_vercel.py
    npx vercel deploy site/vercel_app --prod

It first runs build.py, so the pages match the current page.html, charts and advisor.js, then collects:
public/            every page in site/dist; success-metrics.html is also index.html (the home page), and
                   enter-my-data.html is Advisor Ann's chat, which the home page links to
ann_server.py      Ann's backend; pyproject.toml names its Handler as the one Python entrypoint, which answers
advisor.py         /api/ann, /api/voice and /api/stats (dependencies pinned there too)
vercel.json        gives that function 120 seconds
It never contains .env or data/: on Vercel the keys are project environment variables and the data comes from
Tiger Data. The folder is rebuilt from scratch each run, so edit the originals, not the copies.
"""
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "vercel_app")
sys.path.insert(0, HERE)
from ann_server import selfcheck  # noqa: E402  (light import: the data is only loaded by init())

selfcheck()   # a build never ships course-matching rules that are broken

# the same versions as the local venv the pipeline was tested with
PYPROJECT = """[project]
name = "advisor-ann"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "pandas==3.0.5",
    "numpy==2.4.6",
    "scipy==1.18.1",
    "statsmodels==0.15.0",
    "patsy==1.0.3",
    "SQLAlchemy==2.1.1",
    "psycopg[binary]==3.3.6",
    "python-dotenv==1.2.3",
]

[tool.vercel]
entrypoint = "ann_server:Handler"
"""
# 120 s covers a cold start (~10 s of learning from the data) plus Gemini falling through its busy models (20 s each)
VERCEL_JSON = """{
  "functions": { "ann_server.py": { "maxDuration": 120 } }
}
"""

# start clean, but keep what `vercel link` made: .vercel/ (which project this folder deploys to), and .env.local
# (Vercel's own OIDC token) with its .gitignore. .vercelignore below keeps every .env* file out of the upload.
KEEP = {".vercel", ".env.local", ".gitignore"}
os.makedirs(OUT, exist_ok=True)
for name in os.listdir(OUT):
    if name not in KEEP:
        path = os.path.join(OUT, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
subprocess.run([sys.executable, os.path.join(HERE, "build.py")], check=True)   # fresh site/dist
os.makedirs(os.path.join(OUT, "public"))
for page in glob.glob(os.path.join(HERE, "dist", "*.html")):
    shutil.copy(page, os.path.join(OUT, "public"))
shutil.copy(os.path.join(HERE, "dist", "success-metrics.html"), os.path.join(OUT, "public", "index.html"))
shutil.copy(os.path.join(HERE, "ann_server.py"), OUT)
shutil.copy(os.path.join(ROOT, "advisor", "advisor.py"), OUT)
open(os.path.join(OUT, "pyproject.toml"), "w").write(PYPROJECT)
open(os.path.join(OUT, "vercel.json"), "w").write(VERCEL_JSON)
open(os.path.join(OUT, ".python-version"), "w").write("3.13\n")   # the version the pipeline was tested on
open(os.path.join(OUT, ".vercelignore"), "w").write(".env*\n")

files = sorted(os.path.relpath(os.path.join(d, f), OUT) for d, _, fs in os.walk(OUT) for f in fs
               if ".vercel" not in d and f not in KEEP)
assert ".env" not in files and not any(f.startswith("data") for f in files), files   # never the project's keys or raw data
assert ".env*" in open(os.path.join(OUT, ".vercelignore")).read()   # and no .env* file is ever uploaded
print(f"built {os.path.relpath(OUT, ROOT)}: " + ", ".join(files))
