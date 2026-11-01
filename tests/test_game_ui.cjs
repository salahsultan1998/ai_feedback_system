// Browser-independent interaction checks. No network or model requests.
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const html = fs.readFileSync("templates/game.html", "utf8");
const script = fs.readFileSync("static/js/game.js", "utf8");

function boot(options = {}) {
    const created = [];
    function element() {
        const node = {
            hidden: true, disabled: false, value: "", files: [], checked: false,
            dataset: {}, attributes: {}, listeners: {}, children: [], content: "test-token",
            classList: {toggle() {}, add() {}, remove() {}},
            addEventListener(type, fn) { this.listeners[type] = fn; },
            setAttribute(k, v) { this.attributes[k] = v; },
            getAttribute(k) { return this.attributes[k]; },
            removeAttribute(k) { delete this.attributes[k]; if (k === "data-ready") delete this.dataset.ready; },
            append(...items) { this.children.push(...items); },
            replaceChildren(...items) { this.children = items; },
            focus() {}, reset() { this.wasReset = true; }
        };
        created.push(node);
        return node;
    }
    const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => ["#" + m[1], element()]));
    for (const selector of ['meta[name="csrf-token"]', "#data-table thead", "#data-table tbody", ".table-scroll"]) nodes.set(selector, element());
    nodes.get("#workspace").dataset.game = "baloot";
    const calls = [];
    const requests = [], windowListeners = {};
    const server = {pushStatus: options.pushStatus || "unknown", version: "revision-1", html: "<h1>Report</h1>",
        suggestions: [{id:"s0", text:"增加相册入口", original_text:"增加相册入口", feedback_ids:["0001", "0002"],
            sources:[{feedback_id:"0001",original_text:"أريد إضافة ألبوم الصور",translation_zh:"希望添加相册。"},
                {feedback_id:"0002",original_text:"<script>unsafe()</script>",translation_zh:"希望增加相册入口。"}]}]};
    function review() {
        return {available:true, editable:server.pushStatus === "ready", locked_reason:server.pushStatus === "ready" ? "" : "此报告已开始推送，建议内容已锁定。",
            version:server.version, modified:server.suggestions.some(item=>item.text!==item.original_text), updated_at:null};
    }
    let url = "http://localhost/games/baloot?upload=old-upload";
    const context = vm.createContext({
        URL, console, setTimeout: () => 1, clearTimeout() {},
        window: { location: {href: url, reload() {}}, addEventListener(type, fn) { windowListeners[type] = fn; } },
        history: {replaceState(a, b, next) { url = String(next); }},
        document: {
            querySelector(selector) { assert(nodes.has(selector), "Missing DOM selector " + selector); return nodes.get(selector); },
            createElement: element
        },
        FormData: class { constructor() { assert.equal(nodes.get("#file").disabled, false, "File captured before disabling"); } },
        fetch: async (path, options) => {
            calls.push(path);
            requests.push({path, options});
            let data;
            if (path.includes("/preview?")) data = {columns: ["反馈ID"], rows: [["0001"]], page: 1, page_size: 50, total: 1};
            else if (path.endsWith("/selection")) data = {
                ready: true, periods: {previous: {start:"2026-10-12",end:"2026-10-15"}},
                current_count: 1, previous_count: 1, selected_count: 2, outside_count: 0,
                excluded_in_period: 0, days_without_records: {current:[],previous:[]}, inferred_covered: true
            };
            else if (path.endsWith("/reports")) data = {
                id: "job", status: "complete", message: "完成", done: 2, total: 2,
                result: {total:1, previous_total:1, selection:{periods:{current:{start:"2026-10-19",end:"2026-10-22"}}}}
            };
            else if (path.endsWith("/report")) data = {html:server.html, feishu:{available:true,status:server.pushStatus,sent_at:"2026-10-26T00:00:00+00:00"},review:review()};
            else if (path.endsWith("/review")) {
                if (options.method === "POST") {
                    const body = JSON.parse(options.body);
                    assert.equal(body.version, server.version);
                    assert.deepEqual(Object.keys(body.suggestions[0]).sort(), ["id", "text"]);
                    for (const item of body.suggestions) server.suggestions.find(s=>s.id===item.id).text = item.text;
                    server.version = `revision-${Number(server.version.split("-")[1]) + 1}`;
                    server.html = `<h1>Report</h1><p>${server.suggestions[0].text}</p>`;
                    data = {html:server.html, feishu:{available:true,status:server.pushStatus}, review:review()};
                } else data = {...review(), suggestions:JSON.parse(JSON.stringify(server.suggestions))};
            }
            else if (path.endsWith("/push/feishu")) {
                assert.equal(JSON.parse(options.body).retry_unknown, server.pushStatus === "unknown");
                assert.equal(JSON.parse(options.body).report_version, server.version);
                server.pushStatus = "sent";
                data = {available:true,status:"sent",sent_at:"2026-10-26T00:00:00+00:00"};
            }
            else data = {run:"new-upload", filename:"sample.csv",total:1,valid:1,excluded:0,
                source_start:"2026-10-12",source_end:"2026-10-22",suggested_start:"2026-10-19",suggested_end:"2026-10-22"};
            return {ok:true,json:async()=>data};
        }
    });
    vm.runInContext(script, context);
    return {context, nodes, calls, requests, server, created, windowListeners, url:()=>url};
}

