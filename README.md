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
- Each record gets one I 类别 (`label` / 最终标签), an optional II 类别 (`theme` / 具体主题), and an optional III 类别 (`subtheme` / 三级主题). Third-level choices live in the selected second-level node's `children` list. Every complete path is validated against the configured parent; bracketed display text is never stored as a category name. AI generates translations and evidence-linked text; Python calculates counts, shares, changes and threshold flags. Suggestions use current-period feedback across all categories.
- Classification returns a strict boolean `is_valid`. The backend derives 是否建议 solely from an exact, whitespace-trimmed match of the original 反馈标签 against `suggestion_labels` in the game settings (Baloot: `["建议"]`); it ignores any model-supplied `is_suggestion`. The player-selected tag is not used for this decision. Only valid current-period records with that source label enter the suggestion candidate pool. The model then filters and merges concrete, valuable product suggestions from that pool. Candidates retain their product module and are counted as a subset of valid records, never added again to module totals. “建议” is not a category or subcategory.
- The valuable-suggestions section merges equivalent requests within the same module and shows deduplicated 用户 ID mapped from the source records, preserving leading zeros. Different requests in one module remain separate, and cross-module merges are rejected. Missing user IDs are shown as 未提供, never replaced with feedback IDs. Feedback IDs remain internal references for suggestions; the issue-analysis table still shows feedback IDs. Extraction keeps all identified supporting references, and a consolidation pass runs whenever multiple candidates remain. Compact candidate references preserve all contributors across bounded merge batches. The `max_evidence_ids` limit continues to apply to issue analysis, not to the contributors shown for a merged suggestion. Changing these suggestion instructions invalidates suggestion-analysis caches without changing the classification rules.
- Clear but short feedback is retained. Unintelligible text or feedback lacking an identifiable object/request is marked invalid with a configured reason; the original and translation remain in the processing checkpoint. Such records are excluded from both periods' totals and percentage denominators. An optional third Excel sheet, 排除反馈明细, preserves their original text and reasons; it is not pushed into the valid Feishu detail tab. Any feedback containing actual abusive language is instead marked invalid with 含辱骂内容 and removed in full, even if it also includes useful suggestions or substantive complaints. It is excluded from all report analysis, counts, shares, and every Excel sheet, including 排除反馈明细. Abuse detection shares the existing translation/classification call; there is no extra model call. Processing checkpoints retain the decision for retries. The UI reports semantic and abusive exclusions separately. A period reduced to zero valid records is supported.
- Baloot has six I 类别: 账号问题, 游戏卡顿, 玩法相关, 充值, 作弊, 其他. 玩法相关 retains the direct game modes plus 活动, 游戏限制, 发牌&公平, 段位与赛季 and 俱乐部 as II 类别; the first four groups have III 类别. 战绩记录 maps to 其他 → 个人信息页面, album requests to 其他 → 相册功能, and remaining ban/mute appeals to 其他 → 封禁. Unlisted recharge requests use 充值 → 其他充值问题. Confirmed terminology includes 对局规则举报, 打光头 and the English term project. Source tags are hints for module classification; they do not replace the body. Suggestion candidacy is the separate source-label rule above. Billing amount disputes use the team's 充值未到账 bucket without adding a nonexistent non-delivery claim to the translation.
- Report suggestion/issue prompts always include a shared Chinese-only rule: translate Arabic names/quotations into Chinese, use a neutral Chinese description when a name cannot be identified, and never append the original Arabic wording. Common English abbreviations remain allowed. AI report text is validated before caching and retried through the normal retry policy; the final Markdown is also checked for Arabic characters (including presentation forms and HTML entities) before publishing. This rule covers the browser report and Feishu card derived from it. Original Arabic feedback in Excel/Feishu detail rows is preserved. Updating this shared report rule invalidates report-analysis caches, not the per-record translation/classification cache. Already completed report files are not rewritten automatically.

