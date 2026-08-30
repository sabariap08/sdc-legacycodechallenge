let editor = null;
let monacoLoaded = false;
let openFiles = {};
let activeFile = null;
let fileTree = [];
let eventEnd = null;
let codeDetails = null;
let autoSubmitted = false;
let serverTimeOffset = 0;
let currentModel = null;
let isRunning = false;
let currentJobId = null;
let autosaveTimer = null;
let errorCount = 0;

require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' } });
require(['vs/editor/editor.main'], function () {
    monacoLoaded = true;
    checkTCAndLoadIDE();
});

async function checkTCAndLoadIDE() {
    try {
        const resp = await API.get('/api/participant/tc-status');
        if (resp && resp.ok) {
            const data = await resp.json();
            if (!data.accepted) { window.location.href = '/participant/dashboard'; return; }
        }
    } catch (e) {}
    initEditor();
    loadIDE();
}

function initEditor() {
    const container = document.getElementById('editorContainer');
    container.innerHTML = '';
    editor = monaco.editor.create(container, {
        value: '',
        language: 'python',
        theme: document.documentElement.getAttribute('data-theme') === 'dark' ? 'vs-dark' : 'vs',
        automaticLayout: true,
        fontSize: 14,
        fontFamily: "'JetBrains Mono','Fira Code','Cascadia Code','Consolas',monospace",
        fontLigatures: true,
        minimap: { enabled: true, maxColumn: 80 },
        scrollBeyondLastLine: false,
        wordWrap: 'on',
        lineNumbers: 'on',
        renderWhitespace: 'selection',
        tabSize: 4,
        insertSpaces: true,
        cursorBlinking: 'smooth',
        cursorSmoothCaretAnimation: 'on',
        smoothScrolling: true,
        bracketPairColorization: { enabled: true },
        guides: { bracketPairs: true, indentation: true },
        padding: { top: 8 },
        folding: true,
        foldingStrategy: 'indentation',
        showFoldingControls: 'mouseover',
        matchBrackets: 'always',
        autoClosingBrackets: 'always',
        autoClosingQuotes: 'always',
        autoIndent: 'full',
        formatOnPaste: true,
        formatOnType: true,
        suggestOnTriggerCharacters: true,
        quickSuggestions: true,
        wordBasedSuggestions: 'off',
        parameterHints: { enabled: true },
        scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8, useShadows: false },
        renderLineHighlight: 'all',
        occurrencesHighlight: 'singleFile',
        selectionHighlight: true,
        links: true,
        colorDecorators: true,
        contextmenu: true,
        mouseWheelZoom: true,
    });

    editor.onDidChangeModelContent(function() {
        if (activeFile && openFiles[activeFile]) {
            openFiles[activeFile].modified = true;
            updateTabStatus(activeFile, true);
            updateSaveButton(true);
            scheduleAutosave();
        }
    });

    editor.onDidChangeCursorPosition(function(e) {
        var pos = e.position;
        var el = document.getElementById('statusLineCol');
        if (el) el.textContent = 'Ln ' + pos.lineNumber + ', Col ' + pos.column;
        var linesEl = document.getElementById('statusLines');
        if (linesEl) linesEl.style.display = 'block';
    });

    editor.onDidChangeModelContent(function() {
        updateCursorPosition();
    });

    editor.addAction({
        id: 'save-file',
        label: 'Save File',
        keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
        run: function() { saveFile(); }
    });

    editor.addAction({
        id: 'run-code',
        label: 'Run Code',
        keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
        run: function() { runCode(); }
    });

    editor.addAction({
        id: 'submit-solution',
        label: 'Submit Solution',
        keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Enter],
        run: function() { submitFinal(); }
    });
}

function updateCursorPosition() {
    if (!editor) return;
    var pos = editor.getPosition();
    if (pos) {
        var el = document.getElementById('statusLineCol');
        if (el) el.textContent = 'Ln ' + pos.lineNumber + ', Col ' + pos.column;
        var linesEl = document.getElementById('statusLines');
        if (linesEl) linesEl.style.display = 'block';
    }
}

