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
- Report suggestion/issue prompts always include a shared Chinese-only rule: translate Arabic names/quotations into Chinese, use a neutral Chinese description when a name cannot be identified, and never append the original Arabic wording. Common English abbreviations remain allowed. AI report text is validated before caching and retried through the normal retry policy; the final Markdown is also checked for Arabic characters (including presentation forms and HTML entities) before publishing. This rule covers the browser report and Feishu card derived from it. Original Arabic feedback in Excel/Feishu detail rows is preserved. Updating this shared report rule invalidates report-analysis caches, not the per-record translation/classification cache. Already completed report files are not rewritten automatically.

## Repository configuration only

### Batch translation and classification

Translation and classification share one request per batch. Global defaults in `config/settings.json` are:

```json
"classification_batch_size": 10,
"classification_batch_max_chars": 16000
```

Both settings must be positive integers. Set either key in `config/<game>/settings.json` to override the global default for that game. `classification_batch_size` is the maximum number of uncached feedback records in one request; `concurrency` is the maximum number of simultaneous requests. With batch size 10 and concurrency 3, up to 3 requests containing up to 10 records each can run together. The last batch may be smaller.

The serialized feedback input also has a character budget (`classification_batch_max_chars`, measured as characters, not tokens, and excluding the shared system prompt). Long feedback can reduce the effective batch size. A single record larger than this budget is sent alone, in full; source text is never truncated. Model context/output limits still apply. Setting the batch size to 1 retains one-record requests using the same batch protocol.

Every input includes its string feedback ID; each returned translation and label is validated against the matching ID and game taxonomy. Response ordering is irrelevant. Unknown or non-string IDs are ignored, and duplicate, missing or invalid entries remain unresolved. Successful entries are saved as soon as each response is validated, before another attempt. Retries submit only unresolved entries within the existing `max_retries` budget; they never silently publish an incomplete report. Progress counts successfully processed feedback records, not requests. Stop saves completed work and prevents subsequent attempts/batches.

Existing per-record caches remain valid across this change and across batch-size/concurrency changes. Processing fingerprints still use the game, model, original classification rules and feedback content; changing those rules or the model can require reprocessing. Batch input/output instructions are added at request time, so existing per-game classification prompts keep their translation/category rules. Suggestion and issue-analysis requests are unchanged. No extra paid call is made to merge translation batches.

### Manual Feishu notifications

Completed reports show a “一键推送到飞书” button beside downloads. The server sends the complete report as `interactive` cards with schema `2.0`, a blue header and `markdown` body elements, matching the card format verified through the webhook. Markdown headings, lists, tables and statistical notes are retained. Long reports become numbered cards under an 18,000-byte serialized request budget (below Feishu's 20 KB limit); table headers repeat on continued tables. Exceptionally large individual table rows become labeled paragraphs without dropping their cell contents. HTML cell breaks become inline separators. Local Excel links become an instruction to download in the app; no file attachments are sent. No additional AI request is made.

In `config/<game>/settings.json`, configure `"feishu": {"enabled": true, "webhook_env": "FEISHU_BALOOT_WEBHOOK"}` and put the real URL in the root `.env` as `FEISHU_BALOOT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...`. Use a different environment variable per game. If the bot requires signatures, add `"secret_env": "FEISHU_BALOOT_SECRET"` to its configuration and set that variable in `.env`. Restart the app after changing `.env`. The actual credentials stay out of browser responses and report configuration snapshots. Robot keyword and IP restrictions must also match the outgoing message and server.

Successful sends are recorded in each report folder as `feishu_push.json`; repeated requests do not send again. Each accepted part is checkpointed, so retries resume at the first unconfirmed part. Explicit bot rejection can be retried after correcting configuration. A timeout or interrupted send is marked unknown; check the group first, then use “确认本段未收到，继续推送” only if that part did not arrive. Reports previously sent as plain-text summaries or `post` messages can be manually sent again as cards; checkpoints from the old format do not skip any new card content. Sending is manual, never automatic after report generation. If Python's CA store rejects the server certificate before sending, the app falls back to system curl with certificate verification enabled. curl must be on the app's PATH for this fallback; credentials are passed via standard input, not command arguments.

There is no web settings editor or configuration-write API.

### Add report details to an existing Feishu spreadsheet

Baloot is configured in `config/baloot/settings.json` with its Wiki URL and the spreadsheet token supplied for that document and checked through the metadata API (title: 用户反馈). The current app lacks Wiki node read permission, so the explicit token avoids needing Wiki resolution. The normal “一键推送到飞书” action now writes the detail tab first, then sends the report card containing “打开完整反馈明细”. Report generation itself does not send or upload anything.

Each game can independently configure:

```json
"feishu": {
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

For a Wiki URL, the application resolves the underlying `sheet` with the Wiki node API. This additionally needs one of `wiki:node:read`, `wiki:wiki:readonly` or `wiki:wiki` and access to the node. Alternatively, set `spreadsheet_token` to the verified underlying spreadsheet token alongside the Wiki `url`. A Wiki node ID is not a spreadsheet token. Direct `/sheets/` URLs do not need Wiki access.

Store the real `FEISHU_APP_ID` and `FEISHU_APP_SECRET` in `.env`, enable the Sheets editing API permission (`sheets:spreadsheet`), publish the application and add it to the destination spreadsheet as an editable document application. Game configurations contain environment variable names, not credentials. Games can share the application or name different credential variables. No new upload/import permission is needed for this approach.

The completed Excel export is read without changing it. Its two detail sheets are combined into one new tab, retaining all 11 columns and adding 统计期间 (本期/上周同期). Feedback belonging to both periods remains in both labeled groups. IDs are kept as text; dates are numeric date values with a date format. Rows are written in bounded batches, with at most 5,000 rows per request. New tab names contain only the report date range, such as `2026.10.19-2026.10.22`. If a different report or an existing tab already occupies that name, the push stops with a rename instruction instead of overwriting it. Retrying the same report resumes its saved tab, including older tabs created with an identifier suffix; existing tabs are not renamed. This writes data and basic header/date formats; it does not copy all Excel styling or create a downloadable attachment.

Date formatting uses the API-verified `yyyy/MM/dd HH:mm:ss` value, preserving the time component. The API rejects the hyphenated `yyyy-mm-dd` and `yyyy-MM-dd HH:mm:ss` strings with `90204 invalid formatter`, even though similar formats may work in Excel or the Feishu UI. The `@` text formatter is also verified and protects leading-zero IDs. A formatting failure leaves the saved row offset unchanged; retrying the same report reuses its tab.

`feishu_sheet.json` in each report folder stores the target, reserved tab title/ID, row progress and completion state, never the application secret or access token. If tab creation times out, the next attempt looks up the persisted title before creating anything. If writing times out, the last unconfirmed range is overwritten on retry. Confirmed ranges are skipped. Changing the destination or completed export after a push has started is rejected; generate a new report or restore that report's original configuration. Do not remove checkpoint files to retry: use the push button. Keep storage backed up if you need duplicate protection across restarts.

Use this read-only connection check (does not create tabs or send group messages):

```sh
.venv/bin/python -B -m feedback.feishu_sheets --check baloot
```

| File | Purpose |
| --- | --- |
| `config/settings.json` | Model, translation/classification batch size and input budget, concurrency, retries, timeouts, upload/row limits, report-length limit |
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
