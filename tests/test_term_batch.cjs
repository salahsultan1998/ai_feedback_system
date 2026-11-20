// Real-browser regression checks. Uses real templates/scripts and simulated configuration APIs.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {execFileSync} = require("node:child_process");

function playwright() {
    try { return require("playwright"); }
    catch (error) {
        const bundled = path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
        if (fs.existsSync(bundled)) return require(bundled);
        throw new Error("Install Playwright or make it available through NODE_PATH to run this browser check.", {cause: error});
    }
}

const root = path.resolve(__dirname, "..");
const python = process.env.PYTHON || path.join(root, ".venv/bin/python");
const fixtures = JSON.parse(execFileSync(python, ["-B", "-c", `
import json, os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from app import create_app
from feedback.config import project_config_defaults
with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch("app.load_dotenv"):
    client = create_app({"TESTING": True, "STORAGE_ROOT": Path(directory)}).test_client()
    print(json.dumps({game: {"html": client.get("/games/" + game).get_data(as_text=True),
                            "value": project_config_defaults(game)}
                      for game in ("baloot", "xinjiang", "okey")}))
`], {cwd: root, encoding: "utf8"}));

async function fixture(browser, game, terminology = {}) {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    page.setDefaultTimeout(5000);
    page.on("dialog", dialog => dialog.accept());
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const state = {
        unlocked: false, version: 0, saves: [],
        value: structuredClone(fixtures[game].value),
    };
    state.value.schema.terminology = terminology;
    const response = () => ({
        unlocked: true, available: true, expires_at: Date.now() / 1000 + 1800,
        revision: String(state.version), version: state.version, history: [], value: state.value,
    });
    await page.route("http://localhost/**", async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.pathname === `/games/${game}`) {
            return route.fulfill({contentType: "text/html", body: fixtures[game].html});
        }
        if (url.pathname.startsWith("/static/")) {
            const file = path.join(root, url.pathname);
            const extension = path.extname(file);
            const mime = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".webp": "image/webp"};
            return route.fulfill({contentType: mime[extension] || "application/octet-stream", body: fs.readFileSync(file)});
        }
        let result;
        if (url.pathname.endsWith("/config/unlock")) {
            state.unlocked = true;
            result = response();
        } else if (url.pathname.endsWith("/config/lock")) {
            state.unlocked = false;
            result = {unlocked: false, available: true};
        } else if (url.pathname.endsWith("/config")) {
            if (request.method() === "POST") {
                if (state.saveBarrier) await state.saveBarrier;
                assert(state.unlocked);
                const payload = request.postDataJSON();
                assert.equal(payload.revision, String(state.version));
                state.saves.push(structuredClone(payload));
                state.value = payload.value;
                state.version++;
            }
            result = state.unlocked ? response() : {unlocked: false, available: true};
        } else if (url.pathname.endsWith("/prompts")) {
            result = {editable: true, prompts: {classification: {version: 0, revision: "default", text: "{{LABEL_SCHEMA}}", previous: null}}};
        } else if (url.pathname === "/api/jobs/active") {
            result = {busy: false};
        } else if (url.pathname.startsWith("/api/workspaces/")) {
            result = {ok: true};
        } else {
            throw new Error("Unexpected request: " + request.url());
        }
        return route.fulfill({contentType: "application/json", body: JSON.stringify(result)});
    });
    await page.goto(`http://localhost/games/${game}`);
    await page.getByRole("tab", {name: "配置", exact: true}).click();
    await page.getByLabel("项目密码", {exact: true}).fill("test-password");
    await page.getByRole("button", {name: "解锁", exact: true}).click();
    await page.getByLabel("配置项", {exact: true}).selectOption("labels");
    assert(await page.locator(".term-batch").evaluate(node => node.open), "Bulk terminology table is expanded by default");
    return {page, state, errors};
}

async function paste(page, text, index = 0) {
    const cell = page.locator(".term-grid textarea").nth(index);
    await cell.focus();
    await cell.evaluate((node, content) => {
        const clipboardData = new DataTransfer();
        clipboardData.setData("text/plain", content);
        node.dispatchEvent(new ClipboardEvent("paste", {clipboardData, bubbles: true, cancelable: true}));
    }, text);
}