function getLanguage(filename) {
    var ext = filename.split('.').pop().toLowerCase();
    var map = {
        'py': 'python', 'js': 'javascript', 'ts': 'typescript', 'jsx': 'javascript',
        'tsx': 'typescript', 'html': 'html', 'htm': 'html', 'css': 'css',
        'json': 'json', 'md': 'markdown', 'yml': 'yaml', 'yaml': 'yaml',
        'xml': 'xml', 'java': 'java', 'c': 'c', 'cpp': 'cpp', 'cc': 'cpp',
        'h': 'c', 'hpp': 'cpp', 'go': 'go', 'rs': 'rust', 'rb': 'ruby',
        'php': 'php', 'sh': 'shell', 'bash': 'shell', 'sql': 'sql',
        'txt': 'plaintext', 'env': 'plaintext', 'gitignore': 'plaintext',
        'toml': 'plaintext', 'ini': 'plaintext', 'cfg': 'plaintext',
    };
    return map[ext] || 'plaintext';
}

function getLanguageName(filename) {
    var ext = filename.split('.').pop().toLowerCase();
    var map = {
        'py': 'Python', 'js': 'JavaScript', 'ts': 'TypeScript',
        'java': 'Java', 'c': 'C', 'cpp': 'C++', 'cc': 'C++',
        'go': 'Go', 'rs': 'Rust', 'rb': 'Ruby',
        'html': 'HTML', 'css': 'CSS', 'json': 'JSON', 'md': 'Markdown',
    };
    return map[ext] || ext.toUpperCase();
}

function showIDEBlocked(message) {
    var treeEl = document.getElementById('fileTree');
    if (treeEl) treeEl.innerHTML = '';
    var codeName = document.getElementById('challengeNameDisplay');
    if (codeName) codeName.textContent = 'Challenge Unavailable';
    var codeCode = document.getElementById('challengeCodeDisplay');
    if (codeCode) codeCode.textContent = '--';
    var statusTeamCode = document.getElementById('statusTeamCode');
    if (statusTeamCode) statusTeamCode.textContent = '';
    var blocked = document.createElement('div');
    blocked.style.cssText = 'display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;height:100%;min-height:60vh;padding:40px;text-align:center;';
    blocked.innerHTML =
        '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="var(--ide-accent)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.7"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' +
        '<strong style="font-size:18px;color:var(--ide-text);">' + escapeHtml(message) + '</strong>' +
        '<a href="/participant/dashboard" style="display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border-radius:8px;background:linear-gradient(135deg,var(--ide-accent),var(--ide-accent2));color:#fff;font-weight:600;text-decoration:none;font-size:14px;">Back to Dashboard</a>';
    var editorArea = document.querySelector('.ide-editor-area') || document.body;
    editorArea.insertBefore(blocked, editorArea.firstChild);
}

async function loadIDE() {
    try {
        var resp = await API.get('/api/workspace/code-details');
        if (!resp) return;
        if (!resp.ok) {
            var errData = null;
            try { errData = await resp.json(); } catch (e) {}
            var detail = (errData && errData.detail) ? errData.detail : 'Your challenge is not yet available.';
            showIDEBlocked(detail);
            return;
        }
        codeDetails = await resp.json();

        document.getElementById('challengeCodeDisplay').textContent = codeDetails.challenge_code;
        document.getElementById('challengeNameDisplay').textContent = codeDetails.challenge_name || '';
        document.getElementById('statusTeamCode').textContent = codeDetails.team_code;
        document.getElementById('statusBinNumber').textContent = codeDetails.bin_number || '-';

        try {
            var timeResp = await fetch('/api/server-time');
            if (timeResp.ok) {
                var timeData = await timeResp.json();
                serverTimeOffset = new Date(timeData.server_time).getTime() - Date.now();
            }
        } catch (e) {}

        if (codeDetails.event_start && codeDetails.event_end) {
            eventEnd = new Date(codeDetails.event_end);
            startTimer();
        }

        if (codeDetails.has_evaluator) {
            var testTab = document.getElementById('testTab');
            if (testTab) testTab.style.display = '';
        }

        renderChallengePanel();
        await loadFileTree();
        checkEventStatus();
    } catch (err) {
        console.error('IDE init error:', err);
    }
}

async function checkEventStatus() {
    try {
        var resp = await API.get('/api/participant/event-countdown');
        if (!resp) return;
        var data = await resp.json();
        if (data.status === 'COMPLETED' && !autoSubmitted) {
            await performAutoSubmit();
        }
    } catch (e) {}
}

