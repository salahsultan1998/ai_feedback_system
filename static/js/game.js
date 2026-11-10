/* Workspace behavior. Business rules and configuration stay on the server. */
const $ = selector => document.querySelector(selector);
const workspace = $("#workspace");
const base = `/api/games/${encodeURIComponent(workspace.dataset.game)}/uploads`;
const csrf = $('meta[name="csrf-token"]').content;
const workspaceToken = workspace.dataset.workspace;
const uploadHint = $("#file-summary").textContent;
let run = null, page = 1, totalPages = 1, selection = null, job = null;
let busy = false, uploading = false, filePending = false, timer = null, selectionVersion = 0;
let reportComplete = false;
let reviewState = null, reviewFields = [];
let bugReviewState = null, reviewMode = "suggestions";
let editingReview = false, loadingReview = false, savingReview = false, pushing = false;
let lastPush = {};
let recovering = true, recoveryChecked = false, blockedJob = false, activeTimer = null;
let leaving = false, heartbeatTimer = null;

$("#upload-form").reset();
$("#report-form").reset();
window.addEventListener("pageshow", event => {
    if (event.persisted) window.location.reload();
});
window.addEventListener("beforeunload", event => {
    if (!reviewDirty()) return;
    event.preventDefault();
    event.returnValue = "";
});

function switchTab(name, focus = false) {
    for (const key of ["data", "report"]) {
        const active = key === name;
        const tab = $(`#${key}-tab`);
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        $(key === "data" ? "#data-section" : "#result-section").hidden = !active;
        if (active && focus) tab.focus();
    }
    $("#date-span").hidden = name === "report";
    $("#downloads").hidden = name !== "report" || !$("#download-md").dataset.ready;
}
for (const name of ["data", "report"]) {
    const tab = $(`#${name}-tab`);
    tab.addEventListener("click", () => switchTab(name));
    tab.addEventListener("keydown", event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            switchTab(event.key === "Home" ? "data" : event.key === "End" ? "report" : name === "data" ? "report" : "data", true);
        }
    });
}

function error(message) {
    $("#error").textContent = message || "";
    $("#error").hidden = !message;
}

