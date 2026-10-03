"""Assemble the whole site for one Vercel project in site/vercel_app/, then deploy that folder.

    python site/make_vercel.py
    npx vercel deploy site/vercel_app --prod

Pushes to GitHub already deploy the repository itself (pyproject.toml and vercel.json at the root); this is
the manual route, from a branch Vercel doesn't deploy to production. It first runs build.py, so the pages
match the current page.html, charts and advisor.js, then collects:
public/            every page in public/ (index.html is the home page; enter-my-data.html is Advisor Ann's chat)
ann_server.py      Ann's backend; pyproject.toml (the root one, entrypoint moved to this flat layout) names its
advisor.py         Handler as the one Python entrypoint, which answers /api/ann, /api/voice and /api/stats
vercel.json        the root one: 120 seconds for that function (a cold start learns from the data for ~10 s, then
                   Claude may take up to 50 s, retried once if busy), and the old page address redirected
It never contains .env or data/: on Vercel the keys are project environment variables and the data comes from
Tiger Data. The folder is rebuilt from scratch each run, so edit the originals, not the copies.
"""
import glob
import json
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

# the root config, with ann_server.py moved from site/ to the top of the bundle
PYPROJECT = open(os.path.join(ROOT, "pyproject.toml")).read()
assert 'entrypoint = "site.ann_server:Handler"' in PYPROJECT
PYPROJECT = PYPROJECT.replace('entrypoint = "site.ann_server:Handler"', 'entrypoint = "ann_server:Handler"')
config = json.load(open(os.path.join(ROOT, "vercel.json")))
fn = config["functions"].pop("site/ann_server.py")
fn.pop("excludeFiles")   # the bundle holds nothing to exclude
config["functions"]["ann_server.py"] = fn

# start clean, but keep what `vercel link` made: .vercel/ (which project this folder deploys to), and .env.local
# (Vercel's own OIDC token) with its .gitignore. .vercelignore below keeps every .env* file out of the upload.
KEEP = {".vercel", ".env.local", ".gitignore"}
os.makedirs(OUT, exist_ok=True)
for name in os.listdir(OUT):
    if name not in KEEP:
        path = os.path.join(OUT, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
subprocess.run([sys.executable, os.path.join(HERE, "build.py")], check=True)   # fresh public/
os.makedirs(os.path.join(OUT, "public"))
for page in glob.glob(os.path.join(ROOT, "public", "*.html")):
    shutil.copy(page, os.path.join(OUT, "public"))
shutil.copy(os.path.join(HERE, "ann_server.py"), OUT)
shutil.copy(os.path.join(ROOT, "advisor", "advisor.py"), OUT)
open(os.path.join(OUT, "pyproject.toml"), "w").write(PYPROJECT)
open(os.path.join(OUT, "vercel.json"), "w").write(json.dumps(config, indent=2) + "\n")
open(os.path.join(OUT, ".python-version"), "w").write("3.13\n")   # the version the pipeline was tested on
open(os.path.join(OUT, ".vercelignore"), "w").write(".env*\n")

files = sorted(os.path.relpath(os.path.join(d, f), OUT) for d, _, fs in os.walk(OUT) for f in fs
               if ".vercel" not in d and f not in KEEP)
assert ".env" not in files and not any(f.startswith("data") for f in files), files   # never the project's keys or raw data
assert ".env*" in open(os.path.join(OUT, ".vercelignore")).read()   # and no .env* file is ever uploaded
print(f"built {os.path.relpath(OUT, ROOT)}: " + ", ".join(files))
