# AI 反馈统计

Local game-feedback reporting app. The homepage and game workspace share the same minimal design. The backup is reference material only; the new app never imports code, configuration, credentials or data from it.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5050. Optional `PORT` changes the port. This app is for one local user and one server process.

Create `.env` in the project root using `.env.example` and set `OPENROUTER_API_KEY`. `OPENROUTER_MODEL` optionally overrides the model in `config/settings.json`. The API key is never sent to the browser. Existing credentials in `backup/` are not automatically used. Uploading, previewing and selecting dates do not call AI; generating a report does.

## Workflow

1. Select Baloot. Okey and Xinjiang have placeholder pages until their own configuration is ready.
2. Upload CSV or `.xlsx`. Only the first Excel sheet is used. A paginated, scrollable preview displays the original source fields, including excluded rows.
3. Select inclusive start and end dates. October 19–22 includes all of October 22 and compares against October 12–15, exactly seven days earlier.
4. Review current, comparison, excluded and out-of-range counts. Only the union of the two periods enters translation/classification. Gaps between periods are ignored. For periods longer than seven days, overlapping records are processed once and counted independently in each period.
5. Generate the report. Translation/classification saves each completed batch. Stop lets in-flight requests finish, saves their work and prevents subsequent requests. Retry the same upload to reuse matching results.
6. Read the report preview or download `report.md` and `feedback_details.xlsx`. Excel contains **本期反馈明细** and **上周同期反馈明细**, with original text, Chinese translations, categories and source fields.

The workspace keeps file/date controls together, uses Data/Report tabs, and scrolls long tables and reports inside their panel. Completed reports open automatically in the Report tab, with downloads in its toolbar.

Use `examples/feedback_sample.csv` for upload/preview testing and select **2026-10-19 through 2026-10-22**. All sample messages and IDs are artificial. Generating even a sample report uses the configured paid API; automated tests mock it.

## Coverage and data rules

- Expected columns: 反馈ID, 用户ID, VIP等级, 用户自己填写的内容, 附加信息, 反馈时间, 反馈标签, 用户主动选择的标签. Only 反馈ID, 用户自己填写的内容 and 反馈时间 are required. Configure source-column mappings per game.
- IDs are strings. Empty IDs/messages, invalid dates, ignored source labels and repeated IDs are excluded; the first valid instance of an ID is retained. Different IDs with the same message remain separate records.
- Dates follow the source system's local time; uniform timezone offsets retain wall-clock dates. Mixed timezone formats are rejected for clarification rather than silently shifted.
- Coverage inferred from earliest/latest record dates cannot prove a complete export. Days without records are surfaced for review, not assumed missing. If a required boundary is outside the observed range, report generation is blocked unless the uploader explicitly confirms a complete export with genuinely empty boundary dates. A confirmed empty comparison period is counted as zero and identified in the report's coverage note.
- Every selected valid record must be processed successfully before export. AI failure never silently produces a partial report.
- Each record gets one configured main label and an optional configured subcategory. AI generates translations and evidence-linked text; Python calculates counts, shares, changes and threshold flags. Suggestions use current-period feedback across all categories.

## Repository configuration only

There is no web settings editor or configuration-write API.

| File | Purpose |
| --- | --- |
| `config/settings.json` | Model, concurrency, retries, timeouts, upload/row limits, report-length limit |
| `config/games.json` | Homepage names, icons, enabled games |
| `config/baloot/settings.json` | Column mapping and ignored labels |
| `config/baloot/report_rules.json` | Trend thresholds, suggestion/issue/evidence limits and AI input budget |
| `config/baloot/labels.json` | Fixed categories, subcategories and Arabic game terms |
| `config/baloot/prompts/*.md` | Classification, suggestions and issue-analysis instructions |

Default attention rule: current count ≥ 10, increase ≥ 10 and growth ≥ 50%; a category with a zero baseline needs ≥ 10 current records. Positive counts over zero display as new, never as an invented percentage. Configure these rules in `report_rules.json`.

Changes apply to the next report. Each task captures its configuration and each successful report saves it privately alongside its exports. Changing the model, classification prompt or labels invalidates affected translations. Report-only changes do not retranslate. To add a game, create its own configuration following Baloot's structure, then enable it in `games.json`; do not reuse Baloot terminology unchanged.

## Code and storage

`feedback/` contains domain logic; `web/` contains page/API routes; `templates/` and `static/` contain the shared presentation. `app.py` builds the application. `feedback/workflow.py` is the single reporting workflow.

```text
storage/<game>/<upload-id>/
  source.csv (or source.xlsx)   Original uploaded bytes
  rows.csv                     Normalized source fields for preview/selection
  upload.json                  File metadata
  processed.csv                Saved processing results for this upload
  progress.json                Latest job reference
  jobs/<job-id>.json            Persisted job status
  analysis_cache/               Reusable successful report-AI responses
  reports/<job-id>/
    report.md
    feedback_details.xlsx
    config_snapshot.json
```

Uploads and report runs are isolated; a new report cannot overwrite a completed one. Matching translations are reused within an upload, not across separately uploaded files. A refresh opens a clean workspace and clears the on-screen file, preview, dates and report; saved files remain on disk. Refreshing does not cancel an active background task. A server restart marks unfinished tasks interrupted. Run only one app process against a storage folder.

`storage/`, `.env`, `.venv/`, and `backup/` are excluded from Git. Nothing in storage is served as a public static folder. Back up storage and configuration if you need to preserve uploads/reports. No database or automatic deletion is used.

## Verification

```sh
.venv/bin/python -B -m unittest discover -s tests -v
node tests/test_game_ui.cjs
```

Tests use temporary files and simulated AI responses; they make no paid requests. They cover CSV/Excel import, IDs, exclusions, weekly date alignment, selected rows, retries/reuse, stopping, statistical thresholds, evidence IDs, exports and the HTTP workflow.