async function api(url, options = {}) {
    const response = await fetch(url, {cache: "no-store", ...options,
        headers: {"X-CSRF-Token": csrf, "X-Workspace": workspaceToken, ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const failure = new Error(data.error || "请求失败，请刷新页面后重试。");
        failure.status = response.status;
        failure.busy = data.busy;
        throw failure;
    }
    return data;
}

function post(url, data) {
    return api(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
}

function runUrl() { return `${base}/${run}`; }
function jobUrl() { return `${runUrl()}/jobs/${job}`; }

function forgetJob() {
    try { window.sessionStorage.removeItem(`feedback-report:${workspace.dataset.game}`); }
    catch (_) { /* Older report references are never restored. */ }
    const url = new URL(window.location.href);
    url.searchParams.delete("upload");
    url.searchParams.delete("job");
    history.replaceState(null, "", url);
}

async function checkAvailability() {
    clearTimeout(activeTimer);
    try {
        const data = await api("/api/jobs/active");
        blockedJob = data.busy;
        recoveryChecked = true;
        $("#report-recovery").hidden = !blockedJob;
        $("#recovery-message").textContent = "另一个报告正在处理或清理中，结束后即可生成";
    } catch (failure) {
        recoveryChecked = false;
        $("#report-recovery").hidden = false;
        $("#recovery-message").textContent = failure.message;
    } finally {
        recovering = false;
        controls();
    }
    if (!leaving && (blockedJob || !recoveryChecked)) activeTimer = setTimeout(checkAvailability, 3000);
}

async function heartbeat() {
    try { await post("/api/workspaces/ping", {}); }
    catch (failure) {
        if (failure.status === 410) {
            error("此工作区已过期，请刷新后重新上传文件");
            return;
        }
    }
    if (!leaving) heartbeatTimer = setTimeout(heartbeat, 60000);
}

window.addEventListener("pagehide", () => {
    leaving = true;
    clearTimeout(timer);
    clearTimeout(activeTimer);
    clearTimeout(heartbeatTimer);
    fetch("/api/workspaces/close", {method: "POST", keepalive: true,
        headers: {"X-CSRF-Token": csrf, "X-Workspace": workspaceToken}}).catch(() => {});
});
$("#recovery-open").addEventListener("click", checkAvailability);

function bugDraft(field) {
    return {id: field.item.id, text: field.input.value, included: field.included.checked,
        feedback_ids: field.sourceChecks.filter(source => source.input.checked).map(source => source.id)};
}

function reviewDirty() {
    if (!editingReview) return false;
    if (reviewMode !== "bugs") return reviewFields.some(({item, input, included}) =>
        input.value !== item.text || included.checked !== (item.included !== false));
    return reviewFields.some(field => {
        const draft = bugDraft(field);
        return draft.text !== field.item.text || draft.included !== field.item.included ||
            draft.feedback_ids.length !== field.item.feedback_ids.length || draft.feedback_ids.some(id => !field.item.feedback_ids.includes(id));
    });
}

function reviewControls() {
    const inEditor = editingReview || loadingReview;
    const edit = $("#edit-suggestions");
    edit.disabled = !reviewState?.editable || inEditor || savingReview || pushing;
    edit.textContent = loadingReview && reviewMode === "suggestions" ? "正在读取…" : "编辑建议";
    edit.title = reviewState?.locked_reason || "对照玩家原文，人工修正建议";
    const bugs = $("#edit-bugs");
    bugs.disabled = !bugReviewState?.editable || inEditor || savingReview || pushing;
    bugs.textContent = loadingReview && reviewMode === "bugs" ? "正在读取…" : "审核 BUG";
    bugs.title = bugReviewState?.locked_reason || "核查 BUG 描述，排除误判问题和归属错误的反馈";
    $("#push-feishu").disabled = !lastPush.available || lastPush.status === "sent" || inEditor || savingReview || pushing;
    $("#download-md").setAttribute("aria-disabled", String(inEditor || savingReview));
    $("#download-md").title = inEditor ? "请先保存或取消修改，再下载 Markdown" : "下载已保存的报告";
    const dirty = reviewDirty();
    const invalidBug = reviewMode === "bugs" && reviewFields.some(field => field.included.checked && !field.sourceChecks.some(source => source.input.checked));
    $("#save-review").disabled = !dirty || savingReview || invalidBug;
    $("#save-review").textContent = savingReview ? "正在保存…" : "保存修改";
    $("#cancel-review").disabled = savingReview;
    $("#restore-suggestions").disabled = savingReview || !reviewFields.some(field => field.input.value !== field.item.original_text ||
        !field.included.checked || (reviewMode === "bugs" && field.sourceChecks.some(source => !source.input.checked)));
    for (const field of reviewFields) {
        field.input.disabled = savingReview || !field.included.checked;
        field.input.required = field.included.checked;
        field.included.disabled = savingReview;
        field.card.classList.toggle("excluded", !field.included.checked);
        field.removed.hidden = field.included.checked;
        if (reviewMode === "bugs") {
            for (const source of field.sourceChecks) source.input.disabled = savingReview || !field.included.checked;
        }
    }
    if (reviewMode === "bugs") {
        const included = reviewFields.map(bugDraft).filter(item => item.included);
        const feedbackIds = new Set(included.flatMap(item => item.feedback_ids));
        $("#bug-review-summary").textContent = `当前保留 ${included.length} 个问题，涉及 ${feedbackIds.size} 条去重反馈。保存后自动更新报告。`;
    }
    $("#review-draft-status").textContent = savingReview ? "正在保存，请稍候。" : invalidBug ? "保留的问题至少需要一条来源反馈" : dirty ? "有未保存修改 · 保存后再推送或下载 MD" : reviewMode === "bugs" ? "正在查看已保存的 BUG 审核结果" : "正在查看已保存的建议";
    const status = $("#review-status");
    status.classList.toggle("modified", Boolean(reviewState?.modified));
    status.textContent = reviewState?.locked_reason || (reviewState?.modified ? "已人工修改 · 预览、Markdown 下载与飞书推送使用已保存版本" : "");
    status.hidden = !status.textContent;
    const bugStatus = $("#bug-review-status");
    bugStatus.classList.toggle("modified", Boolean(bugReviewState?.modified));
    bugStatus.textContent = bugReviewState?.modified ? "BUG 已人工审核修改 · 报告使用已保存结果" : "";
    bugStatus.hidden = !bugStatus.textContent;
}

function applyReport(report) {
    reviewState = report.review || null;
    bugReviewState = report.bug_review || null;
    $("#report-preview").innerHTML = report.html;
    showPush(report.feishu);
}

function closeReview() {
    editingReview = false;
    reviewFields = [];
    $("#review-editor").hidden = true;
    $("#review-items").replaceChildren();
    $("#report-preview").hidden = false;
    controls();
    reviewControls();
}

function resetReview() {
    editingReview = loadingReview = savingReview = false;
    reviewState = null;
    bugReviewState = null;
    reviewMode = "suggestions";
    reviewFields = [];
    lastPush = {};
    $("#review-editor").hidden = true;
    $("#review-items").replaceChildren();
    reviewControls();
}

function sourceCard(source, sourceToggle = null) {
    const card = document.createElement("div");
    card.className = "review-source";
    const id = document.createElement("span");
    id.className = "source-id";
    id.textContent = `反馈 ID：${source.feedback_id}`;
    const columns = document.createElement("div");
    columns.className = "source-columns";
    for (const [label, text, direction] of [["玩家原文", source.original_text, "auto"], ["AI 中文翻译", source.translation_zh, "ltr"]]) {
        const column = document.createElement("div");
        const title = document.createElement("span");
        title.className = "source-label";
        title.textContent = label;
        const body = document.createElement("p");
        body.className = "source-text";
        body.dir = direction;
        body.textContent = text || "未提供";
        column.append(title, body);
        columns.append(column);
    }
    if (sourceToggle) card.append(sourceToggle);
    card.append(id, columns);
    return card;
}

function sourceList(sources, headingText, renderSource = sourceCard) {
    const list = document.createElement("div");
    list.className = "review-sources";
    const heading = document.createElement("h3");
    heading.textContent = headingText;
    list.append(heading);
    if (sources.length) list.append(renderSource(sources[0]));
    if (sources.length > 1) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = `展开其余 ${sources.length - 1} 条来源`;
        details.append(summary);
        for (const source of sources.slice(1)) details.append(renderSource(source));
        list.append(details);
    }
    return list;
}

function renderReview(detail) {
    reviewFields = [];
    const cards = detail.suggestions.map((item, index) => {
        const card = document.createElement("section");
        card.className = "suggestion-review";
        const heading = document.createElement("div");
        heading.className = "review-item-heading";
        const label = document.createElement("label");
        const keepLabel = document.createElement("label");
        keepLabel.className = "review-toggle";
        const included = document.createElement("input");
        included.type = "checkbox";
        included.checked = item.included !== false;
        included.addEventListener("change", reviewControls);
        const keepText = document.createElement("span");
        keepText.textContent = "保留此建议";
        keepLabel.append(included, keepText);
        const input = document.createElement("textarea");
        input.id = `suggestion-draft-${index}`;
        input.value = item.text;
        input.maxLength = 2000;
        input.rows = 3;
        input.required = included.checked;
        input.addEventListener("input", reviewControls);
        label.htmlFor = input.id;
        label.textContent = `建议 ${index + 1}`;
        heading.append(label, keepLabel);
        const removed = document.createElement("p");
        removed.className = "review-removed-note";
        removed.textContent = "此建议将移出报告，原始反馈仍可在下方核对";
        const ids = document.createElement("p");
        ids.className = "suggestion-ids";
        ids.textContent = `反馈 ID：${item.feedback_ids.join("、")}`;
        const sources = sourceList(item.sources, `参考反馈（共 ${item.sources.length} 条）`);
        card.append(heading, input, removed, ids, sources);
        reviewFields.push({item, input, included, card, removed});
        return card;
    });
    $("#review-items").replaceChildren(...cards);
    $("#review-title").textContent = "核查玩家建议";
    $("#review-description").textContent = "对照原文修改建议，取消“保留此建议”可将其移出报告，保存后生效。";
    $("#restore-suggestions").textContent = "恢复原始建议";
    $("#bug-review-summary").hidden = true;
    $("#review-editor").hidden = false;
    $("#report-preview").hidden = true;
    editingReview = true;
    controls();
    reviewControls();
    (reviewFields.find(field => field.included.checked)?.input || reviewFields[0]?.included)?.focus();
}

function renderBugReview(detail) {
    reviewFields = [];
    const cards = detail.bugs.map((item, index) => {
        const card = document.createElement("section");
        card.className = "suggestion-review bug-review";
        const heading = document.createElement("div");
        heading.className = "review-item-heading";
        const identity = document.createElement("div");
        const code = document.createElement("h3");
        code.textContent = item.code;
        const module = document.createElement("p");
        module.className = "bug-review-module";
        const categories = Array.isArray(item.categories) ? item.categories.join(" / ") : item.categories;
        module.textContent = [item.label, categories].filter(Boolean).join(" · ");
        identity.append(code, module);
        const keepLabel = document.createElement("label");
        keepLabel.className = "review-toggle";
        const included = document.createElement("input");
        included.type = "checkbox";
        included.checked = item.included;
        included.addEventListener("change", reviewControls);
        const keepText = document.createElement("span");
        keepText.textContent = "保留此问题";
        keepLabel.append(included, keepText);
        heading.append(identity, keepLabel);
        const label = document.createElement("label");
        const input = document.createElement("textarea");
        input.id = `bug-draft-${index}`;
        input.value = item.text;
        input.maxLength = 2000;
        input.rows = 3;
        input.required = item.included;
        input.addEventListener("input", reviewControls);
        label.htmlFor = input.id;
        label.textContent = "问题描述";
        const removed = document.createElement("p");
        removed.className = "review-removed-note";
        removed.textContent = "此问题将移出报告，原始反馈仍可在下方核对。";
        const field = {item, input, included, sourceChecks: [], card, removed};
        function selectableSource(source) {
            const sourceLabel = document.createElement("label");
            sourceLabel.className = "review-toggle bug-source-toggle";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = item.feedback_ids.includes(source.feedback_id);
            checkbox.addEventListener("change", reviewControls);
            const text = document.createElement("span");
            text.textContent = "保留此条反馈";
            sourceLabel.append(checkbox, text);
            field.sourceChecks.push({id: source.feedback_id, input: checkbox});
            return sourceCard(source, sourceLabel);
        }
        const sources = sourceList(item.sources, `参考反馈（共 ${item.sources.length} 条）· 可取消归属错误的反馈`, selectableSource);
        card.append(heading, label, input, removed, sources);
        reviewFields.push(field);
        return card;
    });
    $("#review-items").replaceChildren(...cards);
    $("#review-title").textContent = "审核 BUG";
    $("#review-description").textContent = "对照原文修正问题描述，移除误判问题或归属错误的反馈。保存后自动更新 BUG 问题数和去重反馈数。";
    $("#restore-suggestions").textContent = "恢复原始 BUG";
    $("#bug-review-summary").hidden = false;
    $("#review-editor").hidden = false;
    $("#report-preview").hidden = true;
    editingReview = true;
    controls();
    reviewControls();
    (reviewFields.find(field => field.included.checked)?.input || reviewFields[0]?.included)?.focus();
}

async function openReview(mode) {
    const bugs = mode === "bugs";
    if ($(bugs ? "#edit-bugs" : "#edit-suggestions").disabled) return;
    const target = jobUrl();
    reviewMode = mode;
    loadingReview = true;
    controls();
    reviewControls();
    error("");
    try {
        const detail = await api(`${target}/${bugs ? "bugs" : "review"}`);
        if (target !== jobUrl()) return;
        if (bugs) bugReviewState = detail;
        else reviewState = detail;
        if (!detail.editable) throw new Error(detail.locked_reason || (bugs ? "此报告目前无法审核 BUG。" : "此报告目前无法编辑。"));
        if (bugs) renderBugReview(detail);
        else renderReview(detail);
    } catch (failure) {
        if (target === jobUrl()) error(failure.message);
    } finally {
        if (target === jobUrl()) {
            loadingReview = false;
            controls();
            reviewControls();
        }
    }
}

$("#edit-suggestions").addEventListener("click", () => openReview("suggestions"));
$("#edit-bugs").addEventListener("click", () => openReview("bugs"));

$("#cancel-review").addEventListener("click", () => {
    if (savingReview) return;
    closeReview();
    $(reviewMode === "bugs" ? "#edit-bugs" : "#edit-suggestions").focus();
});

$("#restore-suggestions").addEventListener("click", () => {
    if (savingReview) return;
    for (const field of reviewFields) {
        field.input.value = field.item.original_text;
        field.included.checked = true;
        if (reviewMode === "bugs") {
            for (const source of field.sourceChecks) source.input.checked = field.item.original_feedback_ids.includes(source.id);
        }
    }
    reviewControls();
});

$("#review-editor").addEventListener("submit", async event => {
    event.preventDefault();
    if (!editingReview || savingReview || !reviewDirty()) return;
    if (reviewMode === "bugs" && reviewFields.some(field => field.included.checked && !field.sourceChecks.some(source => source.input.checked))) {
        error("保留的问题至少需要一条来源反馈。");
        return;
    }
    const target = jobUrl();
    savingReview = true;
    reviewControls();
    error("");
    try {
        const bugs = reviewMode === "bugs";
        const version = (bugs ? bugReviewState : reviewState).version;
        const body = bugs ? {version, bugs: reviewFields.map(bugDraft)} :
            {version, suggestions: reviewFields.map(({item, input, included}) => ({id: item.id, text: input.value, included: included.checked}))};
        const report = await post(`${target}/${bugs ? "bugs" : "review"}`, body);
        if (target !== jobUrl()) return;
        closeReview();
        applyReport(report);
        $(reviewMode === "bugs" ? "#edit-bugs" : "#edit-suggestions").focus();
    } catch (failure) {
        if (target === jobUrl()) error(failure.message);
    } finally {
        if (target === jobUrl()) {
            savingReview = false;
            controls();
            reviewControls();
        }
    }
});

$("#download-md").addEventListener("click", event => {
    if (!editingReview && !loadingReview && !savingReview) return;
    event.preventDefault();
    error("请先保存或取消修改，再下载 Markdown。");
});

function showPush(data = {}) {
    lastPush = data;
    const button = $("#push-feishu");
    const pushUnknown = data.status === "unknown";
    button.textContent = data.status === "sent" ? "已推送到飞书" : pushUnknown ? "确认本段未收到，继续推送" : data.available ? (data.sent_parts ? "继续推送报告" : "一键推送到飞书") : "未配置飞书推送";
    const progress = data.total_parts ? `已发送 ${data.sent_parts || 0}/${data.total_parts} 段。` : "";
    const message = data.status === "sent" ? `推送成功 · ${data.total_parts || 1} 段 · ${new Date(data.sent_at).toLocaleString()}` : data.status === "unknown" ? `${progress}下一段发送结果未知，请先检查群消息，避免重复推送。` : progress + (data.last_error || data.reason || "");
    $("#push-status").textContent = message;
    $("#push-status").hidden = !message;
    reviewControls();
}

$("#push-feishu").addEventListener("click", async () => {
    const target = jobUrl();
    const button = $("#push-feishu");
    if (button.disabled) return;
    pushing = true;
    controls();
    reviewControls();
    button.textContent = "推送中…";
    $("#push-status").textContent = "正在准备明细并推送报告，请稍候。";
    $("#push-status").hidden = false;
    error("");
    try {
        const data = await post(`${target}/push/feishu`, {retry_unknown: lastPush.status === "unknown", report_version: reviewState?.version || bugReviewState?.version});
        if (target !== jobUrl()) return;
        showPush(data);
        if (reviewState) reviewState = {...reviewState, editable: false, locked_reason: "此报告已开始推送，建议内容已锁定。"};
        if (bugReviewState) bugReviewState = {...bugReviewState, editable: false, locked_reason: "此报告已开始推送，BUG 审核内容已锁定。"};
        const report = await api(`${target}/report`);
        if (target === jobUrl()) applyReport(report);
    } catch (failure) {
        if (target !== jobUrl()) return;
        error(failure.message);
        try {
            const report = await api(`${target}/report`);
            if (target === jobUrl()) applyReport(report);
        }
        catch (_) { button.textContent = "请检查群消息"; }
    } finally {
        pushing = false;
        if (target === jobUrl()) {
            controls();
            reviewControls();
        }
    }
});
function dates() { return {start: $("#start").value, end: $("#end").value, confirmed: $("#confirm-coverage").checked}; }

function controls() {
    const reviewing = editingReview || loadingReview || savingReview || pushing || recovering;
    $("#recovery-open").disabled = uploading || reviewing;
    $("#upload-button").disabled = busy || uploading || reviewing;
    $("#file").disabled = busy || uploading || reviewing;
    $("#start").disabled = busy || reviewing;
    $("#end").disabled = busy || reviewing;
    $("#confirm-coverage").disabled = busy || reviewing;
    $("#generate-button").disabled = busy || uploading || reviewing || blockedJob || !recoveryChecked || filePending || reportComplete || !selection?.ready;
    $("#generate-button").textContent = reportComplete ? "报告已生成" : busy ? "生成中…" : "生成报告 ↗";
    $("#job-spinner").hidden = !busy;
}

async function showPage(number) {
    const data = await api(`${runUrl()}/preview?page=${number}`);
    const header = document.createElement("tr");
    const extraInfoColumn = data.columns.indexOf("附加信息");
    for (const [index, name] of data.columns.entries()) {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.textContent = name;
        if (index === extraInfoColumn) cell.classList.add("extra-info-column");
        header.append(cell);
    }
    $("#data-table thead").replaceChildren(header);
    const rows = data.rows.map(values => {
        const row = document.createElement("tr");
        for (const [index, value] of values.entries()) {
            const cell = document.createElement("td");
            cell.dir = "auto";
            if (index === extraInfoColumn) cell.classList.add("extra-info-column");
            if (index === extraInfoColumn && value.length > 120) {
                const details = document.createElement("details");
                const summary = document.createElement("summary");
                const content = document.createElement("span");
                content.className = "extra-info-text";
                content.textContent = value;
                summary.title = "点击展开或收起完整附加信息";
                summary.append(content);
                details.append(summary);
                cell.append(details);
            } else {
                cell.textContent = value;
            }
            row.append(cell);
        }
        return row;
    });
    $("#data-table tbody").replaceChildren(...rows);
    page = data.page;
    totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
    $("#page-info").textContent = `第 ${page} / ${totalPages} 页 · 共 ${data.total.toLocaleString()} 行`;
    $("#prev-page").disabled = page <= 1;
    $("#next-page").disabled = page >= totalPages;
    $(".table-scroll").scrollTop = 0;
}

async function checkSelection() {
    const version = ++selectionVersion;
    selection = null;
    controls();
    if (!run || !$("#start").value || !$("#end").value) return;
    try {
        const data = await post(`${runUrl()}/selection`, dates());
        if (version !== selectionVersion) return;
        selection = data;
        error("");
        $("#selection-summary").hidden = false;
        const previous = data.periods.previous;
        $("#previous-period").textContent = `${previous.start} — ${previous.end}`;
        $("#period-counts").textContent = `本期待分析 ${data.current_count.toLocaleString()} 条 · 上周同期待分析 ${data.previous_count.toLocaleString()} 条`;
        $("#selection-note").textContent = `仅处理两期共 ${data.selected_count.toLocaleString()} 条候选反馈，范围外 ${data.outside_count.toLocaleString()} 条不参与处理。两期已按数据规则排除 ${data.excluded_in_period} 条。`;
        const noRecords = [...new Set([...data.days_without_records.current, ...data.days_without_records.previous])];
        const warning = $("#coverage-warning");
        warning.hidden = !noRecords.length && data.inferred_covered;
        if (!data.inferred_covered) {
            warning.textContent = `文件记录范围为 ${data.source_start} 至 ${data.source_end}，未覆盖所需期间边界。请补充文件；若缺少的日期确实没有反馈，可在核对完整导出后确认。`;
        } else if (noRecords.length) {
            warning.textContent = `有 ${noRecords.length} 天没有反馈记录（${noRecords.slice(0, 5).join("、")}${noRecords.length > 5 ? " 等" : ""}）。无记录不一定代表缺失，请核对源文件是否完整导出。`;
        }
        $("#confirm-label").hidden = data.inferred_covered;
    } catch (failure) {
        if (version !== selectionVersion) return;
        $("#selection-summary").hidden = true;
        error(failure.message);
    }
    controls();
}

async function loadUpload(metadata, selected = null) {
    run = metadata.run;
    reportComplete = false;
    resetReview();
    $("#content-section").hidden = false;
    $("#report-form").hidden = false;
    $("#file-summary").textContent = `${metadata.total.toLocaleString()} 行 · ${metadata.valid.toLocaleString()} 条待分析 · ${metadata.excluded.toLocaleString()} 条排除`;
    $("#file-label").textContent = metadata.filename;
    $("#file-label").title = metadata.filename;
    $("#change-file").hidden = false;
    $("#date-span").textContent = `${metadata.source_start} — ${metadata.source_end}`;
    $("#start").value = selected?.start || metadata.suggested_start;
    $("#end").value = selected?.end || metadata.suggested_end;
    $("#confirm-coverage").checked = selected?.confirmed === true;
    await showPage(1);
    await checkSelection();
    switchTab("data");
}

$("#file").addEventListener("change", () => {
    $("#file-label").textContent = $("#file").files[0]?.name || "点击选择文件，或拖入这里";
    $("#file-label").title = $("#file").files[0]?.name || "";
    $("#file-summary").textContent = uploadHint;
    filePending = $("#file").files.length > 0;
    $("#change-file").hidden = !filePending && !run;
    controls();
});
for (const type of ["dragenter", "dragover"]) $("#upload-zone").addEventListener(type, () => $("#upload-zone").classList.add("dragging"));
for (const type of ["dragleave", "drop"]) $("#upload-zone").addEventListener(type, () => $("#upload-zone").classList.remove("dragging"));

$("#upload-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || uploading || recovering || editingReview || loadingReview || savingReview || pushing) return;
    // Capture before disabling controls: disabled file inputs are omitted from FormData.
    const formData = new FormData(event.target);
    uploading = true;
    controls();
    error("");
    $("#upload-button").textContent = "正在读取…";
    try {
        const metadata = await api(base, {method: "POST", body: formData});
        forgetJob();
        if (!blockedJob) $("#report-recovery").hidden = true;
        filePending = false;
        clearTimeout(timer);
        job = null;
        $("#job-status").hidden = true;
        $("#download-md").removeAttribute("data-ready");
        $("#downloads").hidden = true;
        $("#report-preview").hidden = true;
        $("#report-preview").replaceChildren();
        $("#report-empty").hidden = false;
        $("#result-title").textContent = "报告尚未生成";
        await loadUpload(metadata);
    } catch (failure) { error(failure.message); }
    finally {
        uploading = false;
        $("#upload-button").textContent = "上传并预览";
        controls();
    }
});

$("#prev-page").addEventListener("click", () => showPage(Math.max(1, page - 1)).catch(f => error(f.message)));
$("#next-page").addEventListener("click", () => showPage(Math.min(totalPages, page + 1)).catch(f => error(f.message)));
for (const id of ["#start", "#end"]) $(id).addEventListener("change", () => {
    reportComplete = false;
    $("#confirm-coverage").checked = false;
    return checkSelection();
});
$("#confirm-coverage").addEventListener("change", () => {
    reportComplete = false;
    return checkSelection();
});

async function displayJob(data) {
    busy = ["running", "stopping"].includes(data.status);
    reportComplete = data.status === "complete";
    controls();
    $("#job-status").hidden = false;
    $("#job-message").textContent = data.message + (data.total ? ` · ${data.done} / ${data.total}` : "");
    $("#stop-button").hidden = !busy;
    $("#stop-button").disabled = data.status === "stopping";
    $("#job-progress").hidden = !busy;
    if (data.total) {
        $("#job-progress").max = data.total;
        $("#job-progress").value = data.done;
    } else $("#job-progress").removeAttribute("value");
    const complete = data.status === "complete";
    $("#result-title").textContent = complete ? "报告已完成" : "报告进度";
    $("#downloads").hidden = !complete || $("#report-tab").getAttribute("aria-selected") !== "true";
    if (complete) {
        $("#download-md").href = `${jobUrl()}/download/markdown?workspace=${encodeURIComponent(workspaceToken)}`;
        $("#download-excel").href = `${jobUrl()}/download/excel?workspace=${encodeURIComponent(workspaceToken)}`;
        $("#download-md").dataset.ready = "true";
        const p = data.result.selection.periods.current;
        $("#job-message").textContent = `${p.start} — ${p.end} · 本期 ${data.result.total} 条 · 上周同期 ${data.result.previous_total} 条`;
        const finalSelection = data.result.selection;
        if (Number.isInteger(finalSelection.processed_count)) {
            $("#period-counts").textContent = `本期有效 ${data.result.total.toLocaleString()} 条 · 上周同期有效 ${data.result.previous_total.toLocaleString()} 条`;
            $("#selection-note").textContent = `已分析 ${finalSelection.processed_count.toLocaleString()} 条候选反馈，保留两期共 ${finalSelection.selected_count.toLocaleString()} 条有效反馈。` +
                (finalSelection.semantic_excluded_in_period ? `另排除 ${finalSelection.semantic_excluded_in_period} 条内容不明确或无法理解的反馈，原文与原因见 Excel“排除反馈明细”。` : "") +
                (finalSelection.abusive_excluded_in_period ? `已剔除 ${finalSelection.abusive_excluded_in_period} 条含辱骂内容的反馈，不计入统计，也不导出。` : "");
        }
        const target = jobUrl();
        const report = await api(`${target}/report`);
        if (target !== jobUrl()) return;
        applyReport(report);
        $("#report-preview").hidden = false;
        $("#report-empty").hidden = true;
        switchTab("report");
    }
    clearTimeout(timer);
    if (busy) timer = setTimeout(pollJob, 1000);
}

async function pollJob() {
    const target = jobUrl();
    try {
        const data = await api(target);
        if (target === jobUrl()) await displayJob(data);
    }
    catch (failure) {
        if (target !== jobUrl()) return;
        $("#job-message").textContent = "连接暂时中断，正在重新获取进度…";
        timer = setTimeout(pollJob, 3000);
    }
}

$("#report-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || uploading || recovering || blockedJob || !recoveryChecked || editingReview || loadingReview || savingReview || pushing || filePending || reportComplete || !selection?.ready) return;
    busy = true;
    controls();
    $("#job-status").hidden = false;
    $("#job-message").textContent = "正在准备生成报告…";
    $("#job-progress").hidden = false;
    $("#job-progress").removeAttribute("value");
    error("");
    try {
        const data = await post(`${runUrl()}/reports`, dates());
        job = data.id;
        resetReview();
        $("#report-preview").replaceChildren();
        $("#report-preview").hidden = true;
        $("#report-empty").hidden = false;
        $("#download-md").removeAttribute("data-ready");
        $("#downloads").hidden = true;
        await displayJob(data);
    } catch (failure) {
        busy = false;
        controls();
        $("#job-status").hidden = true;
        if (failure.busy) {
            await checkAvailability();
        } else error(failure.message);
    }
});

forgetJob();
const workspaceReady = checkAvailability();
heartbeatTimer = setTimeout(heartbeat, 60000);

$("#stop-button").addEventListener("click", async () => {
    try { await displayJob(await post(`${jobUrl()}/stop`, {})); }
    catch (failure) { error(failure.message); }
});