async function check(page) {
    await page.getByRole("button", {name: "检查并预览", exact: true}).click();
}
async function join(page) {
    await page.getByRole("button", {name: "加入配置草稿", exact: true}).click();
    await page.getByText("术语已加入草稿，点击「保存配置」后生效", {exact: true}).waitFor();
}
async function save(page) {
    await page.getByRole("button", {name: "保存配置", exact: true}).click();
    await page.getByText("已保存，新任务将使用此版本", {exact: true}).waitFor();
}
async function batchValues(page) {
    return page.locator(".term-grid tbody tr").evaluateAll(rows =>
        rows.map(row => Array.from(row.querySelectorAll("textarea"), input => input.value)));
}
async function reopen(page) {
    if (!await page.locator(".term-batch").evaluate(node => node.open)) {
        await page.locator(".term-batch > summary").click();
    }
}

async function pasteAndDraftChecks(browser) {
    const {page, state, errors} = await fixture(browser, "baloot");
    try {
        const headers = await page.locator(".term-grid thead th").allTextContents();
        assert.deepEqual(headers, ["行", "术语名称", "阿语说法", "定义", "翻译说明", "操作"]);
        await paste(page, '新术语\t"قول أول\nقول ""ثان"""\t说明\t"注1\n注2"\r\n另一术语\tثاني\t定义2\t\r\n');
        assert.deepEqual(await batchValues(page), [
            ["新术语", 'قول أول\nقول "ثان"', "说明", "注1\n注2"],
            ["另一术语", "ثاني", "定义2", ""],
        ]);
        const before = await batchValues(page);
        const origin = page.locator(".term-grid textarea").first();
        await origin.press("End");
        await origin.press("x");
        await origin.press(process.platform === "darwin" ? "Meta+z" : "Control+z");
        assert.deepEqual(await batchValues(page), before, "Undo typing must retain the pasted matrix");
        await origin.press(process.platform === "darwin" ? "Meta+z" : "Control+z");
        assert.deepEqual(await batchValues(page), [["", "", "", ""]], "Next undo removes the paste");
        await paste(page, '新术语\t"قول أول\nقول ""ثان"""\t说明\t"注1\n注2"\r\n另一术语\tثاني\t定义2\t\r\n');
        await paste(page, "一\t二\t三\t四\t五");
        await page.getByText("粘贴列数超出表格范围，请按表头顺序重新复制", {exact: true}).waitFor();
        assert.deepEqual(await batchValues(page), before);
        await paste(page, '"未闭合\n');
        await page.getByText("粘贴内容的引号未闭合，请重新复制 Excel 区域", {exact: true}).waitFor();
        assert.deepEqual(await batchValues(page), before);
        await paste(page, Array(1001).fill("术语\t说法").join("\n"));
        await page.getByText("批量术语最多支持 1000 行", {exact: true}).waitFor();
        assert.deepEqual(await batchValues(page), before);

        const view = page.getByLabel("配置项", {exact: true});
        await view.selectOption("report");
        await page.getByLabel("建议数量上限", {exact: true}).fill("19");
        await page.getByRole("button", {name: "保存配置", exact: true}).click();
        await page.getByText("请先将批量术语加入草稿，或清空批量表格后再保存", {exact: true}).waitFor();
        assert.equal(state.saves.length, 0);
        await view.selectOption("labels");
        assert.deepEqual(await batchValues(page), before);
        await check(page);
        await page.getByText("共 2 条术语，新增 2 条，同名 0 条", {exact: true}).waitFor();
        await join(page);
        assert.equal(state.saves.length, 0, "Joining only changes the draft");
        let releaseSave;
        state.saveBarrier = new Promise(resolve => { releaseSave = resolve; });
        const pendingSave = save(page);
        await page.waitForFunction(() => document.querySelector("#config-editor").inert &&
            document.querySelector("#config-navigation").inert);
        releaseSave();
        await pendingSave;
        delete state.saveBarrier;
        assert.equal(state.saves.length, 1);
        assert.deepEqual(state.value.schema.terminology["新术语"], {
            ar_variants: ["قول أول", 'قول "ثان"'], definition: "说明", translation_notes: ["注1", "注2"],
        });
        assert.equal(state.value.report.max_suggestions, 19);

        await reopen(page);
        await paste(page, "\tقول\t说明\t");
        await check(page);
        await page.getByText("第 1 行请填写有效的术语名称", {exact: true}).waitFor();
        assert.equal(await page.getByRole("button", {name: "加入配置草稿", exact: true}).count(), 0);
        await paste(page, "重复词\tقول\t说明\t\n重复词\tثاني\t说明2\t");
        await check(page);
        await page.getByText("本批术语名称不能为空或重复", {exact: true}).waitFor();
        assert.equal(state.saves.length, 1);
        await page.getByRole("button", {name: "删除第 2 行", exact: true}).click();
        assert.equal((await batchValues(page)).length, 1);
        await page.getByRole("button", {name: "撤销", exact: true}).click();
        assert.equal((await batchValues(page)).length, 2);
        await page.getByRole("button", {name: "锁定配置", exact: true}).click();
        await page.getByLabel("项目密码", {exact: true}).waitFor();
        assert.equal(await page.locator(".term-grid textarea").count(), 0);
        await page.getByLabel("项目密码", {exact: true}).fill("test-password");
        await page.getByRole("button", {name: "解锁", exact: true}).click();
        await reopen(page);
        assert.deepEqual(await batchValues(page), [["", "", "", ""]]);
        assert.deepEqual(errors, []);
    } finally { await page.close(); }
}