async function loadFileTree() {
    try {
        var resp = await API.get('/api/workspace/tree');
        if (!resp) return;
        if (!resp.ok) {
            var errData = null;
            try { errData = await resp.json(); } catch (e) {}
            var detail = (errData && errData.detail) ? errData.detail : 'Could not load the challenge files.';
            showIDEBlocked(detail);
            return;
        }
        var data = await resp.json();
        fileTree = data.tree || [];
        if (!fileTree.length) {
            showIDEBlocked('Challenge files are unavailable. The repository has no readable files - please contact the organizer.');
            return;
        }
        renderFileTree();
    } catch (err) {
        console.error('File tree error:', err);
        showIDEBlocked('Could not load the challenge files. Please try again or contact the organizer.');
    }
}

function renderFileTree() {
    var container = document.getElementById('fileTree');
    container.innerHTML = '';
    renderNodes(fileTree, container, 0);
}

function renderNodes(nodes, container, depth) {
    for (var i = 0; i < nodes.length; i++) {
        var node = nodes[i];
        var item = document.createElement('div');
        item.className = 'ide-file-item' + (node.type === 'directory' ? ' directory' : '') + (activeFile === node.path ? ' active' : '');

        var indent = document.createElement('span');
        indent.className = 'indent';
        indent.style.width = (depth * 16) + 'px';
        item.appendChild(indent);

        var icon = document.createElement('span');
        icon.className = 'icon';
        if (node.type === 'directory') {
            icon.innerHTML = node._expanded
                ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="9" y1="14" x2="15" y2="14"/></svg>'
                : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
        } else {
            var ext = node.name.split('.').pop().toLowerCase();
            var color = 'var(--ide-text-dim)';
            if (['py'].includes(ext)) color = '#3572A5';
            else if (['js'].includes(ext)) color = '#f1e05a';
            else if (['ts'].includes(ext)) color = '#3178c6';
            else if (['java'].includes(ext)) color = '#b07219';
            else if (['c', 'h'].includes(ext)) color = '#555555';
            else if (['cpp', 'cc', 'hpp'].includes(ext)) color = '#f34b7d';
            else if (['go'].includes(ext)) color = '#00ADD8';
            else if (['rs'].includes(ext)) color = '#dea584';
            else if (['html', 'htm'].includes(ext)) color = '#e34c26';
            else if (['css'].includes(ext)) color = '#563d7c';
            else if (['json'].includes(ext)) color = '#292929';
            else if (['md'].includes(ext)) color = '#083fa1';
            icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>';
        }
        item.appendChild(icon);

        var name = document.createElement('span');
        name.className = 'name';
        name.textContent = node.name;
        item.appendChild(name);

        (function(n, el) {
            el.addEventListener('click', function() {
                if (n.type === 'directory') {
                    n._expanded = !n._expanded;
                    renderFileTree();
                } else if (n.binary) {
                    appendOutput('info', 'Binary file - cannot open: ' + n.name);
                } else {
                    openFile(n.path);
                }
            });
        })(node, item);

        container.appendChild(item);

        if (node.type === 'directory' && node._expanded && node.children) {
            renderNodes(node.children, container, depth + 1);
        }
    }
}

async function openFile(path) {
    if (openFiles[path]) { switchToFile(path); return; }

    try {
        var resp = await API.get('/api/workspace/file?path=' + encodeURIComponent(path));
        if (!resp) return;
        var data = await resp.json();

        if (data.binary) {
            appendOutput('info', 'Cannot open binary file: ' + path);
            return;
        }

        openFiles[path] = {
            content: data.content,
            originalContent: data.content,
            modified: false,
            language: getLanguage(path.split('/').pop()),
            editable: data.editable !== false,
        };

        createTab(path);
        switchToFile(path);
    } catch (err) {
        console.error('File open error:', err);
    }
}

function createTab(path) {
    var tabs = document.getElementById('editorTabs');
    var filename = path.split('/').pop();

    var tab = document.createElement('div');
    tab.className = 'ide-tab';
    tab.id = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');

    var tabName = document.createElement('span');
    tabName.textContent = filename;

    var dot = document.createElement('span');
    dot.className = 'dot';

    var closeBtn = document.createElement('span');
    closeBtn.className = 'close';
    closeBtn.innerHTML = '&times;';
    closeBtn.onclick = function(e) { e.stopPropagation(); closeFile(path); };

    tab.appendChild(tabName);
    tab.appendChild(dot);
    tab.appendChild(closeBtn);
    tab.addEventListener('click', function() { switchToFile(path); });
    tabs.appendChild(tab);
}

