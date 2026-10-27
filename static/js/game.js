/* Workspace behavior. Business rules and configuration stay on the server. */
const $ = selector => document.querySelector(selector);
const workspace = $("#workspace");
const base = `/api/games/${encodeURIComponent(workspace.dataset.game)}/uploads`;
const csrf = $('meta[name="csrf-token"]').content;
let run = null, page = 1, totalPages = 1, selection = null, job = null;
let busy = false, uploading = false, filePending = false, timer = null, selectionVersion = 0;
let pushUnknown = false;

// Start fresh on reload, including links created by the previous layout.
const cleanUrl = new URL(window.location.href);
cleanUrl.searchParams.delete("upload");
history.replaceState(null, "", cleanUrl);
$("#upload-form").reset();
$("#report-form").reset();
window.addEventListener("pageshow", event => {
    if (event.persisted) window.location.reload();
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
        headers: {"X-CSRF-Token": csrf, ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "请求失败，请刷新页面后重试。");
    return data;
}

function post(url, data) {
    return api(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
}

function runUrl() { return `${base}/${run}`; }
function jobUrl() { return `${runUrl()}/jobs/${job}`; }
function showPush(data = {}) {
    const button = $("#push-feishu");
    pushUnknown = data.status === "unknown";
    button.disabled = !data.available || data.status === "sent";
    button.textContent = data.status === "sent" ? "已推送到飞书" : pushUnknown ? "确认本段未收到，继续推送" : data.available ? (data.sent_parts ? "继续推送报告" : "一键推送到飞书") : "未配置飞书推送";
    const progress = data.total_parts ? `已发送 ${data.sent_parts || 0}/${data.total_parts} 段。` : "";
    const message = data.status === "sent" ? `推送成功 · ${data.total_parts || 1} 段 · ${new Date(data.sent_at).toLocaleString()}` : data.status === "unknown" ? `${progress}下一段发送结果未知，请先检查群消息，避免重复推送。` : progress + (data.last_error || data.reason || "");
    $("#push-status").textContent = message;
    $("#push-status").hidden = !message;
}

$("#push-feishu").addEventListener("click", async () => {
    const target = jobUrl();
    const button = $("#push-feishu");
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "推送中…";
    $("#push-status").textContent = "正在准备明细并推送报告，请稍候。";
    $("#push-status").hidden = false;
    error("");
    try {
        const data = await post(`${target}/push/feishu`, {retry_unknown: pushUnknown});
        if (target === jobUrl()) showPush(data);
    } catch (failure) {
        if (target !== jobUrl()) return;
        error(failure.message);
        try { showPush((await api(`${target}/report`)).feishu); }
        catch (_) { button.textContent = "请检查群消息"; }
    }
});
function dates() { return {start: $("#start").value, end: $("#end").value, confirmed: $("#confirm-coverage").checked}; }

function controls() {
    $("#upload-button").disabled = busy || uploading;
    $("#file").disabled = busy || uploading;
    $("#start").disabled = busy;
    $("#end").disabled = busy;
    $("#confirm-coverage").disabled = busy;
    $("#generate-button").disabled = busy || uploading || filePending || !selection?.ready;
}

async function showPage(number) {
    const data = await api(`${runUrl()}/preview?page=${number}`);
    const header = document.createElement("tr");
    for (const name of data.columns) {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.textContent = name;
        header.append(cell);
    }
    $("#data-table thead").replaceChildren(header);
    const rows = data.rows.map(values => {
        const row = document.createElement("tr");
        for (const value of values) {
            const cell = document.createElement("td");
            cell.textContent = value;
            cell.dir = "auto";
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
        $("#period-counts").textContent = `本期 ${data.current_count.toLocaleString()} 条 · 上周同期 ${data.previous_count.toLocaleString()} 条`;
        $("#selection-note").textContent = `仅处理两期共 ${data.selected_count.toLocaleString()} 条有效反馈，范围外 ${data.outside_count.toLocaleString()} 条不参与处理。两期排除 ${data.excluded_in_period} 条。`;
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

async function loadUpload(metadata) {
    run = metadata.run;
    $("#content-section").hidden = false;
    $("#report-form").hidden = false;
    $("#file-summary").textContent = `${metadata.total.toLocaleString()} 行 · ${metadata.valid.toLocaleString()} 条有效 · ${metadata.excluded.toLocaleString()} 条排除`;
    $("#file-label").textContent = metadata.filename;
    $("#change-file").hidden = false;
    $("#date-span").textContent = `${metadata.source_start} — ${metadata.source_end}`;
    $("#upload-note").hidden = true;
    $("#start").value = metadata.suggested_start;
    $("#end").value = metadata.suggested_end;
    $("#confirm-coverage").checked = false;
    await showPage(1);
    await checkSelection();
    switchTab("data");
}

$("#file").addEventListener("change", () => {
    $("#file-label").textContent = $("#file").files[0]?.name || "点击选择或拖入反馈文件";
    filePending = $("#file").files.length > 0;
    controls();
});
for (const type of ["dragenter", "dragover"]) $("#upload-zone").addEventListener(type, () => $("#upload-zone").classList.add("dragging"));
for (const type of ["dragleave", "drop"]) $("#upload-zone").addEventListener(type, () => $("#upload-zone").classList.remove("dragging"));

$("#upload-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || uploading) return;
    // Capture before disabling controls: disabled file inputs are omitted from FormData.
    const formData = new FormData(event.target);
    uploading = true;
    controls();
    error("");
    $("#upload-button").textContent = "正在读取…";
    try {
        const metadata = await api(base, {method: "POST", body: formData});
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
    $("#confirm-coverage").checked = false;
    checkSelection();
});
$("#confirm-coverage").addEventListener("change", checkSelection);

async function displayJob(data) {
    busy = ["running", "stopping"].includes(data.status);
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
        $("#download-md").href = `${jobUrl()}/download/markdown`;
        $("#download-excel").href = `${jobUrl()}/download/excel`;
        $("#download-md").dataset.ready = "true";
        const p = data.result.selection.periods.current;
        $("#job-message").textContent = `${p.start} — ${p.end} · 本期 ${data.result.total} 条 · 上周同期 ${data.result.previous_total} 条`;
        const report = await api(`${jobUrl()}/report`);
        showPush(report.feishu);
        $("#report-preview").innerHTML = report.html;
        $("#report-preview").hidden = false;
        $("#report-empty").hidden = true;
        switchTab("report");
    }
    clearTimeout(timer);
    if (busy) timer = setTimeout(pollJob, 1000);
}

async function pollJob() {
    try { await displayJob(await api(jobUrl())); }
    catch (failure) {
        $("#job-message").textContent = "连接暂时中断，正在重新获取进度…";
        timer = setTimeout(pollJob, 3000);
    }
}

$("#report-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !selection?.ready) return;
    busy = true;
    controls();
    error("");
    try {
        const data = await post(`${runUrl()}/reports`, dates());
        job = data.id;
        $("#report-preview").replaceChildren();
        $("#report-preview").hidden = true;
        $("#report-empty").hidden = false;
        $("#download-md").removeAttribute("data-ready");
        $("#downloads").hidden = true;
        await displayJob(data);
    } catch (failure) { busy = false; controls(); error(failure.message); }
});

$("#stop-button").addEventListener("click", async () => {
    try { await displayJob(await post(`${jobUrl()}/stop`, {})); }
    catch (failure) { error(failure.message); }
});

