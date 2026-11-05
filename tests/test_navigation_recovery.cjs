// Navigation and task recovery checks reuse the browser-independent UI harness.
const assert = require("node:assert/strict");
const {boot} = require("./test_game_ui.cjs");

const RUN = "a".repeat(32), JOB = "b".repeat(32);
const NEW_RUN = "c".repeat(32), NEW_JOB = "d".repeat(32);
const storageKey = "feedback-report:baloot";

function task(status = "running", overrides = {}) {
    return {
        id:JOB, game:"baloot", run:RUN, status, stage:status === "complete" ? "complete" : "processing",
        message:status === "complete" ? "报告已生成" : "已处理部分反馈", done:4, total:10,
        options:{start:"2026-10-19", end:"2026-10-22", confirmed:false},
        result:status === "complete" ? {total:2, previous_total:1, selection:{processed_count:3, selected_count:3,
            periods:{current:{start:"2026-10-19",end:"2026-10-22"},previous:{start:"2026-10-12",end:"2026-10-15"}}}} : null,
        ...overrides
    };
}

function metadata(run = RUN) {
    return {run, game:"baloot", filename:run === RUN ? "original.csv" : "new.csv", total:10, valid:10, excluded:0,
        source_start:"2026-10-12", source_end:"2026-11-03", suggested_start:"2026-11-01", suggested_end:"2026-11-03"};
}

function storage(reference) {
    const values = new Map(reference === undefined ? [] : [[storageKey, JSON.stringify(reference)]]);
    return {
        getItem(key) { return values.get(key) ?? null; },
        setItem(key, value) { values.set(key, String(value)); },
        removeItem(key) { values.delete(key); }
    };
}

function response(data, status = 200) {
    return {ok:status >= 200 && status < 300, status, json:async()=>JSON.parse(JSON.stringify(data))};
}

function scenario(options = {}) {
    const state = {active:options.active || null, task:options.task || task(), missing:options.missing,
        checkFailed:options.checkFailed || false, metadataFailed:options.metadataFailed || false,
        previewFailed:options.previewFailed || false, conflict:null, stopStatus:"stopping", deferStatus:false, deferUpload:false};
    const sessionStorage = options.sessionStorage || storage(options.reference);
    const app = boot({url:options.url || "http://localhost/games/baloot", sessionStorage, pushStatus:options.pushStatus,
        respond:async(path, request) => {
            if (path === "/api/jobs/active") {
                if (state.checkFailed) return response({error:"暂时无法检查正在处理的任务。"}, 503);
                return response({job:state.active});
            }
            if (path === "/api/games/baloot/uploads" && request.method === "POST") {
                if (state.deferUpload) {
                    state.deferUpload = false;
                    return new Promise(resolve => { state.releaseUpload = () => resolve(response(metadata(NEW_RUN), 201)); });
                }
                return response(metadata(NEW_RUN), 201);
            }
            if (path.includes("/preview?") && state.previewFailed) return response({error:"列映射已变更，请重新上传文件。"}, 400);
            if (/\/uploads\/[a-f0-9]{32}$/.test(path)) {
                if (state.metadataFailed) return response({error:"暂时无法读取上传记录。"}, 503);
                if (state.missing === "upload") return response({error:"上传记录不存在。"}, 400);
                return response(metadata(path.split("/").at(-1)));
            }
            if (/\/jobs\/[a-f0-9]{32}$/.test(path)) {
                if (state.missing === "job") return response({error:"任务不存在。"}, 404);
                if (state.deferStatus) {
                    state.deferStatus = false;
                    const pending = response(state.task);
                    return new Promise(resolve => { state.releaseStatus = () => resolve(pending); });
                }
                return response(state.task);
            }
            if (path.endsWith("/reports") && request.method === "POST") {
                if (state.conflict) {
                    state.active = state.conflict;
                    return response({error:"另一个报告正在处理，请完成或停止后再试。", active_job:state.conflict}, 409);
                }
                state.task = task("running", {id:NEW_JOB, run:path.split("/").at(-2), options:JSON.parse(request.body)});
                state.active = state.task;
                return response(state.task, 202);
            }
            if (path.endsWith("/stop")) {
                state.task = {...state.task, status:state.stopStatus};
                state.active = state.stopStatus === "stopped" ? null : state.task;
                return response(state.task);
            }
            return undefined;
        }});
    return {...app, state, get:id=>app.nodes.get("#" + id)};
}

