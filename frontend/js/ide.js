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

if (!API.isLoggedIn() || API.role !== 'participant') {
    window.location.href = '/login';
}

function logout() {
    API.clearAuth();
    window.location.href = '/login';
}

require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' } });
require(['vs/editor/editor.main'], function () {
    monacoLoaded = true;
    initEditor();
    loadIDE();
});

function initEditor() {
    const container = document.getElementById('editorContainer');
    container.innerHTML = '';
    editor = monaco.editor.create(container, {
        value: '',
        language: 'python',
        theme: 'vs-dark',
        automaticLayout: true,
        fontSize: 14,
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        wordWrap: 'on',
        lineNumbers: 'on',
        renderWhitespace: 'selection',
        tabSize: 4,
        insertSpaces: true,
        cursorBlinking: 'smooth',
        smoothScrolling: true,
        bracketPairColorization: { enabled: true },
        padding: { top: 8 }
    });
    editor.onDidChangeModelContent(() => {
        if (activeFile && openFiles[activeFile]) {
            openFiles[activeFile].modified = true;
            updateTabStatus(activeFile, true);
        }
    });
}

function getLanguage(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const map = {
        'py': 'python', 'js': 'javascript', 'ts': 'typescript', 'jsx': 'javascript',
        'tsx': 'typescript', 'html': 'html', 'htm': 'html', 'css': 'css',
        'json': 'json', 'md': 'markdown', 'yml': 'yaml', 'yaml': 'yaml',
        'xml': 'xml', 'java': 'java', 'c': 'c', 'cpp': 'cpp', 'h': 'c',
        'cs': 'csharp', 'go': 'go', 'rs': 'rust', 'rb': 'ruby',
        'php': 'php', 'sh': 'shell', 'bash': 'shell', 'sql': 'sql',
        'txt': 'plaintext', 'env': 'plaintext', 'gitignore': 'plaintext',
        'toml': 'plaintext', 'ini': 'plaintext', 'cfg': 'plaintext',
        'dockerfile': 'dockerfile', 'makefile': 'makefile'
    };
    return map[ext] || 'plaintext';
}

async function loadIDE() {
    try {
        const resp = await API.get('/api/workspace/code-details');
        if (!resp) return;
        codeDetails = await resp.json();
        document.getElementById('ideTeamName').textContent = codeDetails.team_name;
        document.getElementById('ideChallenge').textContent = codeDetails.challenge_name || codeDetails.challenge_code;

        try {
            const timeResp = await fetch('/api/server-time');
            if (timeResp.ok) {
                const timeData = await timeResp.json();
                serverTimeOffset = new Date(timeData.server_time).getTime() - Date.now();
            }
        } catch (e) {}

        if (codeDetails.event_start && codeDetails.event_end) {
            eventEnd = new Date(codeDetails.event_end);
            startTimer();
        }

        await loadFileTree();
        checkEventStatus();
    } catch (err) {
        console.error('IDE init error:', err);
    }
}

async function checkEventStatus() {
    try {
        const resp = await API.get('/api/participant/event-countdown');
        if (!resp) return;
        const data = await resp.json();
        if (data.status === 'COMPLETED' && !autoSubmitted) {
            await performAutoSubmit();
        }
    } catch (e) {}
}

async function loadFileTree() {
    try {
        const resp = await API.get('/api/workspace/tree');
        if (!resp) return;
        const data = await resp.json();
        fileTree = data.tree;
        renderFileTree();
    } catch (err) {
        console.error('File tree error:', err);
        document.getElementById('fileTree').innerHTML = '<div class="empty-state"><p>Could not load files</p></div>';
    }
}

function renderFileTree() {
    const container = document.getElementById('fileTree');
    container.innerHTML = '';
    renderNodes(fileTree, container, 0);
}

function renderNodes(nodes, container, depth) {
    for (const node of nodes) {
        const item = document.createElement('div');
        item.className = 'ide-file-item' + (node.type === 'directory' ? ' directory' : '') +
            (activeFile === node.path ? ' active' : '');

        const indent = document.createElement('span');
        indent.className = 'indent';
        indent.style.width = (depth * 16) + 'px';
        item.appendChild(indent);

        const icon = document.createElement('span');
        icon.className = 'icon';
        if (node.type === 'directory') {
            icon.innerHTML = node._expanded
                ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="9" y1="14" x2="15" y2="14"/></svg>'
                : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
        } else {
            const ext = node.name.split('.').pop().toLowerCase();
            const fileIcons = {
                'py': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"/></svg>',
                'js': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
                'ts': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
                'html': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
                'css': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3c-1.2 0-2.4.6-3 1.7A3.6 3.6 0 0 0 4.6 9c-1 .6-1.7 1.8-1.7 3s.7 2.4 1.7 3c-.3 1.2 0 2.5 1 3.4.8.8 2.1 1.3 3.4 1 .6 1 1.8 1.7 3 1.7s2.4-.6 3-1.7c1.2.3 2.5 0 3.4-1 .8-.8 1.3-2 1-3.4 1-.6 1.7-1.8 1.7-3s-.7-2.4-1.7-3c.3-1.2 0-2.5-1-3.4A3.7 3.7 0 0 0 15 4.6 3.6 3.6 0 0 0 12 3z"/></svg>',
                'json': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
                'md': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
            };
            icon.innerHTML = fileIcons[ext] || '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
        }
        item.appendChild(icon);

        const name = document.createElement('span');
        name.textContent = node.name;
        item.appendChild(name);

        item.addEventListener('click', () => {
            if (node.type === 'directory') {
                node._expanded = !node._expanded;
                renderFileTree();
            } else {
                openFile(node.path);
            }
        });

        container.appendChild(item);

        if (node.type === 'directory' && node._expanded && node.children) {
            renderNodes(node.children, container, depth + 1);
        }
    }
}

