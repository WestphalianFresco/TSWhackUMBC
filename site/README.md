# Success Metrics page

A one-page summary of `analysis.py`, with animated SVG charts in place of matplotlib screenshots.

```
python site/build.py        # -> site/dist/success-metrics.html (single file, no dependencies)
```

Run it from a branch that has `analysis.py` and `data/*.csv`. Every number comes from the same `compute_*` results `app.py` shows.

| File | What it holds |
| --- | --- |
| `build.py` | Runs `analysis.py`, turns each chart into a JSON spec (`CHARTS`), fills in `page.html`, inlines everything |
| `charts.js` | The chart renderer: one entry in `TYPES` per chart type |
| `page.html` | The page: text, layout, and where each chart goes |

## Adding a chart

1. Add a function to `CHARTS` in `build.py` that returns a spec for one of the types below.
2. Place it in `page.html` with `{{CELL:<function name>|<code shown in the cell>}}`. To put explanation text inside the chart's card, follow it with the text's `<p>` tags and close with `{{/CELL}}`.
3. Run `python site/build.py`.

Chart animation follows the scrollbar. As a chart rises through the bottom 75% of the screen (`END` and `EASE_IN` in `charts.js` set the pace), its bars grow, lines draw, pies sweep, and labels fade in after their marks. Scrolling back up rewinds it, and stopping mid-scroll holds it part-way. Any mark with an animation class (`grow`, `grow-y`, `fade`, `draw`, `sweep`) and a `--i` stagger index animates automatically. Every chart also gets hover tooltips and a table view.

| Type | Spec shape | Used for |
| --- | --- | --- |
| `box` | `groups: [{label, boxes: [{s, ...box(values)}]}]`, optional `series`; `fliers: false`, `zero: true` | Salary by major/degree, by internships |
| `line-band` | `x`, `series: [{label, short, median, p25, p75}]` | Career salary trend |
| `step` | `series: [{label, x, y}]` (cumulative share) | Time to hire |
| `pie` | `pies: [{title, slices: [{s, count, median}]}]`, `series` | Return offers |
| `bar` | `bars: [{label, value, n}]`, optional `ref` line | Unemployment |
| `hbar` | `rows: [{label, value, n?, sub?}]`; negative values diverge left; `style: 'lollipop'` | Experience share, top organizations |
| `hbox-diverge` | `rows: [{label, n, box, diff}]` | First job family / industry |
| `lines` | `x`, `series: [{label, values, s}]` (`s` = color index; leave it out for a grey background line) | Share of participation by year |
| `dumbbell` | `rows: [{label, from, to}]`, `series: [fromName, toName]` | Fastest-growing organizations |
| `stack100` | `rows: [{label, value}]` (value = first share, 0–100) | Club retention |
| `forest` | `rows: [{label, value, lo?, hi?, n?}]`, optional `ref` line; `labels: true` for a dot plot | First-generation members, return offers |
| `heatmap` | `rows`, `cols`, `values[row][col]` | Entry salary by job family and year |
| `area100` | `x`, `series: [{label, s, values}]` (shares adding to 100, bottom band first) | Where grads are 6 months out |
| `stackcols` | `cols: [{label, values}]`, `levels`, `colors` | Seniority by years after the first job |
| `multibar` | `panels: [{title, fmt, bars: [{label, value}]}]` (small multiples; bars may go below zero) | Outcomes by internship count |
| `bubble` | `points: [{label, x, y, n, hl, p?, side?}]` | Which activity types pay off |
| `dotline` | `points: [{label, value, sub, n?}]` | Salary by club involvement |
| `hexbin` | `hexes: [{x, y, c}]`, `sx`, `sy` (from matplotlib's `hexbin`), `line` | GPA vs starting pay |

`box(values)` in `build.py` gives the same quartiles, whiskers, and outliers matplotlib draws. Formats (`usdK`, `pct1`, `signedUsdK1`, ...) are in `FMT` in `charts.js`. Series colors come from `--s1`–`--s4` in `page.html`, which are `analysis.py`'s palette and its dark-mode steps.

A chart shape that none of these types covers needs one new entry in `TYPES` in `charts.js`: a `draw(ctx, spec)` that returns the height, and a `table(spec)`.

## Stage sections

A section with `class="block stage-panel" data-stage="life|strategy|job"` shows below the scene only while that stage is selected in the top bar (`hero.js`). Student Life holds the charts from the Student Activities section of `dataset_analysis.ipynb` (`life_*` specs), Strategies those from `strategies.ipynb` (`strat_*`), and Job Trajectory those from `job_hunting.ipynb` (`job_*`); each spec replicates its notebook cell from the CSVs. Other options: `hl: false` greys out a bar or row, `text` replaces a value label, and `marks` labels points on a `step` chart.