function switchToFile(path) {
    if (!openFiles[path]) return;

    activeFile = path;
    var file = openFiles[path];

    if (editor && monacoLoaded) {
        if (currentModel) { currentModel.dispose(); }
        currentModel = monaco.editor.createModel(file.content, file.language);
        editor.setModel(currentModel);
        if (!file.editable) {
            monaco.editor.setModelLanguage(currentModel, file.language);
        }
    }

    document.querySelectorAll('.ide-tab').forEach(function(t) { t.classList.remove('active'); });
    var tabId = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');
    var tab = document.getElementById(tabId);
    if (tab) tab.classList.add('active');

    document.getElementById('statusFile').style.display = 'block';
    document.getElementById('statusFilePath').textContent = path;
    document.getElementById('langBadge').textContent = getLanguageName(path);

    renderFileTree();
    updateCursorPosition();
}

function closeFile(path) {
    if (openFiles[path] && openFiles[path].modified) {
        if (!confirm('Unsaved changes in ' + path.split('/').pop() + '. Close anyway?')) return;
    }

    delete openFiles[path];
    var tabId = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');
    var tab = document.getElementById(tabId);
    if (tab) tab.remove();

    if (activeFile === path) {
        var remaining = Object.keys(openFiles);
        if (remaining.length > 0) {
            switchToFile(remaining[remaining.length - 1]);
        } else {
            activeFile = null;
            if (editor && monacoLoaded) { editor.setModel(null); }
            document.getElementById('statusFile').style.display = 'none';
            document.getElementById('statusLines').style.display = 'none';
            document.getElementById('langBadge').textContent = '-';
        }
    }
}

function updateTabStatus(path, modified) {
    var tabId = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');
    var tab = document.getElementById(tabId);
    if (tab) tab.classList.toggle('modified', modified);
}

function updateSaveButton(modified) {
    var btn = document.getElementById('saveBtn');
    if (btn) btn.classList.toggle('modified', modified);
}

function saveFile() {
    if (!activeFile || !openFiles[activeFile]) return;
    var content = editor.getValue();
    var path = activeFile;

    var btn = document.getElementById('saveBtn');
    btn.disabled = true;

    fetch('/api/workspace/file/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
        body: JSON.stringify({ path: path, content: content })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.message) {
            openFiles[path].content = content;
            openFiles[path].modified = false;
            updateTabStatus(path, false);
            updateSaveButton(false);
            appendOutput('success', 'File saved: ' + path);
        } else {
            appendOutput('error', 'Save failed: ' + (data.detail || 'Unknown error'));
        }
    })
    .catch(function(err) {
        appendOutput('error', 'Save error: ' + err.message);
    })
    .finally(function() {
        btn.disabled = false;
    });
}

function scheduleAutosave() {
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(function() {
        if (activeFile && openFiles[activeFile] && openFiles[activeFile].modified && editor) {
            fetch('/api/workspace/file/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
                body: JSON.stringify({ path: activeFile, content: editor.getValue() })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.message) {
                    openFiles[activeFile].content = editor.getValue();
                    openFiles[activeFile].modified = false;
                    updateTabStatus(activeFile, false);
                    updateSaveButton(false);
                }
            })
            .catch(function() {});
        }
    }, 3000);
}

function autoSaveAll() {
    var promises = [];
    for (var path in openFiles) {
        if (openFiles[path].modified) {
            promises.push(
                fetch('/api/workspace/file/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
                    body: JSON.stringify({ path: path, content: openFiles[path].content })
                })
            );
        }
    }
    return Promise.all(promises);
}

function switchBottomPanel(panel, el) {
    document.querySelectorAll('.ide-bottom-tab').forEach(function(t) { t.classList.remove('active'); });
    if (el) el.classList.add('active');
    document.getElementById('outputPanel').style.display = panel === 'output' ? 'block' : 'none';
    document.getElementById('errorsPanel').style.display = panel === 'errors' ? 'block' : 'none';
    document.getElementById('testsPanel').style.display = panel === 'tests' ? 'block' : 'none';
}

function clearPanel() {
    document.getElementById('outputPanel').innerHTML = '<div class="line line-info">Ready. Click Run to execute your code.</div>';
    document.getElementById('errorsPanel').innerHTML = '';
    document.getElementById('testsPanel').innerHTML = '';
    errorCount = 0;
    var badge = document.getElementById('errorBadge');
    if (badge) { badge.style.display = 'none'; badge.textContent = '0'; }
    var testBadge = document.getElementById('testBadge');
    if (testBadge) { testBadge.style.display = 'none'; }
}

