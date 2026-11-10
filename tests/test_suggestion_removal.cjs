// Suggestion removal and restoration checks reuse the shared UI harness.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {boot} = require("./test_game_ui.cjs");

const suggestions = [
    {id:"s0", text:"增加相册入口", original_text:"增加相册入口", included:true, feedback_ids:["0001","0002"],
        sources:[{feedback_id:"0001",original_text:"أريد إضافة ألبوم الصور",translation_zh:"希望添加相册。"},
            {feedback_id:"0002",original_text:"相册入口不明显",translation_zh:"希望增加相册入口。"}]},
    {id:"s1", text:"改善活动入口说明", original_text:"改善活动入口说明", included:true, feedback_ids:["0003"],
        sources:[{feedback_id:"0003",original_text:"Etkinlik açıklamasını geliştirin",translation_zh:"希望完善活动说明。"}]}
];

function toggle(input, checked) {
    input.checked = checked;
    input.listeners.change();
}

async function testSuggestionRemoval() {
    const app = boot({pushStatus:"ready",suggestions});
    await app.ready;
    const node = id => app.nodes.get("#" + id);
    const fields = () => vm.runInContext("reviewFields",app.context);
    const evidence = () => JSON.stringify(app.server.suggestions.map(({id,feedback_ids,sources})=>({id,feedback_ids,sources})));
    const saves = () => app.requests.filter(item=>item.path.endsWith("/review") && item.options.method === "POST");
    const reportStarts = () => app.requests.filter(item=>item.path.endsWith("/reports") && item.options.method === "POST");
    const open = () => node("edit-suggestions").listeners.click();
    const save = () => node("review-editor").listeners.submit({preventDefault(){}});
    await node("upload-form").listeners.submit({preventDefault(){},target:node("upload-form")});
    await node("report-form").listeners.submit({preventDefault(){}});
    const originalEvidence = evidence();

    await open();
    assert.equal(fields().length,2);
    assert(fields().every(field=>field.included.checked));
    assert(app.created.some(item=>item.textContent === "保留此建议"));
    const originalSources = fields()[0].card.children.find(item=>item.className === "review-sources");
    assert(originalSources,"Each suggestion retains its original source panel");
    toggle(fields()[0].included,false);
    assert.equal(fields()[0].input.disabled,true);
    assert.equal(fields()[0].input.required,false,"Removed suggestions do not block form validation");
    assert.equal(fields()[0].removed.hidden,false);
    assert(fields()[0].card.children.includes(originalSources),"Excluding a suggestion keeps its source panel in the editor");
    assert.equal(node("save-review").disabled,false,"An inclusion-only change can be saved");
    assert.equal(node("push-feishu").disabled,true);
    assert.equal(node("download-md").getAttribute("aria-disabled"),"true");
    const leaving = {preventDefault(){this.prevented=true;}};
    app.windowListeners.beforeunload(leaving);
    assert.equal(leaving.prevented,true,"Unsaved removal triggers navigation protection");
    fields()[1].input.value = "改善活动入口与玩法说明";
    fields()[1].input.listeners.input();
    await save();
    assert.equal(node("review-editor").hidden,true);
    assert(!node("report-preview").innerHTML.includes(suggestions[0].text));
    assert(node("report-preview").innerHTML.includes("1. 改善活动入口与玩法说明"),"The saved server preview shows the remaining suggestion first");
    assert(node("review-status").textContent.includes("已人工修改"));
    assert.equal(evidence(),originalEvidence,"Saving removals cannot change evidence IDs or original source text");
    const partial = JSON.parse(saves().at(-1).options.body).suggestions;
    assert.deepEqual(partial,[{id:"s0",text:suggestions[0].text,included:false},{id:"s1",text:"改善活动入口与玩法说明",included:true}]);

    await node("edit-bugs").listeners.click();
    fields()[0].input.value = "玩家反映登录时出现异常提示。";
    fields()[0].input.listeners.input();
    await save();
    assert(!node("report-preview").innerHTML.includes(suggestions[0].text),"BUG review preserves the saved suggestion subset");
    assert(node("report-preview").innerHTML.includes("改善活动入口与玩法说明"));
    assert(node("review-status").textContent.includes("已人工修改"));
    assert.equal(evidence(),originalEvidence);

    await open();
    assert.equal(fields().length,2,"Reopening returns excluded entries as well as kept entries");
    assert.equal(fields()[0].included.checked,false);
    assert.equal(fields()[0].removed.hidden,false);
    node("restore-suggestions").listeners.click();
    assert(fields().every(field=>field.included.checked));
    assert(fields().every(field=>field.input.value === field.item.original_text));
    assert.equal(node("save-review").disabled,false);
    node("cancel-review").listeners.click();
    assert.equal(app.server.suggestions[0].included,false,"Cancel discards draft restoration");
    assert.equal(app.server.suggestions[1].text,"改善活动入口与玩法说明");

    await open();
    toggle(fields()[1].included,false);
    assert(fields().every(field=>!field.included.checked));
    assert.equal(node("save-review").disabled,false,"Deleting every suggestion remains saveable");
    await save();
    assert(app.server.suggestions.every(item=>!item.included));
    assert(node("report-preview").innerHTML.includes("本期建议已全部移除。"));
    assert.equal(node("edit-suggestions").disabled,false,"An empty saved section can reopen for restoration");
    assert.equal(evidence(),originalEvidence);

    await open();
    assert.equal(fields().length,2);
    assert(fields().every(field=>field.input.disabled && !field.included.checked));
    assert(fields().every(field=>!field.removed.hidden));
    node("restore-suggestions").listeners.click();
    node("cancel-review").listeners.click();
    assert(app.server.suggestions.every(item=>!item.included),"Cancel after deleting all keeps the saved empty section");
    await open();
    node("restore-suggestions").listeners.click();
    assert(fields().every(field=>field.included.checked && !field.input.disabled && field.input.required));
    await save();
    assert(app.server.suggestions.every(item=>item.included && item.text === item.original_text));
    assert.equal(node("review-status").hidden,true,"Restoring originals clears the suggestion modification badge");
    assert(node("bug-review-status").textContent.includes("BUG 已人工审核修改"),"Suggestion restoration preserves independent BUG edits");
    assert(node("report-preview").innerHTML.includes(suggestions[0].text));
    assert(node("report-preview").innerHTML.includes(suggestions[1].text));
    assert.equal(evidence(),originalEvidence);

    await open();
    toggle(fields()[0].included,false);
    await save();
    const version = app.server.version;
    await node("push-feishu").listeners.click();
    const push = app.requests.find(item=>item.path.endsWith("/push/feishu"));
    assert.equal(JSON.parse(push.options.body).report_version,version);
    assert.equal(node("edit-suggestions").disabled,true);
    assert.equal(node("edit-bugs").disabled,true);
    const callsAfterPush = app.calls.length;
    await open();
    assert.equal(app.calls.length,callsAfterPush,"Push locking prevents reopening a removal editor");
    assert.equal(app.server.suggestions[0].included,false);
    assert.equal(evidence(),originalEvidence);
    assert.equal(reportStarts().length,1,"Removing, restoring and reviewing suggestions never regenerates the AI report");
    console.log("Suggestion removal passed: partial/all removal, immutable sources, BUG preservation, unsaved protection, cancel/restore, payload flags and push locking.");
}

testSuggestionRemoval().catch(error => {console.error(error);process.exitCode=1;});
