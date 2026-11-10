# AI 反馈统计

Local game-feedback reporting app: import feedback, translate and classify it, compare periods, review the report, and optionally push it to Feishu. Baloot is configured; Okey and 新疆 have placeholder pages.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create `.env` in the project root:

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
# Optional model override:
# OPENROUTER_MODEL=openai/gpt-4o-mini
```

```sh
.venv/bin/python app.py
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050). `PORT` optionally changes the port. Run one server process for one local user. Restart after changing `.env` or Python code. Credentials stay on the server; nothing is imported from `backup/`.

## Use the app

1. Select Baloot and upload CSV or `.xlsx`. Only the first Excel sheet is read. The preview includes rows that will later be excluded.
2. Select inclusive dates. October 19–22 compares with October 12–15, exactly seven days earlier. Only records in either period enter AI processing; overlapping records are processed once and counted in each applicable period.
3. Check the counts and coverage warning. If the export does not span both periods, generation requires confirmation that it is complete and the missing boundary dates truly had no feedback. Days without records alone do not prove an incomplete export.
4. Generate the report. **Stop** lets in-flight requests finish and saves successful results. Retry the same upload to reuse them. Every selected record must finish successfully before a report is exported.
5. Review the report or download `report.md` and `feedback_details.xlsx`. Optionally use **编辑建议** or **审核 BUG**, save the changes, then push to Feishu.

Uploading, previewing, selecting dates and manual editing make no AI calls. Generating reports uses the configured paid API. For a sample, upload `examples/feedback_sample.csv` and select **2026-10-19 through 2026-10-22**.

Leaving a game or refreshing does not cancel processing. Reopening the game reconnects to its active report and restores the original upload, dates, progress and Stop button. A report that finished while you were away reopens with its downloads. Stopped, failed or interrupted reports can be retried against the same upload to reuse completed work; recovery never starts another AI request automatically.

The browser remembers the report reference for each game for the current tab session and includes it in the page URL. The server remains authoritative about active work, including tasks started in another tab. If another report blocks generation, **打开报告** opens its progress or saved result. Only one report processes at a time; Stop releases that slot after in-flight work finishes. Uploading a replacement file starts a new workspace. A server restart marks unfinished tasks interrupted.

## Data and report rules

- Required columns are `反馈ID`, `用户自己填写的内容` and `反馈时间`. Optional source fields are `用户ID`, `VIP等级`, `附加信息`, `反馈标签` and `用户主动选择的标签`. Per-game `column_mapping` maps source headers to these names.
- IDs remain strings, including leading zeros. Empty IDs/text, invalid dates, ignored source labels and duplicate IDs are excluded; the first valid instance of an ID is retained. Different IDs with identical text remain separate records. `ignored_labels` matching ignores case.
- Dates follow source-system local time. Uniform timezone offsets retain wall-clock dates; mixed timezone formats must be normalized before upload.
- AI returns a Chinese translation, a strict validity decision and one validated I/II/III category path. Python computes counts, shares and period changes. Short, understandable feedback remains valid. Unclear feedback is excluded from statistics and preserved with its reason in **排除反馈明细**. Feedback containing actual abuse is excluded in full from analysis, statistics and every Excel sheet; its processing decision remains cached for retries.
- Suggestion and BUG candidates come only from valid current-period rows whose original `反馈标签`, after trimming, exactly matches `suggestion_labels` or `bug_labels`. Matching is case-sensitive; Baloot uses `["建议"]` and `["BUG"]`. The player-selected tag and any model-supplied suggestion flag do not control candidacy.
- Suggestions merge equivalent requests within one I category and retain their supporting feedback IDs. BUG analysis merges equivalent malfunctions only within the same complete category path, retaining every recognized issue and contributor without a top-N cutoff. Both pools are independent of module growth thresholds; no candidates means no corresponding analysis requests.
- The report contains an overview, evidence-linked suggestions, suspected BUGs when enabled, and module statistics. Report text is Chinese; original-language source text remains available in the review editor and detail exports. Suggestions, BUGs and issue analysis cite **feedback IDs**.
- Excel contains **本期反馈明细** and **上周同期反馈明细**, plus the excluded sheet when needed. Its nine valid-detail columns retain IDs, source text, translation and other detail fields. I/II/III paths appear in two category columns, for example `玩法相关 | 活动（许愿树）`. Statistics aggregate by I category. Source labels and the internal suggestion flag are omitted from new exports.

Game categories, terminology and extraction rules live in `config/<game>/labels.json` and `prompts/`; update those sources rather than duplicating them in code.

## Review before pushing