(async () => {
    const {context, nodes, calls, url} = boot();
    const get = id => nodes.get("#" + id);
    assert(!url().includes("upload="), "Old upload link removed on initial load");
    assert.equal(calls.length, 0, "Refresh must not restore uploaded data");
    assert(get("upload-form").wasReset && get("report-form").wasReset);
    await get("upload-form").listeners.submit({preventDefault(){},target:get("upload-form")});
    assert.equal(get("content-section").hidden,false);
    assert.equal(get("report-form").hidden,false);
    assert.equal(get("data-section").hidden,false);
    assert.equal(get("result-section").hidden,true);
    assert.equal(get("generate-button").disabled,false);
    assert.equal(get("change-file").hidden,false);
    await vm.runInContext('displayJob({status:"running",message:"正在处理",done:1,total:2})', context);
    assert.equal(get("job-spinner").hidden,false);
    assert.equal(get("job-progress").hidden,false);
    assert.equal(get("generate-button").disabled,true);
    assert.equal(get("generate-button").textContent,"生成中…");
    await vm.runInContext('displayJob({status:"failed",message:"处理失败",done:1,total:2})', context);
    assert.equal(get("job-spinner").hidden,true);
    assert.equal(get("generate-button").disabled,false);
    get("report-tab").listeners.click();
    assert.equal(get("data-section").hidden,true);
    assert.equal(get("result-section").hidden,false);
    await get("report-form").listeners.submit({preventDefault(){}});
    assert.equal(get("report-empty").hidden,true);
    assert.equal(get("report-preview").hidden,false);
    assert.equal(get("downloads").hidden,false);
    assert.equal(get("job-spinner").hidden,true);
    assert.equal(get("generate-button").disabled,true);
    assert.equal(get("generate-button").textContent,"报告已生成");
    const reportCalls = calls.filter(path => path.endsWith("/reports")).length;
    await get("report-form").listeners.submit({preventDefault(){}});
    assert.equal(calls.filter(path => path.endsWith("/reports")).length, reportCalls, "Completed report cannot be submitted twice");
    assert.equal(get("push-feishu").disabled,false);
    assert.equal(get("push-feishu").textContent,"确认本段未收到，继续推送");
    await get("push-feishu").listeners.click();
    assert.equal(get("push-feishu").textContent,"已推送到飞书");
    assert.equal(get("push-feishu").disabled,true);
    get("data-tab").listeners.click();
    assert.equal(get("downloads").hidden,true);
    get("report-tab").listeners.click();
    assert.equal(get("downloads").hidden,false);
    await vm.runInContext('displayJob({status:"complete",message:"完成",done:4,total:4,result:{total:1,previous_total:2,selection:{periods:{current:{start:"2026-10-19",end:"2026-10-22"}},processed_count:4,selected_count:3,semantic_excluded_in_period:1}}})', context);
    assert.equal(get("period-counts").textContent,"本期有效 1 条 · 上周同期有效 2 条");
    assert(get("selection-note").textContent.includes("另排除 1 条"));
    await vm.runInContext('displayJob({status:"complete",message:"完成",done:5,total:5,result:{total:1,previous_total:2,selection:{periods:{current:{start:"2026-10-19",end:"2026-10-22"}},processed_count:5,selected_count:3,semantic_excluded_in_period:1,abusive_excluded_in_period:1}}})', context);
    assert(get("selection-note").textContent.includes("已剔除 1 条含辱骂内容的反馈，不计入统计，也不导出。"));
    get("end").value = "2026-10-23";
    await get("end").listeners.change();
    assert.equal(get("generate-button").disabled,false);
    assert.equal(get("generate-button").textContent,"生成报告 ↗");
    const fresh = boot();
    assert.equal(fresh.calls.length,0);
    assert.equal(fresh.nodes.get("#content-section").hidden,true);

    const reviewApp = boot({pushStatus:"ready"});
    const n = id => reviewApp.nodes.get("#" + id);
    const fields = () => vm.runInContext("reviewFields", reviewApp.context);
    const dirty = () => vm.runInContext("reviewDirty()", reviewApp.context);
    async function draft(text) {
        fields()[0].input.value = text;
        fields()[0].input.listeners.input();
    }
    await n("upload-form").listeners.submit({preventDefault(){},target:n("upload-form")});
    await n("report-form").listeners.submit({preventDefault(){}});
    assert.equal(n("edit-suggestions").disabled, false);
    await n("edit-suggestions").listeners.click();
    assert.equal(n("review-editor").hidden, false);
    assert.equal(n("report-preview").hidden, true);
    assert.equal(n("push-feishu").disabled, true);
    assert.equal(n("file").disabled, true);
    assert.equal(n("start").disabled, true);
    assert.equal(n("download-md").getAttribute("aria-disabled"), "true");
    assert.equal(dirty(), false);
    const originals = reviewApp.created.filter(item=>item.className==="source-text");
    assert(originals.some(item=>item.textContent==="أريد إضافة ألبوم الصور" && item.dir==="auto"));
    assert(originals.some(item=>item.textContent==="<script>unsafe()</script>" && !item.innerHTML), "Source text is never inserted as HTML");
    assert(reviewApp.created.some(item=>item.textContent==="展开其余 1 条来源"));
    let prevented = false;
    n("download-md").listeners.click({preventDefault(){prevented=true;}});
    assert(prevented, "Cannot download an older MD while editing a draft");
    await draft("新增相册并调整入口");
    assert.equal(dirty(), true);
    assert.equal(n("save-review").disabled, false);
    let beforeUnload = {preventDefault(){this.prevented=true;}};
    reviewApp.windowListeners.beforeunload(beforeUnload);
    assert.equal(beforeUnload.prevented, true);
    n("restore-suggestions").listeners.click();
    assert.equal(fields()[0].input.value,"增加相册入口");
    assert.equal(dirty(),false);
    await draft("不会保存的草稿");
    n("cancel-review").listeners.click();
    assert.equal(n("review-editor").hidden,true);
    assert.equal(n("report-preview").hidden,false);
    assert.equal(reviewApp.requests.filter(item=>item.path.endsWith("/review")&&item.options.method==="POST").length,0);
    assert.equal(n("push-feishu").disabled,false);
    assert.equal(n("file").disabled,false);
    await n("edit-suggestions").listeners.click();
    await draft("增加相册入口，方便玩家分享照片");
    await n("review-editor").listeners.submit({preventDefault(){}});
    assert.equal(n("review-editor").hidden,true);
    assert(n("report-preview").innerHTML.includes("增加相册入口，方便玩家分享照片"));
    assert(n("review-status").textContent.includes("已人工修改"));
    assert.equal(reviewApp.server.version,"revision-2");
    assert.equal(n("download-md").getAttribute("aria-disabled"),"false");
    beforeUnload = {preventDefault(){this.prevented=true;}};
    reviewApp.windowListeners.beforeunload(beforeUnload);
    assert(!beforeUnload.prevented, "Saved content does not warn on leaving");
    await n("edit-suggestions").listeners.click();
    n("restore-suggestions").listeners.click();
    assert.equal(fields()[0].input.value,"增加相册入口");
    n("cancel-review").listeners.click();
    assert.equal(reviewApp.server.suggestions[0].text,"增加相册入口，方便玩家分享照片", "Restore only changes the draft until saved");
    await n("push-feishu").listeners.click();
    const pushRequest = reviewApp.requests.find(item=>item.path.endsWith("/push/feishu"));
    assert.equal(JSON.parse(pushRequest.options.body).report_version,"revision-2");
    assert.equal(n("edit-suggestions").disabled,true);
    assert(n("review-status").textContent.includes("锁定"));
    assert.equal(reviewApp.requests.filter(item=>item.path.endsWith("/reports")).length,1,"Editing never generates another AI report");
    console.log("UI interactions passed: report workflow, source references, suggestion drafts/save/cancel/restore, push revision and locking.");
})().catch(error => {console.error(error);process.exitCode=1;});
