// Browser-independent interaction checks. No network or model requests.
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const html = fs.readFileSync("templates/game.html", "utf8");
const script = fs.readFileSync("static/js/game.js", "utf8");

function boot() {
    function element() {
        return {
            hidden: true, disabled: false, value: "", files: [], checked: false,
            dataset: {}, attributes: {}, listeners: {}, content: "test-token",
            classList: {toggle() {}, add() {}, remove() {}},
            addEventListener(type, fn) { this.listeners[type] = fn; },
            setAttribute(k, v) { this.attributes[k] = v; },
            getAttribute(k) { return this.attributes[k]; },
            removeAttribute(k) { delete this.attributes[k]; if (k === "data-ready") delete this.dataset.ready; },
            append() {}, replaceChildren() {}, focus() {}, reset() { this.wasReset = true; }
        };
    }
    const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => ["#" + m[1], element()]));
    for (const selector of ['meta[name="csrf-token"]', "#data-table thead", "#data-table tbody", ".table-scroll"]) nodes.set(selector, element());
    nodes.get("#workspace").dataset.game = "baloot";
    const calls = [];
    let url = "http://localhost/games/baloot?upload=old-upload";
    const context = vm.createContext({
        URL, console, setTimeout: () => 1, clearTimeout() {},
        window: { location: {href: url, reload() {}}, addEventListener() {} },
        history: {replaceState(a, b, next) { url = String(next); }},
        document: {
            querySelector(selector) { assert(nodes.has(selector), "Missing DOM selector " + selector); return nodes.get(selector); },
            createElement: element
        },
        FormData: class { constructor() { assert.equal(nodes.get("#file").disabled, false, "File captured before disabling"); } },
        fetch: async (path, options) => {
            calls.push(path);
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
            else if (path.endsWith("/report")) data = {html:"<h1>Report</h1>"};
            else data = {run:"new-upload", filename:"sample.csv",total:1,valid:1,excluded:0,
                source_start:"2026-10-12",source_end:"2026-10-22",suggested_start:"2026-10-19",suggested_end:"2026-10-22"};
            return {ok:true,json:async()=>data};
        }
    });
    vm.runInContext(script, context);
    return {context, nodes, calls, url:()=>url};
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
    get("report-tab").listeners.click();
    assert.equal(get("data-section").hidden,true);
    assert.equal(get("result-section").hidden,false);
    await get("report-form").listeners.submit({preventDefault(){}});
    assert.equal(get("report-empty").hidden,true);
    assert.equal(get("report-preview").hidden,false);
    assert.equal(get("downloads").hidden,false);
    get("data-tab").listeners.click();
    assert.equal(get("downloads").hidden,true);
    get("report-tab").listeners.click();
    assert.equal(get("downloads").hidden,false);
    const fresh = boot();
    assert.equal(fresh.calls.length,0);
    assert.equal(fresh.nodes.get("#content-section").hidden,true);
    console.log("UI interactions passed: upload, tabs, report/downloads, clean refresh.");
})().catch(error => {console.error(error);process.exitCode=1;});