function reportPosts(app) {
    return app.requests.filter(request => request.path.endsWith("/reports") && request.options.method === "POST");
}

async function submitUpload(app) {
    const form = app.get("upload-form");
    await form.listeners.submit({preventDefault(){},target:form});
}

async function submitReport(app) {
    await app.get("report-form").listeners.submit({preventDefault(){}});
}

function assertOriginalDates(app) {
    assert.equal(app.get("start").value,"2026-10-19","Recovery uses the task's original start date");
    assert.equal(app.get("end").value,"2026-10-22","Recovery uses the task's original end date");
    assert.equal(app.get("file-label").textContent,"original.csv");
}

async function testRecovery() {
    const running = scenario({active:task()});
    await running.ready;
    assertOriginalDates(running);
    assert.equal(running.get("job-status").hidden,false);
    assert.equal(running.get("job-progress").value,4);
    assert.equal(running.get("stop-button").hidden,false);
    assert.equal(running.get("generate-button").disabled,true);
    assert.equal(reportPosts(running).length,0,"Recovering never starts another paid report");
    assert.deepEqual(JSON.parse(running.sessionStorage.getItem(storageKey)),{run:RUN,job:JOB});
    await running.get("stop-button").listeners.click();
    assert(running.calls.includes(`/api/games/baloot/uploads/${RUN}/jobs/${JOB}/stop`));
    assert.equal(running.get("generate-button").disabled,true,"A stop request must finish before another report can start");
    running.state.active = null;
    running.state.task = task("stopped");
    await running.runTimer();
    assert.equal(running.get("stop-button").hidden,true);
    assert.equal(running.get("generate-button").disabled,false);

    const changedMapping = scenario({active:task(),previewFailed:true});
    await changedMapping.ready;
    assertOriginalDates(changedMapping);
    assert.deepEqual(JSON.parse(changedMapping.sessionStorage.getItem(storageKey)),{run:RUN,job:JOB},"A preview validation error must retain valid upload/job references");
    assert.equal(changedMapping.get("job-status").hidden,false);
    assert.equal(changedMapping.get("job-progress").value,4);
    assert.equal(changedMapping.get("stop-button").hidden,false,"Preview failure does not hide an active task's Stop control");
    assert(changedMapping.get("error").textContent.includes("列映射"));
    await changedMapping.get("stop-button").listeners.click();
    assert(changedMapping.calls.includes(`/api/games/baloot/uploads/${RUN}/jobs/${JOB}/stop`));
    assert.equal(changedMapping.get("generate-button").disabled,true);

    for (const active of [null, task("running")]) {
        // The second case completes between active-task discovery and status loading.
        const completed = scenario({active, task:task("complete"), reference:{run:RUN,job:JOB}});
        await completed.ready;
        assertOriginalDates(completed);
        assert.equal(completed.get("report-preview").hidden,false);
        assert.equal(completed.get("downloads").hidden,false);
        assert.equal(completed.get("download-md").href,`/api/games/baloot/uploads/${RUN}/jobs/${JOB}/download/markdown`);
        assert.equal(completed.get("stop-button").hidden,true);
        assert.equal(reportPosts(completed).length,0,"A completed-away report is reopened without regeneration");
    }

    for (const status of ["stopped","failed","interrupted"]) {
        const resumable = scenario({task:task(status), reference:{run:RUN,job:JOB}});
        await resumable.ready;
        assertOriginalDates(resumable);
        assert.equal(resumable.get("generate-button").disabled,false,`${status} work can be retried`);
        assert.equal(resumable.get("stop-button").hidden,true);
        assert.equal(reportPosts(resumable).length,0,"Retry always requires an explicit action");
        await submitReport(resumable);
        assert.equal(reportPosts(resumable).length,1);
        assert.equal(reportPosts(resumable)[0].path,`/api/games/baloot/uploads/${RUN}/reports`,"Retry reuses the original upload's cache");
        assert.equal(JSON.parse(reportPosts(resumable)[0].options.body).start,"2026-10-19");
    }

    const oldReference = {run:NEW_RUN,job:NEW_JOB};
    const priority = scenario({active:task(), reference:oldReference});
    await priority.ready;
    assertOriginalDates(priority);
    assert(!priority.calls.includes(`/api/games/baloot/uploads/${NEW_RUN}`),"Server-active work takes priority over an older remembered report");

    const crossGame = scenario({active:task("running",{game:"okey"}), reference:oldReference,
        task:task("complete",{run:NEW_RUN,id:NEW_JOB})});
    await crossGame.ready;
    assert.equal(crossGame.get("report-recovery").hidden,false);
    assert.equal(crossGame.get("generate-button").disabled,true);
    assert.deepEqual(JSON.parse(crossGame.sessionStorage.getItem(storageKey)),oldReference,"Another game's task does not overwrite this game's remembered report");
    await crossGame.get("recovery-open").listeners.click();
    const crossUrl = new URL(crossGame.url());
    assert.equal(crossUrl.pathname,"/games/okey");
    assert.equal(crossUrl.searchParams.get("upload"),RUN);
    assert.equal(crossUrl.searchParams.get("job"),JOB);

    const uploadRace = scenario({active:task("running",{game:"okey"})});
    await uploadRace.ready;
    uploadRace.state.deferUpload = true;
    const uploading = submitUpload(uploadRace);
    await new Promise(setImmediate);
    assert.equal(typeof uploadRace.state.releaseUpload,"function");
    assert.equal(uploadRace.get("recovery-open").disabled,true,"Recovery cannot interrupt an in-flight upload");
    const uploadingUrl = uploadRace.url(), uploadingCalls = uploadRace.calls.length;
    await uploadRace.get("recovery-open").listeners.click();
    assert.equal(uploadRace.url(),uploadingUrl,"A queued recovery click cannot navigate away during upload");
    assert.equal(uploadRace.calls.length,uploadingCalls,"A queued recovery click cannot restore another workspace during upload");
    uploadRace.state.releaseUpload();
    await uploading;
    assert.equal(uploadRace.get("file-label").textContent,"new.csv");
    assert.equal(uploadRace.get("recovery-open").disabled,false);

    const editingRace = scenario({active:task("running",{game:"okey"}),task:task("complete"),
        reference:{run:RUN,job:JOB},pushStatus:"ready"});
    await editingRace.ready;
    await editingRace.get("edit-suggestions").listeners.click();
    const draft = editingRace.created.find(node => node.id === "suggestion-draft-0");
    assert(draft,"The saved report opens its suggestion editor");
    draft.value = "尚未保存的人工修改";
    draft.listeners.input();
    assert.equal(editingRace.get("recovery-open").disabled,true,"Recovery cannot discard an open review draft");
    const editingUrl = editingRace.url(), editingCalls = editingRace.calls.length;
    await editingRace.get("recovery-open").listeners.click();
    assert.equal(editingRace.url(),editingUrl);
    assert.equal(editingRace.calls.length,editingCalls);
    assert.equal(editingRace.get("review-editor").hidden,false);
    assert.equal(draft.value,"尚未保存的人工修改");
    editingRace.get("cancel-review").listeners.click();
    assert.equal(editingRace.get("recovery-open").disabled,false);

    const linked = scenario({task:task("complete"),url:`http://localhost/games/baloot?upload=${RUN}&job=${JOB}`});
    await linked.ready;
    assertOriginalDates(linked);
    assert.equal(linked.get("report-preview").hidden,false,"Explicit report links recover without browser storage");

    for (const missing of ["upload","job"]) {
        const stale = scenario({missing, reference:{run:RUN,job:JOB}});
        await stale.ready;
        assert.equal(stale.sessionStorage.getItem(storageKey),null,"Missing server records clear their stale reference");
        assert.equal(stale.get("file").disabled,false,"A missing report does not trap the upload controls");
        assert.equal(reportPosts(stale).length,0);
    }

    const blockedStorage = {getItem(){throw new Error("Storage blocked");},setItem(){throw new Error("Storage blocked");},removeItem(){throw new Error("Storage blocked");}};
    const privateBrowser = scenario({active:task(),sessionStorage:blockedStorage});
    await privateBrowser.ready;
    assertOriginalDates(privateBrowser);
    assert.equal(privateBrowser.get("stop-button").hidden,false,"Server discovery works when browser storage is blocked");
    const privateFresh = scenario({sessionStorage:blockedStorage});
    await privateFresh.ready;
    await submitUpload(privateFresh);
    assert.equal(privateFresh.get("file-label").textContent,"new.csv","Blocked storage removal does not prevent a fresh upload");

    for (const value of ["{", "[]", JSON.stringify({run:12,job:JOB})]) {
        const malformedStorage = storage();
        malformedStorage.setItem(storageKey,value);
        const malformed = scenario({sessionStorage:malformedStorage});
        await malformed.ready;
        assert.equal(malformedStorage.getItem(storageKey),null,"Malformed browser references are discarded safely");
        assert.equal(malformed.get("file").disabled,false);
    }

    const unavailable = scenario({checkFailed:true, reference:{run:RUN,job:JOB}, task:task("complete")});
    await unavailable.ready;
    assert.equal(unavailable.get("report-recovery").hidden,false);
    assert.equal(unavailable.get("recovery-open").disabled,false);
    assert(unavailable.sessionStorage.getItem(storageKey),"A failed connection check must retain a recoverable reference");
    unavailable.state.checkFailed = false;
    unavailable.state.active = task();
    unavailable.state.task = task();
    await unavailable.get("recovery-open").listeners.click();
    assertOriginalDates(unavailable);
    assert.equal(unavailable.get("stop-button").hidden,false);

    const metadataOffline = scenario({active:task(),metadataFailed:true});
    await metadataOffline.ready;
    assert(metadataOffline.sessionStorage.getItem(storageKey),"A discovered task is remembered even if its metadata request fails");
    assert.equal(metadataOffline.get("report-recovery").hidden,false);
    assert.equal(metadataOffline.get("generate-button").disabled,true);
    metadataOffline.state.active = null;
    metadataOffline.state.task = task("complete");
    metadataOffline.state.metadataFailed = false;
    await metadataOffline.get("recovery-open").listeners.click();
    assertOriginalDates(metadataOffline);
    assert.equal(metadataOffline.get("report-preview").hidden,false,"Metadata retry can reopen a task that completed during the outage");

    const conflict = scenario();
    await conflict.ready;
    await submitUpload(conflict);
    conflict.state.conflict = task();
    await submitReport(conflict);
    assert.equal(conflict.get("report-recovery").hidden,false,"A task started elsewhere is recoverable from the conflict response");
    await conflict.get("recovery-open").listeners.click();
    assertOriginalDates(conflict);
    assert.equal(conflict.get("stop-button").hidden,false);
    assert.equal(reportPosts(conflict).length,1,"Opening the existing task does not resubmit generation");

    const finishedConflict = scenario();
    await finishedConflict.ready;
    await submitUpload(finishedConflict);
    finishedConflict.state.conflict = task();
    await submitReport(finishedConflict);
    finishedConflict.state.active = null;
    finishedConflict.state.task = task("complete");
    await finishedConflict.runTimer();
    assert.equal(finishedConflict.get("report-recovery").hidden,false,"A completed conflict keeps its Open action after the blocker watcher runs");
    await finishedConflict.get("recovery-open").listeners.click();
    assertOriginalDates(finishedConflict);
    assert.equal(finishedConflict.get("report-preview").hidden,false,"A conflicting task remains recoverable if it finishes before the user opens it");
    assert.equal(reportPosts(finishedConflict).length,1);

    const conflictBeforeRefresh = scenario();
    await conflictBeforeRefresh.ready;
    await submitUpload(conflictBeforeRefresh);
    conflictBeforeRefresh.state.conflict = task();
    await submitReport(conflictBeforeRefresh);
    const refreshedConflict = scenario({sessionStorage:conflictBeforeRefresh.sessionStorage, task:task("complete")});
    await refreshedConflict.ready;
    assertOriginalDates(refreshedConflict);
    assert.equal(refreshedConflict.get("report-preview").hidden,false,"Refreshing before opening a conflict still recovers its completed report");
    assert.equal(reportPosts(refreshedConflict).length,0);

    const stalePoll = scenario({active:task()});
    await stalePoll.ready;
    stalePoll.state.deferStatus = true;
    const polling = stalePoll.runTimer();
    await new Promise(setImmediate);
    assert.equal(typeof stalePoll.state.releaseStatus,"function");
    stalePoll.state.stopStatus = "stopped";
    await stalePoll.get("stop-button").listeners.click();
    await submitUpload(stalePoll);
    stalePoll.state.releaseStatus();
    await polling;
    assert.equal(stalePoll.get("file-label").textContent,"new.csv");
    assert.equal(stalePoll.get("job-status").hidden,true,"A late response from the old job cannot replace the new upload's state");
    assert.equal(stalePoll.get("generate-button").disabled,false);

    console.log("Navigation recovery passed: running/completed work, retries, server priority, cross-game links, stale references, blocked storage, failed checks, conflicts and stale polling.");
}

testRecovery().catch(error => {console.error(error);process.exitCode=1;});