**编辑建议** changes suggestion wording while keeping its evidence fixed. Uncheck **保留此建议** to remove a suggestion from the report, then save; retained suggestions are numbered consecutively. Removed suggestions and their sources remain in the editor for restoration, including when every suggestion has been removed. **审核 BUG** also lets you exclude an issue or remove an incorrectly attributed source. Saving BUG edits recalculates that section's issue and distinct-feedback counts. Neither editor changes module statistics or the Excel export, and neither calls AI.

Each description is limited to 2,000 characters and must follow the Chinese report rules. **保存修改** updates the saved Markdown used by preview, download and Feishu. **取消** discards drafts; **恢复原始建议 / 恢复原始 BUG** restores the original into the draft and requires Save to apply. Removed BUG candidates remain recoverable with their stable codes and original sources.

Saves reject stale report versions. An open editor blocks Markdown downloads and pushes until saved or canceled. Once Feishu push preparation starts, the report is permanently locked for editing, including failed detail uploads and partially sent messages, so resumed parts use consistent content.

New reports preserve immutable originals in `report.original.md` and evidence in `review.json`. Older unpushed reports that cite feedback IDs can recover suggestion evidence from their own completed current-period workbook sheet. Missing/unverifiable sources and user-ID-only reports require regeneration; old reports without a BUG snapshot cannot acquire BUG review evidence from later processing caches.

## Configuration

Configuration is repository-managed; the app has no settings editor or configuration-write API. Global settings are overridden by `config/<game>/settings.json`; `OPENROUTER_MODEL` overrides the configured model.

| File | Controls |
| --- | --- |
| `config/settings.json` | Model, batching, concurrency, retries, timeouts, import and period limits |
| `config/games.json` | Game names, images and enabled state |
| `config/<game>/settings.json` | Source columns, ignored/candidate labels, game overrides and Feishu settings |
| `config/<game>/report_rules.json` | Growth thresholds, suggestion/issue/evidence limits and analysis input budget |
| `config/<game>/labels.json` | Category hierarchy, exclusion reasons and terminology |
| `config/<game>/prompts/*.md` | Classification, suggestion, issue and optional BUG instructions |

Current global defaults:

| Settings | Values |
| --- | --- |
| `model` | `openai/gpt-4o-mini` |
| `classification_batch_size`, `classification_batch_max_chars` | 30 records, 26,000 serialized input characters |
| `concurrency` | 200 simultaneous classification requests |
| `max_retries`, `retry_delay`, `timeout_seconds` | 3 retries, 2-second delay, 60-second timeout |
| `max_upload_mb`, `max_rows`, `max_expanded_xlsx_mb` | 32 MB upload, 100,000 rows, 128 MB expanded Excel |
| `preview_page_size`, `max_report_days`, `csv_encoding` | 50 rows, 31 days, `utf-8-sig` |

Batch size and character budget must be positive integers. Long records reduce the effective batch size; a record larger than the budget is sent alone without truncation. Successful entries are checkpointed before retrying only unresolved entries. Progress counts completed records, not requests. Provider errors appear in task progress and server logs; fix the cause before retrying. HTTP 401/402/404 stop later batch groups, while 403 can be specific to one batch. Lower concurrency if the provider rate-limits requests.

Baloot's report rules currently allow 10 suggestions, analyze up to three issue themes per flagged module, cite up to three evidence IDs per issue analysis, and use a 12,000-character analysis input budget. The issue evidence limit does not truncate merged suggestion or BUG contributors.

Each I category needs a finite, nonnegative `module_growth_thresholds` value; Baloot currently uses `0.3` (30%) for all six categories. A module with current feedback is flagged when `(current - previous) / previous >= threshold`. Any positive count on a zero previous baseline is new and flagged. A zero threshold includes flat positive counts; declines are not flagged. No minimum count or absolute increase is required.

Tasks capture their configuration and completed reports save `config_snapshot.json`. Model, classification prompt, taxonomy or source suggestion-rule changes can require reprocessing; batch-size/concurrency changes reuse matching record results. Report-only changes reuse translations and invalidate only affected analysis caches. Completed reports are never rewritten automatically. To add a game, provide its own settings, taxonomy and required prompts, then enable it in `games.json`; `prompts/bugs.md` enables BUG analysis.

## Feishu

Pushes are manual. When spreadsheet sync is enabled, the app writes the detail tab first, then sends the saved report as native-table cards with a link to the details. Long cards are split under an 18,000-byte request budget with at most five tables per part; every table cell is retained. No AI call or file attachment is involved.

