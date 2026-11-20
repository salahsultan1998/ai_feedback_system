/* Spreadsheet-style input for project terminology. */
window.createTermGrid = function (container, {columns, onChange, showError}) {
    const maxRows = 1000;
    const undo = [];
    const controls = document.createElement("div");
    const wrap = document.createElement("div");
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const body = document.createElement("tbody");
    let changed = false, beforeEdit = null;

    controls.className = "term-grid-actions";
    wrap.className = "term-grid-wrap";
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", "批量术语表格，可横向滚动");
    table.className = "term-grid";
    table.setAttribute("aria-label", "批量术语表格");
    const heading = head.insertRow();
    for (const name of ["行", ...columns.map(column => column.label), "操作"]) {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.textContent = name;
        heading.append(cell);
    }
    table.append(head, body);
    wrap.append(table);
    container.append(controls, wrap);

    function button(text, action) {
        const node = document.createElement("button");
        node.type = "button";
        node.className = "secondary compact";
        node.textContent = text;
        node.addEventListener("click", action);
        controls.append(node);
        return node;
    }
    function rows() {
        return Array.from(body.rows, row => Array.from(row.querySelectorAll("textarea"), input => input.value));
    }
    function notify() {
        changed = true;
        beforeEdit = null;
        undoButton.disabled = !undo.length;
        onChange();
    }
    function remember(snapshot = rows()) {
        undo.push(snapshot);
        if (undo.length > 30) undo.shift();
    }
    function renumber() {
        for (const [index, row] of Array.from(body.rows).entries()) {
            row.firstElementChild.textContent = index + 1;
            row.querySelectorAll("textarea").forEach((input, column) => {
                input.setAttribute("aria-label", `第 ${index + 1} 行${columns[column].label}`);
            });
            row.lastElementChild.firstElementChild.setAttribute("aria-label", `删除第 ${index + 1} 行`);
        }
    }
    function appendRow(values = []) {
        const row = body.insertRow();
        const number = document.createElement("th");
        number.scope = "row";
        row.append(number);
        for (const [index] of columns.entries()) {
            const cell = row.insertCell();
            const input = document.createElement("textarea");
            input.rows = 2;
            input.dir = "auto";
            input.value = values[index] || "";
            cell.append(input);
        }
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary compact";
        remove.textContent = "删除";
        remove.addEventListener("click", () => {
            remember();
            const index = row.sectionRowIndex;
            row.remove();
            if (!body.rows.length) appendRow();
            renumber();
            notify();
            body.rows[Math.min(index, body.rows.length - 1)].querySelector("textarea").focus();
        });
        row.insertCell().append(remove);
        return row;
    }
    function replace(values) {
        body.replaceChildren();
        for (const value of values) appendRow(value);
        if (!values.length) appendRow();
        renumber();
    }
    // Excel quotes cells containing tabs, line breaks or double quotes.
    function parseClipboard(text) {
        const result = [];
        let row = [], field = "", quoted = false, endedWithNewline = false;
        for (let i = 0; i < text.length; i++) {
            const character = text[i];
            endedWithNewline = false;
            if (quoted) {
                if (character === '"' && text[i + 1] === '"') { field += '"'; i++; }
                else if (character === '"') quoted = false;
                else field += character;
            } else if (character === '"' && field === "") quoted = true;
            else if (character === "\t") { row.push(field); field = ""; }
            else if (character === "\r" || character === "\n") {
                if (character === "\r" && text[i + 1] === "\n") i++;
                row.push(field);
                result.push(row);
                row = [];
                field = "";
                endedWithNewline = true;
            } else field += character;
        }
        if (quoted) throw new Error("粘贴内容的引号未闭合，请重新复制 Excel 区域");
        if (!endedWithNewline) result.push([...row, field]);
        return result;
    }
    function cellInput(event) {
        return event.target.matches("textarea") ? event.target : null;
    }
    body.addEventListener("focusin", event => {
        if (cellInput(event)) beforeEdit = rows();
    });
    body.addEventListener("input", event => {
        if (!cellInput(event)) return;
        if (beforeEdit) remember(beforeEdit);
        notify();
    });
    body.addEventListener("paste", event => {
        const input = cellInput(event);
        if (!input || !event.clipboardData) return;
        const text = event.clipboardData.getData("text/plain");
        if (!/[\t\r\n]/.test(text)) return;
        event.preventDefault();
        const rowIndex = input.closest("tr").sectionRowIndex;
        const columnIndex = input.parentElement.cellIndex - 1;
        let values;
        try {
            values = parseClipboard(text);
            if (rowIndex + values.length > maxRows) throw new Error("批量术语最多支持 1000 行");
            if (values.some(row => row.length + columnIndex > columns.length)) {
                throw new Error("粘贴列数超出表格范围，请按表头顺序重新复制");
            }
        } catch (failure) {
            showError(failure.message);
            return;
        }
        remember();
        for (let index = body.rows.length; index < rowIndex + values.length; index++) appendRow();
        for (const [offset, value] of values.entries()) {
            const inputs = body.rows[rowIndex + offset].querySelectorAll("textarea");
            value.forEach((text, column) => {
                inputs[columnIndex + column].value = text.replace(/\r\n?/g, "\n");
            });
        }
        renumber();
        notify();
        beforeEdit = rows();
    });
    body.addEventListener("dblclick", event => { cellInput(event)?.select(); });
    body.addEventListener("keydown", event => {
        const input = cellInput(event);
        if (!input || event.isComposing) return;
        if (event.key === "F2") {
            event.preventDefault();
            input.select();
        } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !event.shiftKey) {
            event.preventDefault();
            restore();
        } else if (event.key === "Tab" && !event.shiftKey &&
                   input.closest("tr") === body.lastElementChild &&
                   input.parentElement.cellIndex === columns.length) {
            event.preventDefault();
            add();
        }
    });
    function add() {
        if (body.rows.length >= maxRows) return showError("批量术语最多支持 1000 行");
        remember();
        const row = appendRow();
        renumber();
        notify();
        row.querySelector("textarea").focus();
    }
    function restore() {
        if (!undo.length) return;
        replace(undo.pop());
        notify();
        body.querySelector("textarea").focus();
    }
    button("添加一行", add);
    const undoButton = button("撤销", restore);
    undoButton.disabled = true;
    button("清空表格", () => {
        if (!rows().some(row => row.some(Boolean))) return;
        if (!window.confirm("清空本次批量输入的术语？")) return;
        remember();
        replace([]);
        notify();
        body.querySelector("textarea").focus();
    });
    replace([]);
    return {rows, dirty: () => changed};
};