function appendOutput(type, text) {
    var panel = document.getElementById('outputPanel');
    var line = document.createElement('div');
    line.className = 'line line-' + type;
    line.textContent = text;
    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;
}

function setOutputPanel(id, html) {
    document.getElementById(id).innerHTML = html;
    document.getElementById(id).scrollTop = document.getElementById(id).scrollHeight;
}

function runCode() {
    if (isRunning || !codeDetails) return;
    if (!activeFile || !openFiles[activeFile]) {
        appendOutput('warning', 'No file open. Select a file to run.');
        return;
    }

    isRunning = true;
    var runBtn = document.getElementById('runBtn');
    var stopBtn = document.getElementById('stopBtn');
    runBtn.classList.add('running');
    runBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> Running...';
    runBtn.disabled = true;
    stopBtn.classList.add('visible');
    stopBtn.disabled = false;

    var savePromise = openFiles[activeFile].modified
        ? fetch('/api/workspace/file/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
            body: JSON.stringify({ path: activeFile, content: editor.getValue() })
          }).then(function() {
              openFiles[activeFile].content = editor.getValue();
              openFiles[activeFile].modified = false;
              updateTabStatus(activeFile, false);
              updateSaveButton(false);
          })
        : Promise.resolve();

    savePromise.then(function() {
        var stdin = document.getElementById('stdinInput').value;
        switchBottomPanel('output', document.querySelector('[data-panel="output"]'));
        setOutputPanel('outputPanel', '<div class="line line-info">Running ' + activeFile.split('/').pop() + '...</div>');

        return fetch('/api/execution/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
            body: JSON.stringify({ stdin: stdin, file_path: activeFile })
        });
    })
    .then(function(resp) { return resp.json(); })
    .then(function(data) {
        currentJobId = data.job_id || null;
        renderExecutionResult(data);
    })
    .catch(function(err) {
        setOutputPanel('outputPanel', '<div class="line line-error">Execution failed: ' + err.message + '</div>');
    })
    .finally(function() {
        isRunning = false;
        currentJobId = null;
        runBtn.classList.remove('running');
        runBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run';
        runBtn.disabled = false;
        stopBtn.classList.remove('visible');
    });
}

function stopExecution() {
    if (currentJobId) {
        fetch('/api/execution/cancel/' + currentJobId, {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + API.token }
        }).catch(function() {});
    }
    isRunning = false;
    currentJobId = null;
    var runBtn = document.getElementById('runBtn');
    var stopBtn = document.getElementById('stopBtn');
    runBtn.classList.remove('running');
    runBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run';
    runBtn.disabled = false;
    stopBtn.classList.remove('visible');
    appendOutput('warning', 'Execution cancelled.');
}

function renderExecutionResult(data) {
    var statusMap = {
        success: { cls: 'success', label: 'SUCCESS' },
        runtime_error: { cls: 'error', label: 'RUNTIME ERROR' },
        compilation_error: { cls: 'error', label: 'COMPILATION ERROR' },
        timeout: { cls: 'warning', label: 'TIMEOUT' },
        error: { cls: 'error', label: 'ERROR' },
    };
    var s = statusMap[data.status] || statusMap.error;

    var outputPanel = document.getElementById('outputPanel');
    var errorsPanel = document.getElementById('errorsPanel');
    outputPanel.innerHTML = '';
    errorsPanel.innerHTML = '';

    if (data.status === 'compilation_error') {
        var errBadge = document.getElementById('errorBadge');
        errBadge.style.display = 'inline-flex';
        errBadge.textContent = '!';
        errorsPanel.innerHTML = '<div class="line line-header">Compilation Error</div>' +
            '<div class="line line-error">' + escapeHtml(data.stderr || 'Compilation failed') + '</div>' +
            '<div class="line line-divider"></div>' +
            '<div class="line line-meta">Exit code: ' + data.exit_code + ' | Time: ' + data.execution_time + 's | ' + data.language + '</div>';
        switchBottomPanel('errors', document.querySelector('[data-panel="errors"]'));
        return;
    }

    var html = '<div class="line line-header">' + s.label + '</div>';

    if (data.stdout && data.stdout.trim()) {
        html += '<div class="line line-stdout">' + escapeHtml(data.stdout) + '</div>';
    }

    if (data.stderr && data.stderr.trim()) {
        var errBadge2 = document.getElementById('errorBadge');
        errBadge2.style.display = 'inline-flex';
        errBadge2.textContent = '!';
        errorsPanel.innerHTML = '<div class="line line-header">Stderr</div>' +
            '<div class="line line-error">' + escapeHtml(data.stderr) + '</div>';
    } else {
        document.getElementById('errorBadge').style.display = 'none';
    }

    if (!data.stdout || !data.stdout.trim()) {
        if (!data.stderr || !data.stderr.trim()) {
            html += '<div class="line line-info">(No output)</div>';
        }
    }

    html += '<div class="line line-divider"></div>';
    html += '<div class="line line-meta">';
    html += 'Exit code: ' + data.exit_code;
    if (data.execution_time !== undefined) html += ' | Time: ' + data.execution_time + 's';
    if (data.language) html += ' | ' + data.language;
    html += '</div>';

    outputPanel.innerHTML = html;
    outputPanel.scrollTop = outputPanel.scrollHeight;
}