async function conflictChecks(browser) {
    const original = {ar_variants: ["قديم"], definition: "旧定义", translation_notes: ["旧说明"], legacy: "保留字段"};
    const {page, state, errors} = await fixture(browser, "baloot", {"金币": structuredClone(original)});
    try {
        await paste(page, "金币\tحديث\t新定义\t新说明");
        await check(page);
        const choice = page.getByLabel("同名术语「金币」的处理", {exact: true});
        if (process.env.TERM_BATCH_SCREENSHOTS) {
            await page.locator(".term-batch").screenshot({path: "/tmp/feedback-term-batch-desktop.png"});
            await page.setViewportSize({width: 390, height: 844});
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile page must not overflow");
            assert(await choice.evaluate(select => {
                const box = select.getBoundingClientRect();
                const panel = select.closest(".term-batch").getBoundingClientRect();
                return box.right <= panel.right && box.left >= panel.left;
            }), "Mobile conflict controls must remain inside the panel");
            await page.locator(".term-batch").screenshot({path: "/tmp/feedback-term-batch-mobile.png"});
            await page.setViewportSize({width: 1280, height: 900});
        }
        assert.equal(await choice.inputValue(), "keep");
        await join(page);
        await save(page);
        assert.deepEqual(state.value.schema.terminology["金币"], original);

        await reopen(page);
        await paste(page, "金币\tحديث\t新定义\t新说明");
        await check(page);
        await choice.selectOption("merge");
        await join(page);
        await save(page);
        assert.deepEqual(state.value.schema.terminology["金币"], {
            ar_variants: ["قديم", "حديث"], definition: "旧定义\n新定义",
            translation_notes: ["旧说明", "新说明"], legacy: "保留字段",
        });
        await reopen(page);
        await paste(page, "金币\tأحدث\t替换定义\t");
        await check(page);
        await choice.selectOption("replace");
        await join(page);
        await save(page);
        assert.deepEqual(state.value.schema.terminology["金币"], {ar_variants: ["أحدث"], definition: "替换定义"});
        assert.deepEqual(errors, []);
    } finally { await page.close(); }
}

async function otherGameChecks(browser) {
    for (const [game, text, expected, columns] of [
        ["xinjiang", "金币\tتەڭگە\tتىيىن\t游戏货币", {ug_official: "تەڭگە", ug_colloquial: "تىيىن", definition: "游戏货币"},
            ["行", "术语名称", "维语正式说法", "维语常用说法", "定义", "操作"]],
        ["okey", "金币\tÇip\t游戏货币", {tr_variants: ["Çip"], definition: "游戏货币"},
            ["行", "术语名称", "土语说法", "定义", "操作"]],
    ]) {
        const {page, state, errors} = await fixture(browser, game);
        try {
            assert.deepEqual(await page.locator(".term-grid thead th").allTextContents(), columns);
            await paste(page, text);
            await check(page);
            await join(page);
            await save(page);
            assert.deepEqual(state.value.schema.terminology["金币"], expected);
            assert.deepEqual(errors, []);
        } finally { await page.close(); }
    }
}

(async () => {
    const chrome = process.env.CHROME_BIN || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
    const browser = await playwright().chromium.launch(chrome ? {executablePath: chrome} : {});
    try {
        await pasteAndDraftChecks(browser);
        await conflictChecks(browser);
        await otherGameChecks(browser);
        console.log("Term batch passed: Excel paste, validation, atomic rejection, draft persistence, conflict choices, save boundary, lock, and three projects.");
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
