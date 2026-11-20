/* Project rules share one draft so taxonomy and thresholds can be saved together. */
(() => {
    const url = `${gameApi}/config`;
    const view = $("#config-view"), fields = $("#config-fields"), save = $("#config-save");
    const navigation = $("#config-navigation"), form = $("#config-editor");
    const reserved = new Set(["__proto__", "constructor", "prototype"]);
    const reportFields = {
        max_suggestions: "建议数量上限", max_issues_per_module: "每模块重点主题上限",
        max_evidence_ids: "每主题反馈 ID 上限", ai_max_input_chars: "分析输入字符上限",
    };
    const settingFields = {
        column_mapping: "导入列映射", detail_columns: "明细导出列名", ignored_labels: "排除的后台标签",
        suggestion_labels: "建议标签", bug_labels: "BUG 标签", model: "模型",
        concurrency: "并发数", classification_batch_size: "每批反馈条数",
        classification_batch_max_chars: "每批输入字符上限", timeout_seconds: "请求超时（秒）",
        max_retries: "重试次数", retry_delay: "重试间隔（秒）", max_rows: "导入行数上限",
        max_report_days: "报告天数上限", preview_page_size: "预览每页条数",
        max_expanded_xlsx_mb: "Excel 解压体积上限（MB）", csv_encoding: "CSV 编码",
    };
    const termFields = {
        definition: "定义", ar_variants: "阿语说法", translation_notes: "翻译说明",
        ug_official: "维语正式说法", ug_colloquial: "维语常用说法", tr_variants: "土语说法",
    };
    const projectTermFields = {
        baloot: ["ar_variants", "definition", "translation_notes"],
        xinjiang: ["ug_official", "ug_colloquial", "definition"],
        okey: ["tr_variants", "definition"],
    };
    let state, draft, groups, terms, changed = false, busy = false, expiry;
    let termBatch = null;

    function el(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function button(text, action) {
        const node = el("button", text, "secondary compact");
        node.type = "button";
        node.addEventListener("click", action);
        return node;
    }
    function status(text, failed = false) {
        const node = $("#config-status");
        node.textContent = text;
        node.classList.toggle("failed", failed);
    }
    function dirty() {
        return changed || pendingTerms();
    }
    function pendingTerms() {
        return Boolean(termBatch?.grid.dirty() && termBatch.grid.rows().some(row => row.some(cell => cell.trim())));
    }
    function touch() {
        changed = true;
        save.disabled = busy;
    }
    function setBusy(value) {
        busy = value;
        form.inert = navigation.inert = value;
    }
    function clear() {
        clearTimeout(expiry);
        termBatch = null;
        state = draft = groups = terms = null;
        changed = false;
        promptConfig = null;
        promptDrafts = {};
        $("#prompt-text").value = "";
        $("#prompt-form").hidden = true;
        $("#config-prompts").hidden = true;
        navigation.hidden = form.hidden = true;
        fields.replaceChildren();
    }
    function lockScreen(available, message) {
        clear();
        $("#config-unlock").hidden = !available;
        status(message || (available ? "" : "此项目尚未设置编辑密码"), Boolean(message));
    }
    function expired() {
        lockScreen(true, "解锁已过期，请重新输入项目密码");
    }
    function setDraft(value) {
        termBatch = null;
        draft = structuredClone(value);
        groups = Object.entries(draft.schema.labels).map(([name, data]) => ({
            name, data, threshold: draft.report.module_growth_thresholds[name] ?? 0.3,
        }));
        terms = Object.entries(draft.schema.terminology || {}).map(([name, data]) => ({name, data}));
    }
    function show(result) {
        if (!result.unlocked) {
            lockScreen(result.available);
            return;
        }
        state = result;
        setDraft(result.value);
        changed = false;
        $("#config-unlock").hidden = true;
        navigation.hidden = false;
        clearTimeout(expiry);
        expiry = setTimeout(expired, Math.max(0, result.expires_at * 1000 - Date.now()));
        render();
    }
    async function refresh() {
        if (busy) return;
        setBusy(true);
        status("正在读取配置");
        try {
            const result = await api(url);
            status("");
            show(result);
        } catch (failure) {
            lockScreen(true, failure.message);
        } finally { setBusy(false); }
    }
    function input(label, value, update, options = {}) {
        const wrapper = el("label", undefined, "config-field");
        const numeric = typeof value === "number" || value === null;
        const control = el(Array.isArray(value) ? "textarea" : "input");
        if (Array.isArray(value)) control.rows = 3;
        else control.type = numeric ? "number" : "text";
        control.className = "prompt-input";
        if (numeric) {
            control.step = options.step || "1";
            control.min = options.min ?? "0";
        }
        control.value = Array.isArray(value) ? value.join("\n") : value ?? "";
        if (options.required) control.required = true;
        if (options.readOnly) control.readOnly = true;
        control.addEventListener("input", () => {
            update(Array.isArray(value) ? control.value.split("\n").map(item => item.trim()).filter(Boolean) :
                numeric ? (control.value === "" ? null : control.valueAsNumber) : control.value);
            touch();
        });
        wrapper.append(el("span", label), control);
        return wrapper;
    }
    function section(name, note) {
        const node = el("section", undefined, "config-group");
        node.append(el("h2", name));
        if (note) node.append(el("p", note, "muted small"));
        return node;
    }
    function grid() { return el("div", undefined, "config-grid"); }
    function threshold(group) {
        const percent = group.threshold === null ? null : Number((group.threshold * 100).toFixed(8));
        return input(`${group.name || "新模块"}（%）`, percent,
            value => { group.threshold = value === null ? null : value / 100; }, {step: "any", required: true});
    }
    function renderReport() {
        const growth = section("模块增长阈值", "填写百分比，例如 30 表示增长 30% 时触发重点分析");
        const list = grid();
        groups.forEach(group => list.append(threshold(group)));
        growth.append(list);
        const report = section("报告上限"), values = grid();
        for (const [key, label] of Object.entries(reportFields)) {
            values.append(input(label, draft.report[key], value => { draft.report[key] = value; }, {required: true}));
        }
        report.append(values);
        fields.append(growth, report);
    }
    function addRemovable(list, items, item, node, label) {
        const row = el("div", undefined, "config-item");
        const remove = button("删除", () => {
            const index = items.indexOf(item);
            if (index < 0 || !window.confirm(`删除${label}？保存后生效。`)) return;
            items.splice(index, 1);
            row.remove();
            touch();
        });
        remove.setAttribute("aria-label", `删除${label}`);
        row.append(node, remove);
        list.append(row);
    }
    function editableList(items, label, make, renderItem) {
        const list = el("div", undefined, "config-list");
        const rows = el("div", undefined, "config-list");
        const add = item => addRemovable(rows, items, item, renderItem(item), label);
        items.forEach(add);
        list.append(rows, button(`添加${label}`, () => {
            const item = make();
            items.push(item);
            add(item);
            touch();
            const row = rows.lastElementChild;
            if (row.firstElementChild.tagName === "DETAILS") row.firstElementChild.open = true;
            else row.querySelector("input")?.focus();
        }));
        return list;
    }
    function nested(title, build) {
        const details = el("details", undefined, "config-details");
        const summary = el("summary", title);
        details.append(summary);
        details.addEventListener("toggle", () => {
            if (details.open && details.childElementCount === 1) details.append(build(summary));
        });
        return details;
    }
    function objectAliases(data) {
        const panel = grid();
        const lang = {baloot: "ar", okey: "tr", xinjiang: "ug"}[workspace.dataset.game];
        if (!lang) return panel;
        panel.append(input(`原语言名称（${lang}）`, data.translations?.[lang] || "", value => {
            if (value.trim()) (data.translations ??= {})[lang] = value.trim();
            else if (data.translations) delete data.translations[lang];
        }));
        panel.append(input("其他写法（每行一项）", data.variants?.[lang] || [], value => {
            if (value.length) (data.variants ??= {})[lang] = value;
            else if (data.variants) delete data.variants[lang];
        }));
        return panel;
    }
    function themeEditor(theme, level) {
        return nested(theme.name || `新${level}标签`, summary => {
            const panel = el("div", undefined, "config-list");
            panel.append(input("标签名称", theme.name, value => {
                theme.name = value;
                summary.textContent = value || `新${level}标签`;
            }, {required: true}), objectAliases(theme));
            if (level === "二级") {
                panel.append(editableList(theme.children ??= [], "三级标签", () => ({name: ""}), child => themeEditor(child, "三级")));
            }
            return panel;
        });
    }
    function renderTermBatch() {
        if (termBatch) return termBatch.container;
        const container = el("details", undefined, "term-batch");
        container.open = true;
        container.append(el("summary", "批量添加术语"));
        const panel = el("div", undefined, "config-list");
        panel.append(el("p", "从 Excel 复制数据行并粘贴到表格。每行一条术语，多种说法可在同一单元格内换行", "muted small"));
        const columns = [{key: "name", label: "术语名称"},
            ...(projectTermFields[workspace.dataset.game] || ["definition"]).map(key => ({key, label: termFields[key]}))];
        const gridHost = el("div");
        const message = el("p", undefined, "small term-batch-message");
        message.setAttribute("role", "status");
        const preview = el("div", undefined, "config-list");
        let plan = null;
        function messageText(text, failed = false) {
            message.textContent = text;
            message.classList.toggle("failed", failed);
        }
        function invalidate() {
            plan = null;
            preview.replaceChildren();
            messageText("");
        }
        const grid = window.createTermGrid(gridHost, {columns, onChange: invalidate,
            showError: text => messageText(text, true)});
        function describe(data) {
            return Object.entries(data).map(([key, value]) =>
                `${termFields[key] || key}：${Array.isArray(value) ? value.join("、") : value}`).join("\n") || "未填写说明";
        }
        const check = button("检查并预览", () => {
            invalidate();
            try {
                const existing = named(terms, "已有术语", item => item.data);
                const entries = grid.rows().map((row, index) => {
                    const values = row.map(value => value.trim());
                    if (!values.some(Boolean)) return null;
                    if (!values[0] || reserved.has(values[0])) throw new Error(`第 ${index + 1} 行请填写有效的术语名称`);
                    const data = {};
                    for (const [offset, column] of columns.slice(1).entries()) {
                        const value = values[offset + 1];
                        if (value) data[column.key] = column.key.endsWith("variants") || column.key.endsWith("notes") ?
                            [...new Set(value.split(/\r?\n/).map(text => text.trim()).filter(Boolean))] : value;
                    }
                    return {name: values[0], data};
                }).filter(Boolean);
                if (!entries.length) throw new Error("请先填写或粘贴术语");
                named(entries, "本批术语", item => item.data);
                const conflicts = entries.filter(item => Object.hasOwn(existing, item.name));
                const decisions = new Map();
                for (const item of conflicts) {
                    const card = el("section", undefined, "term-conflict");
                    card.append(el("h3", item.name));
                    const comparison = el("div", undefined, "config-grid");
                    for (const [title, data] of [["现有内容", existing[item.name]], ["本次内容", item.data]]) {
                        const side = el("div");
                        side.append(el("strong", title), el("p", describe(data)));
                        comparison.append(side);
                    }
                    card.append(comparison);
                    const label = el("label", undefined, "config-field");
                    const select = el("select", undefined, "prompt-input");
                    select.setAttribute("aria-label", `同名术语「${item.name}」的处理`);
                    for (const [value, title] of [["keep", "保留现有内容"], ["merge", "合并两份内容"], ["replace", "用本次内容覆盖"]]) {
                        select.append(new Option(title, value));
                    }
                    label.append(el("span", `同名术语「${item.name}」的处理`), select);
                    decisions.set(item.name, select);
                    card.append(label);
                    preview.append(card);
                }
                const snapshot = JSON.stringify(terms);
                plan = {entries, decisions, snapshot};
                const apply = button("加入配置草稿", () => {
                    if (!plan) return;
                    if (busy || JSON.stringify(terms) !== plan.snapshot) {
                        messageText("术语草稿已变化，请重新检查后再加入", true);
                        return;
                    }
                    for (const item of plan.entries) {
                        const previous = terms.find(term => term.name.trim() === item.name);
                        if (!previous) { terms.push(structuredClone(item)); continue; }
                        const choice = plan.decisions.get(item.name).value;
                        if (choice === "keep") continue;
                        previous.data = choice === "replace" ? structuredClone(item.data) : mergeTermData(previous.data, item.data);
                    }
                    termBatch = null;
                    touch();
                    render();
                    status("术语已加入草稿，点击「保存配置」后生效");
                });
                preview.append(apply);
                messageText(`共 ${entries.length} 条术语，新增 ${entries.length - conflicts.length} 条，同名 ${conflicts.length} 条`);
            } catch (failure) { messageText(failure.message, true); }
        });
        panel.append(gridHost, check, message, preview);
        container.append(panel);
        termBatch = {container, grid};
        return container;
    }
    function mergeTermData(previous, incoming) {
        const merged = structuredClone(previous);
        for (const [key, value] of Object.entries(incoming)) {
            const old = merged[key];
            if (Array.isArray(value)) merged[key] = [...new Set([...(Array.isArray(old) ? old : old ? [old] : []), ...value])];
            else merged[key] = old && old !== value ? [...new Set([...String(old).split("\n"), ...value.split("\n")])].join("\n") : value;
        }
        return merged;
    }
    function renderLabels() {
        const labels = section("分类标签", "展开模块可编辑二级和三级标签。一级模块的阈值在「统计规则」中调整");
        labels.append(editableList(groups, "一级模块", () => ({name: "", data: {themes: []}, threshold: 0.3}),
            group => nested(group.name || "新一级模块", summary => {
                const panel = el("div", undefined, "config-list");
                panel.append(input("一级名称", group.name, value => {
                    group.name = value;
                    summary.textContent = value || "新一级模块";
                }, {required: true}), objectAliases(group.data),
                editableList(group.data.themes, "二级标签", () => ({name: ""}), theme => themeEditor(theme, "二级")));
                return panel;
            })));
        const dictionary = section("术语", "填写报告使用的名称，以及翻译时需要识别的说法");
        dictionary.append(renderTermBatch());
        dictionary.append(editableList(terms, "术语", () => ({name: "", data: {}}), term =>
            nested(term.name || "新术语", summary => {
                const panel = grid();
                panel.append(input("术语名称", term.name, value => {
                    term.name = value;
                    summary.textContent = value || "新术语";
                }, {required: true}));
                const options = new Set([...(projectTermFields[workspace.dataset.game] || ["definition"]), ...Object.keys(term.data)]);
                for (const key of options) {
                    const empty = key.endsWith("variants") || key.endsWith("notes") ? [] : "";
                    panel.append(input(termFields[key] || key, term.data[key] || empty, value => {
                        if (value.length) term.data[key] = value;
                        else delete term.data[key];
                    }));
                }
                return panel;
            })));
        const exclusions = section("语义排除原因", "AI 使用的排除原因，每行一项");
        const reasons = draft.schema.exclusion_reasons || ["缺少明确反馈对象或诉求", "内容无法理解", "含辱骂内容"];
        exclusions.append(input("排除原因", reasons, value => { draft.schema.exclusion_reasons = value; }));
        fields.append(labels, dictionary, exclusions);
    }
    function renderSettings() {
        const advanced = section("导入与处理参数", "调整后用于新的上传或报告任务");
        const general = grid();
        for (const [key, value] of Object.entries(draft.settings)) {
            if (workspace.dataset.game === "xinjiang" && ["suggestion_labels", "bug_labels"].includes(key)) continue;
            const label = settingFields[key] || key;
            if (value && typeof value === "object" && !Array.isArray(value)) {
                const mapping = section(label);
                const entries = grid();
                for (const [name, column] of Object.entries(value)) {
                    const readOnly = key === "detail_columns" &&
                        ["反馈时间", "反馈ID", "用户自己填写的内容", "中文翻译"].includes(name);
                    entries.append(input(name, column, changed => { value[name] = changed; }, {required: true, readOnly}));
                }
                mapping.append(entries);
                fields.append(mapping);
            } else {
                general.append(input(label, value, updated => { draft.settings[key] = updated; },
                    {required: !Array.isArray(value)}));
            }
        }
        advanced.append(general);
        fields.append(advanced);
    }
    async function loadVersion(version) {
        if (busy || !state || (dirty() && !window.confirm("载入历史版本会替换未保存的配置草稿。继续吗？"))) return;
        setBusy(true);
        try {
            const result = await api(`${url}/versions/${encodeURIComponent(version)}`);
            if (!state) return;
            setDraft(result.value);
            touch();
            view.value = "report";
            render();
            status("已载入历史版本，保存后生效");
        } catch (failure) {
            if (failure.locked) expired(); else status(failure.message, true);
        } finally { setBusy(false); save.disabled = !changed; }
    }
    function renderHistory() {
        const history = section("版本记录", "历史版本可载入为草稿，确认后再保存");
        const list = el("ul", undefined, "config-history");
        for (const entry of state.history) {
            const row = el("li");
            const date = entry.saved_at ? new Date(entry.saved_at).toLocaleString() : "";
            const label = entry.version ? `第 ${entry.version} 版` : "仓库默认";
            row.append(el("span", [label, date, entry.editor, entry.version === state.version ? "当前版本" : ""].filter(Boolean).join(" · ")));
            if (entry.version !== state.version) row.append(button("载入此版本", () => loadVersion(entry.version)));
            list.append(row);
        }
        history.append(list);
        fields.append(history);
    }
    function render() {
        if (!state) return;
        const mode = view.value;
        $("#config-prompts").hidden = mode !== "prompts";
        form.hidden = mode === "prompts";
        fields.replaceChildren();
        if (mode === "prompts") {
            if (!promptConfig) loadPrompts();
        } else {
            ({report: renderReport, labels: renderLabels, settings: renderSettings, history: renderHistory})[mode]();
        }
        save.disabled = !changed;
    }
    function named(items, label, value) {
        const entries = items.map(item => [item.name.trim(), value(item)]);
        const keys = entries.map(([name]) => name);
        if (keys.some(key => !key || reserved.has(key)) || new Set(keys).size !== keys.length) {
            throw new Error(`${label}名称不能为空或重复`);
        }
        return Object.fromEntries(entries);
    }
    function valueForSave() {
        draft.report.module_growth_thresholds = named(groups, "一级模块", group => group.threshold);
        draft.schema.labels = named(groups, "一级模块", group => group.data);
        if (terms.length || "terminology" in draft.schema) {
            draft.schema.terminology = named(terms, "术语", term => term.data);
        }
        return draft;
    }

    $("#config-unlock").addEventListener("submit", async event => {
        event.preventDefault();
        if (busy) return;
        const input = $("#config-password");
        const button = event.submitter;
        setBusy(true);
        if (button) button.disabled = true;
        status("正在验证项目密码");
        try {
            show(await post(url + "/unlock", {password: input.value}));
            status("");
        } catch (failure) { status(failure.message, true); }
        finally { input.value = ""; if (button) button.disabled = false; setBusy(false); }
    });
    $("#config-lock").addEventListener("click", async () => {
        if (busy || savingPrompt) return;
        if ((dirty() || Object.keys(promptDrafts).length) && !window.confirm("锁定会清除未保存的配置修改。继续吗？")) return;
        setBusy(true);
        try {
            await post(url + "/lock", {});
            lockScreen(true);
        } catch (failure) { status(failure.message, true); }
        finally { setBusy(false); }
    });
    $("#config-reload").addEventListener("click", () => {
        if (dirty() && !window.confirm("重新读取会清除未保存的配置修改。继续吗？")) return;
        refresh();
    });
    view.addEventListener("change", render);
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!changed || busy || !state) return;
        if (pendingTerms()) { status("请先将批量术语加入草稿，或清空批量表格后再保存", true); return; }
        setBusy(true);
        save.disabled = true;
        try {
            const result = await post(url, {revision: state.revision, value: valueForSave()});
            show(result);
            status("已保存，新任务将使用此版本");
        } catch (failure) {
            if (failure.locked) expired(); else status(failure.message, true);
        } finally { setBusy(false); save.disabled = !changed; }
    });

    window.projectConfig = {open: () => { if (!state) refresh(); }, dirty, expired, unlocked: () => Boolean(state)};
})();