async function openFile(path) {
    if (openFiles[path]) {
        switchToFile(path);
        return;
    }

    try {
        const resp = await API.get(`/api/workspace/file?path=${encodeURIComponent(path)}`);
        if (!resp) return;
        const data = await resp.json();

        openFiles[path] = {
            content: data.content,
            originalContent: data.content,
            modified: false,
            language: getLanguage(path.split('/').pop())
        };

        createTab(path);
        switchToFile(path);
    } catch (err) {
        console.error('File open error:', err);
    }
}

function createTab(path) {
    const tabs = document.getElementById('editorTabs');
    const filename = path.split('/').pop();

    const tab = document.createElement('div');
    tab.className = 'ide-tab active';
    tab.id = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');

    const tabName = document.createElement('span');
    tabName.className = 'tab-name';
    tabName.textContent = filename;

    const statusDot = document.createElement('span');
    statusDot.className = 'status-indicator';

    const closeBtn = document.createElement('span');
    closeBtn.className = 'close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = function(e) {
        e.stopPropagation();
        closeFile(path);
    };

    tab.appendChild(tabName);
    tab.appendChild(statusDot);
    tab.appendChild(closeBtn);
    tab.addEventListener('click', () => switchToFile(path));
    tabs.appendChild(tab);
}

function switchToFile(path) {
    if (!openFiles[path]) return;

    activeFile = path;
    const file = openFiles[path];

    if (editor && monacoLoaded) {
        if (currentModel) {
            currentModel.dispose();
        }
        currentModel = monaco.editor.createModel(file.content, file.language);
        editor.setModel(currentModel);
    }

    document.querySelectorAll('.ide-tab').forEach(t => t.classList.remove('active'));
    const tabId = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');
    const tab = document.getElementById(tabId);
    if (tab) tab.classList.add('active');

    renderFileTree();
}

function closeFile(path) {
    if (openFiles[path] && openFiles[path].modified) {
        if (!confirm('Unsaved changes in ' + path.split('/').pop() + '. Close anyway?')) return;
    }

    delete openFiles[path];
    const tabId = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');
    const tab = document.getElementById(tabId);
    if (tab) tab.remove();

    if (activeFile === path) {
        const remaining = Object.keys(openFiles);
        if (remaining.length > 0) {
            switchToFile(remaining[remaining.length - 1]);
        } else {
            activeFile = null;
            if (editor && monacoLoaded) {
                editor.setModel(null);
            }
        }
    }
}

function saveFile() {
    if (!activeFile || !openFiles[activeFile]) return;

    const content = editor.getValue();

    const btn = document.getElementById('saveBtn');
    btn.innerHTML = '<span class="loading-spinner"></span>';
    btn.disabled = true;

    fetch('/api/workspace/file/save', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + API.token
        },
        body: JSON.stringify({ path: activeFile, content: content })
    })
    .then(r => r.json())
    .then(data => {
        if (data.message) {
            openFiles[activeFile].content = content;
            openFiles[activeFile].modified = false;
            btn.innerHTML = 'Saved \u2713';
            btn.style.background = 'var(--success)';
            setTimeout(() => {
                btn.innerHTML = 'Save';
                btn.style.background = '';
            }, 2000);
            updateTabStatus(activeFile, false);
            appendTerminal('File saved: ' + activeFile + '\n');
        }
    })
    .catch(err => {
        btn.innerHTML = 'Save';
        btn.style.background = '';
        appendTerminal('Error saving file: ' + err.message + '\n');
    })
    .finally(() => {
        btn.disabled = false;
    });
}

function updateTabStatus(path, modified) {
    const tabId = 'tab_' + path.replace(/[^a-zA-Z0-9]/g, '_');
    const tab = document.getElementById(tabId);
    if (!tab) return;
    const indicator = tab.querySelector('.status-indicator');
    if (indicator) {
        indicator.textContent = modified ? '●' : '';
        indicator.style.color = modified ? 'var(--warning)' : '';
    }
    tab.classList.toggle('modified', modified);
}