Baloot already enables webhook and spreadsheet integration in its game settings. Supply the corresponding environment variables in `.env` and verify the configured spreadsheet destination:

```dotenv
FEISHU_BALOOT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/your-webhook-id
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
```

Other games can use their own environment variable names. If the bot requires signatures, set `feishu.secret_env` to an additional environment variable containing its signing secret. Bot keyword/IP restrictions must permit the outgoing message and server.

For a new integration, configure `feishu` in the game's `settings.json`:

```json
{
  "enabled": true,
  "webhook_env": "FEISHU_BALOOT_WEBHOOK",
  "spreadsheet": {
    "enabled": true,
    "url": "https://your-company.feishu.cn/sheets/your-spreadsheet-token",
    "app_id_env": "FEISHU_APP_ID",
    "app_secret_env": "FEISHU_APP_SECRET",
    "batch_rows": 500
  }
}
```

Publish the Feishu application, enable Sheets editing permission (`sheets:spreadsheet`), and add it to the target spreadsheet as an editable document application. A `/wiki/` URL also needs Wiki node read access unless `spreadsheet_token` supplies the verified underlying sheet token. A Wiki node ID is not a spreadsheet token. Direct `/sheets/` URLs need no Wiki resolution.

Check the connection without creating tabs or sending messages (this contacts Feishu):

```sh
.venv/bin/python -B -m feedback.feishu_sheets --check baloot
```

The detail tab combines both valid Excel sheets and adds `统计期间`; overlapping records appear in both groups. The excluded sheet is not uploaded. New tabs use a date-range title such as `2026.10.19-2026.10.22`. A title collision stops the push rather than overwriting another report; rename the existing tab before retrying. `batch_rows` supports 1–5,000 rows. IDs retain their text format; dates use the API-supported `yyyy/MM/dd HH:mm:ss` formatter. Older supported 10/11/12-column workbooks retain their original columns when pushed.

Table widths are keyed by column header under `feishu.card.table_column_widths`. Values support `auto`, `80px`–`600px` or `1%`–`100%`. Baloot's current widths are **12% / 16% / 16% / 18% / 38%** for module, current, previous, change and analysis. The shared fallback is **12% / 16% / 12% / 16% / 44%**; unknown headers use `auto`. Width edits apply to new pushes, while started pushes retain their saved widths.

Push checkpoints preserve safe retries:

- `feishu_push.json` records accepted card parts. Repeated completed pushes do not resend. Explicit rejection can be retried after correcting configuration. For an unknown result after a timeout or interruption, inspect the group first; use **确认本段未收到，继续推送** only when that part did not arrive.
- `feishu_sheet.json` records the reserved tab and confirmed row offset. A retry reuses the same tab, including older names with identifier suffixes, and rewrites only unconfirmed ranges. Changing that report's destination or completed export is rejected; restore the original or generate a new report.
- Partially accepted `card-markdown-v1` sends resume with their original content/layout. Completed older formats may be offered a new native-table push; follow the app's saved state. Never delete checkpoint files to force a retry.

HTTPS certificate verification remains enabled. If Python's certificate store fails, the app can use system `curl`; keep it on `PATH`. Credentials are passed through standard input rather than command arguments.

## Code and storage

`app.py` builds the application. `web/` owns routes, `feedback/workflow.py` coordinates reporting, and the other `feedback/` modules handle import, processing, analysis, review, exports and Feishu. Both editors share snapshot, version and lock handling in `feedback/review_state.py`. `templates/` and `static/` contain the presentation.

```text
storage/<game>/<upload-id>/
  source.csv or source.xlsx
  rows.csv
  upload.json
  processed.csv
  jobs/<job-id>.json
  analysis_cache/
  reports/<job-id>/
    report.md
    report.original.md
    review.json
    review.lock.json          # Created when push preparation begins
    feedback_details.xlsx
    config_snapshot.json
    feishu_push.json          # Created when sending cards
    feishu_sheet.json         # Created when syncing details
```

Uploads and report runs are isolated. Record results are reused within an upload, not across separate uploads. Storage is private and is not served as a static folder. There is no database or automatic deletion: back up storage and configuration to retain reports and duplicate-send protection across restarts. `.env`, `.venv/`, `storage/` and `backup/` are excluded from Git. Never run multiple app processes against the same storage folder.

## Verification

```sh
.venv/bin/python -B -m unittest discover -s tests -v
node tests/test_game_ui.cjs
node tests/test_navigation_recovery.cjs
node tests/test_suggestion_removal.cjs
```

Node is needed only for the JavaScript interaction checks. Tests use temporary files and simulated AI/Feishu responses; they make no paid model calls or real pushes.
