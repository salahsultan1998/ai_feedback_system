// Browser-independent interaction checks. No network or model requests.
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const html = fs.readFileSync("templates/game.html", "utf8");
const script = fs.readFileSync("static/js/game.js", "utf8");

function boot(options = {}) {
    const fixtureOptions = options;
    const created = [];
    function element() {
        let disabled = false;
        const node = {
            hidden: true, value: "", files: [], checked: false,
            get disabled() { return disabled; },
            set disabled(value) { disabled = Boolean(value); },
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
    const game = options.game || "baloot";
    nodes.get("#workspace").dataset.game = game;
    const calls = [];
    const requests = [], windowListeners = {};
    const server = {pushStatus: options.pushStatus || "unknown", version: "revision-1", html: "<h1>Report</h1>",
        suggestions: JSON.parse(JSON.stringify(options.suggestions || [{id:"s0", text:"增加相册入口", original_text:"增加相册入口", included:true, feedback_ids:["0001", "0002"],
            sources:[{feedback_id:"0001",original_text:"أريد إضافة ألبوم الصور",translation_zh:"希望添加相册。"},
                {feedback_id:"0002",original_text:"<script>unsafe()</script>",translation_zh:"希望增加相册入口。"}]}])),
        bugs: options.bugs || [{id:"b0",code:"BUG-001",label:"账号问题",categories:"无法登录",text:"玩家反映无法登录。",original_text:"玩家反映无法登录。",included:true,
            feedback_ids:["0001","0002"],original_feedback_ids:["0001","0002"],
            sources:[{feedback_id:"0001",original_text:"لا أستطيع الدخول",translation_zh:"无法进入游戏。"},
                {feedback_id:"0002",original_text:"登录失败",translation_zh:"登录失败。"}]},
            {id:"b1",code:"BUG-002",label:"玩法相关",categories:["活动","许愿树"],text:"活动入口打不开。",original_text:"活动入口打不开。",included:true,
            feedback_ids:["0002","0003"],original_feedback_ids:["0002","0003"],
            sources:[{feedback_id:"0002",original_text:"登录失败",translation_zh:"登录失败。"},
                {feedback_id:"0003",original_text:"活动打不开",translation_zh:"活动打不开。"}]}]};
    function review() {
        return {available:true, editable:server.pushStatus === "ready", locked_reason:server.pushStatus === "ready" ? "" : "此报告已开始推送，建议内容已锁定。",
            version:server.version, modified:server.suggestions.some(item=>item.text!==item.original_text || !item.included)};
    }
    function bugReview() {
        const available = options.bugAvailable !== false;
        return {available, editable:available && server.bugs.length > 0 && server.pushStatus === "ready",
            locked_reason:!available ? "此报告未保存 BUG 审核来源，请重新生成报告。" : server.bugs.length === 0 ? "本期未发现 BUG 候选。" : server.pushStatus === "ready" ? "" : "此报告已开始推送，BUG 审核已锁定。",
            version:server.version, modified:server.bugs.some(item=>item.text!==item.original_text || !item.included || item.feedback_ids.length!==item.original_feedback_ids.length || item.feedback_ids.some(id=>!item.original_feedback_ids.includes(id)))};
    }
    function reportResponse() {
        return {html:server.html, feishu:{available:true,status:server.pushStatus,sent_at:"2026-10-26T00:00:00+00:00"}, review:review(), bug_review:bugReview()};
    }
    function reportHtml() {
        const suggestions = server.suggestions.filter(item=>item.included);
        const suggestionHtml = suggestions.length ? suggestions.map((item, index)=>`<p>${index + 1}. ${item.text}</p>`).join("") :
            `<p>${server.suggestions.length ? "本期建议已全部移除。" : "本期未发现明确、具体的产品改进建议。"}</p>`;
        const bugs = server.bugs.filter(item=>item.included);
        const count = new Set(bugs.flatMap(item=>item.feedback_ids)).size;
        return `<h1>Report</h1>${suggestionHtml}<p>BUG 问题数：${bugs.length}；涉及反馈：${count}</p>` + bugs.map(item=>`<p>${item.code} ${item.text}</p>`).join("");
    }
    server.html = reportHtml();
    let currentUrl = options.url || `http://localhost/games/${game}?upload=old-upload`;
    const location = {
        get href() { return currentUrl; },
        set href(value) { currentUrl = new URL(String(value), currentUrl).href; },
        reload() { this.reloaded = true; }
    };
    const stored = new Map();
    const sessionStorage = options.sessionStorage || {
        getItem(key) { return stored.get(key) ?? null; },
        setItem(key, value) { stored.set(key, String(value)); },
        removeItem(key) { stored.delete(key); }
    };
    const timers = new Map();
    let nextTimer = 0;
    const context = vm.createContext({
        URL, console,
        setTimeout(callback, delay) { timers.set(++nextTimer, {callback, delay}); return nextTimer; },
        clearTimeout(id) { timers.delete(id); },
        window: { location, sessionStorage, addEventListener(type, fn) { windowListeners[type] = fn; } },
        history: {replaceState(a, b, next) { location.href = String(next); }},
        document: {
            querySelector(selector) { assert(nodes.has(selector), "Missing DOM selector " + selector); return nodes.get(selector); },
            createElement: element
        },
        FormData: class { constructor() { assert.equal(nodes.get("#file").disabled, false, "File captured before disabling"); } },
        fetch: async (path, options) => {
            calls.push(path);
            requests.push({path, options});
            if (fixtureOptions.respond) {
                const response = await fixtureOptions.respond(path, options, server);
                if (response !== undefined) return response;
            }
            let data;
            if (path === "/api/jobs/active") data = {job:null};
            else if (path.includes("/preview?")) data = {columns: ["反馈ID"], rows: [["0001"]], page: 1, page_size: 50, total: 1};
            else if (path.endsWith("/selection")) data = {
                ready: true, periods: {previous: {start:"2026-10-12",end:"2026-10-15"}},
                current_count: 1, previous_count: 1, selected_count: 2, outside_count: 0,
                excluded_in_period: 0, days_without_records: {current:[],previous:[]}, inferred_covered: true
            };
            else if (path.endsWith("/reports")) data = {
                id: "job", status: "complete", message: "完成", done: 2, total: 2,
                result: {total:1, previous_total:1, selection:{periods:{current:{start:"2026-10-19",end:"2026-10-22"}}}}
            };
            else if (path.endsWith("/report")) data = reportResponse();
            else if (path.endsWith("/review")) {
                if (options.method === "POST") {
                    const body = JSON.parse(options.body);
                    assert.equal(body.version, server.version);
                    assert.equal(body.suggestions.length, server.suggestions.length);
                    for (const [index, item] of body.suggestions.entries()) {
                        assert.deepEqual(Object.keys(item).sort(), ["id", "included", "text"]);
                        assert.equal(item.id, server.suggestions[index].id);
                        assert.equal(typeof item.included, "boolean");
                        Object.assign(server.suggestions[index], item);
                    }
                    server.version = `revision-${Number(server.version.split("-")[1]) + 1}`;
                    server.html = reportHtml();
                    data = reportResponse();
                } else data = {...review(), suggestions:JSON.parse(JSON.stringify(server.suggestions))};
            }
            else if (path.endsWith("/bugs")) {
                if (options.method === "POST") {
                    const body = JSON.parse(options.body);
                    assert.equal(body.version, server.version);
                    assert.deepEqual(Object.keys(body.bugs[0]).sort(), ["feedback_ids","id","included","text"]);
                    for (const item of body.bugs) Object.assign(server.bugs.find(bug=>bug.id===item.id), item);
                    server.version = `revision-${Number(server.version.split("-")[1]) + 1}`;
                    server.html = reportHtml();
                    data = reportResponse();
                } else data = {...bugReview(), bugs:JSON.parse(JSON.stringify(server.bugs))};
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
    const ready = vm.runInContext("typeof workspaceReady === 'undefined' ? Promise.resolve() : workspaceReady", context);
    async function runTimer() {
        const entry = timers.entries().next().value;
        assert(entry, "A polling timer is scheduled");
        timers.delete(entry[0]);
        await entry[1].callback();
    }
    return {context, nodes, calls, requests, server, created, windowListeners, ready, sessionStorage, timers, runTimer,
        url:()=>location.href};
}

async function testUI() {
    const app = boot();
    await app.ready;
    const {context, nodes, calls, url} = app;
    const get = id => nodes.get("#" + id);
    assert(!url().includes("upload="), "Old upload link removed on initial load");
    assert.deepEqual(calls, ["/api/jobs/active"], "A fresh workspace checks for active work without restoring unrelated uploads");
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
    await fresh.ready;
    assert.deepEqual(fresh.calls,["/api/jobs/active"]);
    assert.equal(fresh.nodes.get("#content-section").hidden,true);

    const previewApp = boot();
    await previewApp.ready;
    const fetchPreview = previewApp.context.fetch;
    const longInfo = "附加信息".repeat(31);
    const longText = "玩家原文".repeat(31);
    previewApp.context.fetch = async (path, options) => path.includes("/preview?") ? {ok:true,json:async()=>({
        columns:["反馈ID", "附加信息", "用户自己填写的内容"],
        rows:[["0001", "简短信息", "<script>unsafe()</script>"], ["0002", longInfo, longText]],
        page:1, page_size:50, total:2
    })} : fetchPreview(path, options);
    const previewForm = previewApp.nodes.get("#upload-form");
    await previewForm.listeners.submit({preventDefault(){},target:previewForm});
    const previewRows = previewApp.nodes.get("#data-table tbody").children;
    assert.equal(previewRows[0].children[1].textContent,"简短信息");
    assert.equal(previewRows[0].children[2].textContent,"<script>unsafe()</script>");
    assert(!previewRows[0].children[2].innerHTML,"Preview source text stays plain text");
    assert.equal(previewRows[1].children[1].children[0].children[0].children[0].textContent,longInfo,"Long extra information expands in place");
    assert.equal(previewRows[1].children[2].textContent,longText,"Other long source columns stay plain text");

    const reviewApp = boot({pushStatus:"ready"});
    await reviewApp.ready;
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
    assert.equal(n("edit-bugs").disabled,true);
    assert(n("review-status").textContent.includes("锁定"));
    assert.equal(reviewApp.requests.filter(item=>item.path.endsWith("/reports")).length,1,"Editing never generates another AI report");

    const bugApp = boot({pushStatus:"ready"});
    await bugApp.ready;
    const b = id => bugApp.nodes.get("#"+id);
    const bugFields = () => vm.runInContext("reviewFields", bugApp.context);
    const change = (input, checked) => {input.checked=checked; input.listeners.change();};
    await b("upload-form").listeners.submit({preventDefault(){},target:b("upload-form")});
    await b("report-form").listeners.submit({preventDefault(){}});
    await b("edit-suggestions").listeners.click();
    bugFields()[0].input.value="保留的人工建议修改";
    bugFields()[0].input.listeners.input();
    await b("review-editor").listeners.submit({preventDefault(){}});
    assert.equal(b("edit-bugs").disabled,false);
    await b("edit-bugs").listeners.click();
    assert.equal(b("review-title").textContent,"审核 BUG");
    assert.equal(b("edit-suggestions").disabled,true,"Only one editor can be open");
    const callsBeforeOtherEditor = bugApp.calls.length;
    await b("edit-suggestions").listeners.click();
    assert.equal(bugApp.calls.length,callsBeforeOtherEditor);
    assert(b("bug-review-summary").textContent.includes("2 个问题，涉及 3 条去重反馈"),"Shared feedback IDs count once");
    assert(bugApp.created.some(item=>item.textContent==="BUG-001"));
    assert(bugApp.created.some(item=>item.textContent==="玩法相关 · 活动 / 许愿树"));
    change(bugFields()[0].sourceChecks[1].input,false);
    assert(b("bug-review-summary").textContent.includes("2 个问题，涉及 3 条去重反馈"));
    change(bugFields()[1].included,false);
    assert(b("bug-review-summary").textContent.includes("1 个问题，涉及 1 条去重反馈"));
    assert.equal(bugFields()[1].input.disabled,true);
    assert.equal(bugFields()[1].input.required,false);
    assert.equal(bugFields()[1].removed.hidden,false);
    beforeUnload={preventDefault(){this.prevented=true;}};
    bugApp.windowListeners.beforeunload(beforeUnload);
    assert(beforeUnload.prevented,"BUG draft triggers unload protection");
    change(bugFields()[0].sourceChecks[0].input,false);
    assert.equal(b("save-review").disabled,true,"An included issue must retain evidence");
    assert(b("review-draft-status").textContent.includes("至少需要一条"));
    b("restore-suggestions").listeners.click();
    assert.equal(vm.runInContext("reviewDirty()",bugApp.context),false);
    assert.equal(bugFields()[1].included.checked,true);
    assert(bugFields().every(field=>field.sourceChecks.every(source=>source.input.checked)));
    change(bugFields()[0].sourceChecks[1].input,false);
    change(bugFields()[1].included,false);
    bugFields()[0].input.value="玩家反映登录时出现错误提示。";
    bugFields()[0].input.listeners.input();
    assert.equal(b("push-feishu").disabled,true);
    assert.equal(b("download-md").getAttribute("aria-disabled"),"true");
    await b("review-editor").listeners.submit({preventDefault(){}});
    const savedBugs = JSON.parse(bugApp.requests.find(item=>item.path.endsWith("/bugs")&&item.options.method==="POST").options.body).bugs;
    assert.deepEqual(savedBugs[0].feedback_ids,["0001"]);
    assert.equal(savedBugs[1].included,false);
    assert(b("report-preview").innerHTML.includes("BUG 问题数：1；涉及反馈：1"));
    assert(b("report-preview").innerHTML.includes("玩家反映登录时出现错误提示。"));
    assert(b("bug-review-status").textContent.includes("BUG 已人工审核修改"));
    assert(b("review-status").textContent.includes("已人工修改"),"BUG changes preserve independent suggestion badge");
    await b("edit-bugs").listeners.click();
    change(bugFields()[0].included,false);
    await b("review-editor").listeners.submit({preventDefault(){}});
    assert(b("report-preview").innerHTML.includes("BUG 问题数：0；涉及反馈：0"));
    assert.equal(b("edit-bugs").disabled,false,"Excluded-all BUG review can reopen for restoration");
    await b("edit-bugs").listeners.click();
    b("restore-suggestions").listeners.click();
    b("cancel-review").listeners.click();
    assert(bugApp.server.bugs.every(item=>!item.included),"Cancel discards local restoration");
    await b("edit-bugs").listeners.click();
    b("restore-suggestions").listeners.click();
    await b("review-editor").listeners.submit({preventDefault(){}});
    assert(b("report-preview").innerHTML.includes("BUG 问题数：2；涉及反馈：3"));
    assert.equal(b("bug-review-status").hidden,true,"Restoring originals clears BUG modification status");
    assert(b("review-status").textContent.includes("已人工修改"));
    await b("push-feishu").listeners.click();
    const bugPush = bugApp.requests.find(item=>item.path.endsWith("/push/feishu"));
    assert.equal(JSON.parse(bugPush.options.body).report_version,"revision-5","Push uses revision after BUG saves");
    assert.equal(b("edit-bugs").disabled,true);
    assert.equal(b("edit-suggestions").disabled,true);
    assert.equal(bugApp.calls.filter(path=>path.endsWith("/reports")).length,1);

    for (const options of [{bugAvailable:false},{bugs:[]}]) {
        const old = boot({pushStatus:"ready",...options});
        await old.ready;
        await old.nodes.get("#upload-form").listeners.submit({preventDefault(){},target:old.nodes.get("#upload-form")});
        await old.nodes.get("#report-form").listeners.submit({preventDefault(){}});
        assert.equal(old.nodes.get("#edit-bugs").disabled,true);
        assert(old.nodes.get("#edit-bugs").title.includes(options.bugs ? "未发现" : "重新生成"));
    }

    for (const button of ["edit-suggestions", "edit-bugs"]) {
        const locked = boot({pushStatus:"ready"});
        await locked.ready;
        const node = id => locked.nodes.get("#" + id);
        await node("upload-form").listeners.submit({preventDefault(){},target:node("upload-form")});
        await node("report-form").listeners.submit({preventDefault(){}});
        // Another browser can start pushing after this page has displayed the report.
        locked.server.pushStatus = "sent";
        await node(button).listeners.click();
        assert.equal(node("review-editor").hidden,true,"A newly locked report cannot open an editor");
        assert.equal(node("report-preview").hidden,false);
        assert(node("error").textContent.includes("锁定"));
        assert.equal(node(button).disabled,true);
        assert.equal(node("file").disabled,false,"Rejected editor requests release the workspace controls");
        assert.equal(node("start").disabled,false);
    }
    console.log("UI interactions passed: suggestion/BUG editing, original sources, source exclusions, unique counts, restore/cancel, revisions and push locking.");
}

module.exports = {boot};
if (require.main === module) testUI().catch(error => {console.error(error);process.exitCode=1;});
