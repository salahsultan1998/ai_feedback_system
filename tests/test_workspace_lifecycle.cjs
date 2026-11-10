const assert = require('node:assert/strict');
const vm = require('node:vm');
const {boot} = require('./test_game_ui.cjs');

(async () => {
    const app = boot({url:'http://localhost/games/baloot?upload=old&job=old', workspaceToken:'page-one',
        sessionStorage:{getItem(){throw Error('Must not restore');},setItem(){throw Error('Must not persist');},removeItem(){}}});
    await app.ready;
    assert(!app.url().includes('upload=') && !app.url().includes('job='));
    assert(app.nodes.get('#content-section').hidden);
    assert.deepEqual(app.calls, ['/api/jobs/active']);
    assert.equal(app.requests[0].options.headers['X-Workspace'], 'page-one');
    await app.nodes.get('#upload-form').listeners.submit({preventDefault(){},target:app.nodes.get('#upload-form')});
    await app.nodes.get('#report-form').listeners.submit({preventDefault(){}});
    assert(!app.url().includes('job='));
    assert(app.nodes.get('#download-md').href.endsWith('?workspace=page-one'));
    app.windowListeners.pagehide();
    const close = app.requests.at(-1);
    assert.equal(close.path, '/api/workspaces/close');
    assert.equal(close.options.keepalive, true);
    assert.equal(close.options.headers['X-Workspace'], 'page-one');
    assert.equal(close.options.headers['X-CSRF-Token'], 'test-token');
    assert.equal(app.timers.size, 0);

    const fresh = boot({workspaceToken:'page-two'});
    await fresh.ready;
    assert(fresh.nodes.get('#content-section').hidden);
    fresh.windowListeners.pageshow({persisted:true});
    assert(vm.runInContext('window.location.reloaded', fresh.context));

    let busy = true;
    const waiting = boot({respond(path){if(path === '/api/jobs/active') return {ok:true,json:async()=>({busy})};}});
    await waiting.ready;
    assert(!waiting.nodes.get('#report-recovery').hidden);
    assert(waiting.nodes.get('#content-section').hidden);
    assert.deepEqual(waiting.calls, ['/api/jobs/active']);
    busy = false;
    await vm.runInContext('checkAvailability()', waiting.context);
    assert(waiting.nodes.get('#report-recovery').hidden);
    await vm.runInContext('heartbeat()', waiting.context);
    assert.equal(waiting.requests.at(-1).path, '/api/workspaces/ping');
    console.log('Temporary workspace passed: blank reload, no report persistence, ownership headers, close cleanup, heartbeat and busy-slot release.');
})().catch(error => {console.error(error);process.exit(1);});