Excel exports use I 类别 and II 类别 headers. Three-level paths are displayed in two columns, for example `玩法相关 | 活动（许愿树）`; a leaf second-level category remains plain, such as `玩法相关 | 俱乐部`. Statistics aggregate by I 类别, while issue analysis keeps distinct II/III paths separate. Suggestion consolidation receives the full category context so unrelated requests under the broader roots are not conflated. Feishu reads this same display format; previous headers remain accepted without changing existing push fingerprints.

Report changes use ↑ / ↓ for increases/decreases, with signed percentages such as +20.0% / -10.0%, in both the overview and module table. Flat comparisons show 持平; a zero previous-period baseline retains the 新增 label.

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

Failed tasks show the underlying HTTP status and provider reason in the progress message, and write warnings to the Flask terminal. Error summaries hide credentials, input bodies and raw moderation metadata. HTTP 401/402/404 stop subsequent batch groups after current requests finish; a 403 can concern one batch's content, so other batches can still save their results. A 0/N progress count means no feedback has a successful result, even if requests have already failed. Fix the reported cause before retrying the same upload; changes to `.env` require restarting Flask. Earlier task records retain the error detail that was saved when they ran.

Within the current result protocol, per-record caches remain valid across batch-size/concurrency changes. Processing fingerprints include the protocol version, game, model, expanded classification prompt, batch protocol and feedback content; changing classification rules or the model can require reprocessing. Batch input/output instructions are added at request time and share the per-game classification rules. No extra paid call is made to merge translation batches.

The three-level/source-suggestion/abuse protocol is versioned separately: earlier cached results must be reprocessed to apply the new hierarchy, abuse check and source-label decisions. A missing 三级主题 field is not assumed to be an empty third level. Changes to `suggestion_labels` also invalidate affected processing fingerprints. New results (including properly excluded records) are reused across batch-size changes and retries. 是否建议 (是/否) remains an internal source-label candidate marker used for report analysis; it is omitted from new Excel exports and their Feishu detail tabs. New exports omit 原标签, 用户主动选择的标签 and 是否建议 from all sheets; source labels remain available internally for classification. Completed older exports retain their original 10-, 11- or 12-column shape and headers when pushed, preserving their existing checkpoint fingerprints. Existing generated reports are not rewritten.

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

The completed Excel export is read without changing it. Its two valid-detail sheets are combined into one new tab, retaining all detail columns (9 for new reports, 10/11/12 for older reports) and adding 统计期间 (本期/上周同期). An optional excluded-feedback sheet is not included in that tab. Feedback belonging to both periods remains in both labeled groups. IDs are kept as text; dates are numeric date values with a date format. Rows are written in bounded batches, with at most 5,000 rows per request. New tab names contain only the report date range, such as `2026.10.19-2026.10.22`. If a different report or an existing tab already occupies that name, the push stops with a rename instruction instead of overwriting it. Retrying the same report resumes its saved tab, including older tabs created with an identifier suffix; existing tabs are not renamed. This writes data and basic header/date formats; it does not copy all Excel styling or create a downloadable attachment.

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
| `config/baloot/labels.json` | Fixed three-level categories (`themes` / `children`) and Arabic game terms |
| `config/baloot/prompts/*.md` | Classification, suggestions and issue-analysis instructions |

Each I 类别 has its own `module_growth_thresholds` entry in `report_rules.json`. Values are relative growth rates: `0.1` means 10%, `0.5` means 50%, and `2` means 200%. The six defaults are 0.5; edit each value to suit its priority. A category with current feedback enters issue analysis when `(current - previous) / previous >= its threshold`, with no minimum current count or minimum absolute increase. A zero previous count with any positive current count is treated as new and triggers analysis without inventing a percentage. A threshold of 0 also includes flat positive counts; declining categories do not trigger. Every I 类别 must have a finite nonnegative numeric threshold. Threshold-only changes reuse matching translation/classification results and existing analysis caches where applicable.

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