function runTests() {
    if (!codeDetails) return;

    switchBottomPanel('tests', document.querySelector('[data-panel="tests"]'));
    setOutputPanel('testsPanel', '<div class="line line-info">Running tests...</div>');

    fetch('/api/execution/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
        body: JSON.stringify({})
    })
    .then(function(resp) { return resp.json(); })
    .then(function(data) {
        renderTestResult(data);
    })
    .catch(function(err) {
        setOutputPanel('testsPanel', '<div class="line line-error">Test error: ' + err.message + '</div>');
    });
}

function renderTestResult(data) {
    var panel = document.getElementById('testsPanel');

    if (data.configured === false) {
        panel.innerHTML = '<div class="line line-info">' + escapeHtml(data.message || 'No tests configured for this challenge.') + '</div>';
        return;
    }

    var html = '';

    if (data.results && data.results.length > 0) {
        var passed = 0;
        for (var i = 0; i < data.results.length; i++) { if (data.results[i].passed) passed++; }
        var total = data.results.length;
        var allPass = passed === total;

        var badge = document.getElementById('testBadge');
        badge.style.display = 'inline-flex';
        badge.textContent = passed + '/' + total;
        badge.className = 'badge ' + (allPass ? 'success' : 'error');

        html += '<div class="line line-header">' + passed + ' / ' + total + ' TESTS PASSED</div>';
        html += '<div class="line line-divider"></div>';

        for (var j = 0; j < data.results.length; j++) {
            var r = data.results[j];
            var icon = r.passed ? '\u2713' : '\u2717';
            var cls = r.passed ? 'pass' : 'fail';
            html += '<div class="test-row">';
            html += '<span class="test-icon ' + cls + '">' + icon + '</span>';
            html += '<span class="test-name">' + escapeHtml(r.test) + '</span>';
            if (r.time) html += '<span class="test-time">' + r.time + 's</span>';
            html += '</div>';
            if (!r.passed && r.reason) {
                html += '<div class="test-reason">' + escapeHtml(r.reason) + '</div>';
            }
        }

        html += '<div class="line line-divider"></div>';
        html += '<div class="line line-meta">' + escapeHtml(data.message || '') + '</div>';
    } else if (data.stdout || data.stderr) {
        var exitCls = data.exit_code === 0 ? 'success' : 'error';
        var exitLabel = data.exit_code === 0 ? 'ALL TESTS PASSED' : 'TESTS FAILED';
        html += '<div class="line line-header">' + exitLabel + '</div>';
        if (data.stdout) html += '<div class="line line-stdout">' + escapeHtml(data.stdout) + '</div>';
        if (data.stderr) html += '<div class="line line-error">' + escapeHtml(data.stderr) + '</div>';
        html += '<div class="line line-divider"></div>';
        html += '<div class="line line-meta">Exit code: ' + data.exit_code;
        if (data.execution_time) html += ' | Time: ' + data.execution_time + 's';
        html += '</div>';
    } else {
        html += '<div class="line line-info">No test output.</div>';
    }

    panel.innerHTML = html;
    panel.scrollTop = panel.scrollHeight;
}

function submitFinal() {
    document.getElementById('submitModal').classList.add('active');
}

function closeSubmitModal() {
    document.getElementById('submitModal').classList.remove('active');
}