function switchPanel(panel, el) {
    document.querySelectorAll('.ide-panel-tab').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');
    else if (event && event.target) event.target.classList.add('active');

    document.getElementById('terminalOutput').style.display = panel === 'terminal' ? 'block' : 'none';
    document.getElementById('generalOutput').style.display = panel === 'output' ? 'block' : 'none';
    document.getElementById('testOutput').style.display = panel === 'tests' ? 'block' : 'none';
}

function appendTerminal(text) {
    const el = document.getElementById('terminalOutput');
    el.textContent += text;
    el.scrollTop = el.scrollHeight;
}

function setOutput(id, text) {
    const el = document.getElementById(id);
    el.textContent = text;
}

async function runCode() {
    if (!codeDetails) return;
    const btn = document.getElementById('runBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span>';

    appendTerminal('\n$ Running code...\n');

    try {
        const resp = await fetch('/api/execution/run', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + API.token
            },
            body: JSON.stringify({})
        });
        const data = await resp.json();

        if (data.stdout) appendTerminal(data.stdout);
        if (data.stderr) appendTerminal('STDERR:\n' + data.stderr);
        appendTerminal(`\nExit Code: ${data.exit_code}\nCommand: ${data.command}\n`);
    } catch (err) {
        appendTerminal('Error: ' + err.message + '\n');
    }

    btn.disabled = false;
    btn.innerHTML = 'Run';
}

async function runTests() {
    if (!codeDetails) return;
    const btn = document.getElementById('testBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span>';

    switchPanel('tests');
    document.querySelectorAll('.ide-panel-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.ide-panel-tab')[2].classList.add('active');
    setOutput('testOutput', 'Running tests...\n');

    try {
        const resp = await fetch('/api/execution/test', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + API.token
            },
            body: JSON.stringify({})
        });
        const data = await resp.json();

        let output = '';
        if (data.stdout) output += data.stdout;
        if (data.stderr) output += '\nSTDERR:\n' + data.stderr;
        output += `\nExit Code: ${data.exit_code}\n`;
        setOutput('testOutput', output);
    } catch (err) {
        setOutput('testOutput', 'Error: ' + err.message);
    }

    btn.disabled = false;
    btn.innerHTML = 'Test';
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
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + API.token
            },
            body: JSON.stringify({ path: activeFile, content: editor.getValue() })
        });
    }

    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span>';

    appendTerminal('\nSubmitting final...\n');

    try {
        const resp = await fetch('/api/submission/submit', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + API.token
            },
            body: JSON.stringify({})
        });
        const data = await resp.json();

        if (resp.ok) {
            appendTerminal(`\n=== SUBMISSION EVALUATED ===\n`);
            appendTerminal(`Score: ${data.score}/${data.total}\n\n`);
            for (const r of data.results) {
                const icon = r.passed ? '✓ PASS' : '✗ FAIL';
                appendTerminal(`${r.test}: ${icon}\n`);
                if (!r.passed && r.reason) appendTerminal(`  Reason: ${r.reason}\n`);
            }
            appendTerminal(`\nFinal Score: ${data.score}/${data.total}\n`);
        } else {
            appendTerminal('Error: ' + (data.detail || 'Submission failed') + '\n');
        }
    } catch (err) {
        appendTerminal('Error: ' + err.message + '\n');
    }

    btn.disabled = false;
    btn.innerHTML = 'Submit Final';
}

function startTimer() {
    function update() {
        if (!eventEnd) return;
        const now = new Date(Date.now() + serverTimeOffset);
        const diff = eventEnd - now;
        if (diff <= 0) {
            document.getElementById('timerDisplay').textContent = '00:00:00';
            document.getElementById('timerDisplay').style.color = 'var(--danger)';
            if (!autoSubmitted) {
                autoSubmitted = true;
                appendTerminal('\n--- Time expired. Auto-submitting... ---\n');
                performAutoSubmit();
            }
            return;
        }
        const h = Math.floor(diff / 3600000);
        const m = Math.floor((diff % 3600000) / 60000);
        const s = Math.floor((diff % 60000) / 1000);
        document.getElementById('timerDisplay').textContent =
            String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    }
    update();
    setInterval(update, 1000);
}

async function performAutoSubmit() {
    if (autoSubmitted && !confirm('Event has ended. Auto-submit your work?')) return;
    autoSubmitted = true;
    try {
        for (const path of Object.keys(openFiles)) {
            if (openFiles[path].modified && editor && activeFile === path) {
                await fetch('/api/workspace/file/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
                    body: JSON.stringify({ path, content: editor.getValue() })
                });
            }
        }
        const resp = await fetch('/api/submission/auto-submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token },
            body: JSON.stringify({})
        });
        const data = await resp.json();
        appendTerminal('Auto-submit result: ' + data.message + '\n');
    } catch (err) {
        appendTerminal('Auto-submit error: ' + err.message + '\n');
    }
}