async function confirmSubmit() {
    closeSubmitModal();

    if (activeFile && openFiles[activeFile] && editor) {
        openFiles[activeFile].content = editor.getValue();
        await fetch('/api/workspace/file/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
            body: JSON.stringify({ path: activeFile, content: editor.getValue() })
        });
    }

    var btn = document.getElementById('submitBtn');
    btn.disabled = true;
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Submitting...';

    try {
        var resp = await fetch('/api/submission/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
            body: JSON.stringify({})
        });
        var data = await resp.json();

        if (resp.ok) {
            appendOutput('success', 'Submission evaluated: ' + data.score + '/' + data.total);
            switchBottomPanel('output', document.querySelector('[data-panel="output"]'));
        } else {
            appendOutput('error', 'Submission failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (err) {
        appendOutput('error', 'Submission error: ' + err.message);
    }

    btn.disabled = false;
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Submit';
}

function toggleChallengePanel() {
    var panel = document.getElementById('challengePanel');
    panel.classList.toggle('open');
}

function renderChallengePanel() {
    if (!codeDetails) return;
    var ch = codeDetails;
    var diffColors = { 'easy': '#22c55e', 'medium': '#f59e0b', 'hard': '#ef4444' };
    var diffColor = diffColors[(ch.difficulty || '').toLowerCase()] || 'var(--text-muted)';

    document.getElementById('challengePanelContent').innerHTML =
        '<h2 style="font-size:22px;font-weight:700;margin-bottom:8px;">' + escapeHtml(ch.challenge_name || ch.challenge_code) + '</h2>' +
        '<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">' +
        '<span style="font-size:12px;padding:4px 12px;border-radius:var(--radius-full);background:rgba(137,180,250,.1);color:var(--ide-accent);border:1px solid rgba(137,180,250,.2);font-family:var(--font-mono);">' + escapeHtml(ch.challenge_code) + '</span>' +
        (ch.language ? '<span style="font-size:12px;padding:4px 12px;border-radius:var(--radius-full);background:rgba(166,227,161,.1);color:var(--ide-success);border:1px solid rgba(166,227,161,.2);">' + escapeHtml(ch.language) + '</span>' : '') +
        (ch.difficulty ? '<span style="font-size:12px;padding:4px 12px;border-radius:var(--radius-full);background:rgba(255,255,255,.05);color:' + diffColor + ';border:1px solid ' + diffColor + '30;text-transform:uppercase;font-weight:600;">' + escapeHtml(ch.difficulty) + '</span>' : '') +
        '</div>' +
        (ch.challenge_description ? '<div style="color:var(--text-secondary);font-size:14px;line-height:1.7;white-space:pre-wrap;">' + escapeHtml(ch.challenge_description) + '</div>' : '<p style="color:var(--text-muted);font-size:13px;">No description provided.</p>');
}

function startTimer() {
    function update() {
        if (!eventEnd) return;
        var now = new Date(Date.now() + serverTimeOffset);
        var diff = eventEnd - now;
        if (diff <= 0) {
            document.getElementById('timerDisplay').textContent = '00:00:00';
            document.getElementById('timerDisplay').style.color = 'var(--ide-error)';
            if (!autoSubmitted) {
                autoSubmitted = true;
                appendOutput('warning', 'Time expired. Auto-submitting...');
                performAutoSubmit();
            }
            return;
        }
        var h = Math.floor(diff / 3600000);
        var m = Math.floor((diff % 3600000) / 60000);
        var s = Math.floor((diff % 60000) / 1000);
        document.getElementById('timerDisplay').textContent =
            String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        if (diff < 600000) {
            document.getElementById('timerDisplay').style.color = 'var(--ide-error)';
        }
    }
    update();
    setInterval(update, 1000);
}

async function performAutoSubmit() {
    if (autoSubmitted && !confirm('Event has ended. Auto-submit your work?')) return;
    autoSubmitted = true;
    try {
        for (var path in openFiles) {
            if (openFiles[path].modified) {
                await fetch('/api/workspace/file/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
                    body: JSON.stringify({ path: path, content: openFiles[path].content })
                });
            }
        }
        var resp = await fetch('/api/submission/auto-submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
            body: JSON.stringify({})
        });
        var data = await resp.json();
        appendOutput('info', 'Auto-submit: ' + data.message);
    } catch (err) {
        appendOutput('error', 'Auto-submit error: ' + err.message);
    }
}
