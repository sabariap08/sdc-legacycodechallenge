const API = {
    token: localStorage.getItem('auth_token') || '',
    role: localStorage.getItem('auth_role') || '',

    setAuth(token, role) {
        this.token = token;
        this.role = role;
        localStorage.setItem('auth_token', token);
        localStorage.setItem('auth_role', role);
    },

    clearAuth() {
        this.token = '';
        this.role = '';
        localStorage.removeItem('auth_token');
        localStorage.removeItem('auth_role');
    },

    isLoggedIn() {
        return !!this.token;
    },

    getLoginPath() {
        if (this.role === 'admin') return '/admin/dashboard';
        if (this.role === 'participant') return '/participant/dashboard';
        return '/login';
    },

    async request(url, options = {}) {
        const headers = {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const response = await fetch(url, { ...options, headers });
        if (response.status === 401) {
            this.clearAuth();
            window.location.href = '/login';
            return null;
        }
        return response;
    },

    async get(url) { return this.request(url, { method: 'GET' }); },
    async post(url, data) { return this.request(url, { method: 'POST', body: JSON.stringify(data) }); },
    async put(url, data) { return this.request(url, { method: 'PUT', body: JSON.stringify(data) }); },
    async del(url) { return this.request(url, { method: 'DELETE' }); }
};

const ICONS = {
    dashboard: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    teams: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    add: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    archive: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
    target: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
    fileText: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>',
    trophy: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>',
    clock: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    clipboard: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>',
    logout: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>',
    sun: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>',
    moon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    users: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    user: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    code: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    terminal: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
    alertCircle: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    checkCircle: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    xCircle: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    play: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
    save: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
    send: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
    ban: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>',
    inbox: '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.3"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
    randomize: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>',
    shield: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
    shieldOff: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.69 14a6.9 6.9 0 0 0 .31-2V5l-8-3-3.16 1.18"/><path d="M4.73 4.73L4 5v7c0 6 8 10 8 10a20.29 20.29 0 0 0 5.62-4.38"/><line x1="1" y1="1" x2="23" y2="23"/></svg>',
    unlock: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>',
    folder: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    file: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
    arrowLeft: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
    refresh: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
};

function showError(elementId, message) {
    const el = document.getElementById(elementId);
    if (el) { el.textContent = message; el.classList.add('show'); }
}

function hideError(elementId) {
    const el = document.getElementById(elementId);
    if (el) el.classList.remove('show');
}

function showSuccess(elementId, message) {
    const el = document.getElementById(elementId);
    if (el) { el.innerHTML = message; el.classList.add('show'); }
}

function hideSuccess(elementId) {
    const el = document.getElementById(elementId);
    if (el) el.classList.remove('show');
}

function formatDate(dateStr) {
    if (!dateStr) return 'N/A';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function getStatusBadge(status) {
    const map = {
        'READY': 'badge-success', 'FAILED': 'badge-danger', 'REGISTERED': 'badge-info',
        'CHECKED-IN': 'badge-warning', 'STARTED': 'badge-success', 'COMPLETED': 'badge-success',
        'DRAFT': 'badge-neutral', 'REGISTRATION_OPEN': 'badge-success', 'REGISTRATION_CLOSED': 'badge-danger',
        'ALLOCATION_GENERATED': 'badge-info', 'ALLOCATION_CONFIRMED': 'badge-info',
        'ALLOCATIONS_RELEASED': 'badge-success', 'EVENT_STARTED': 'badge-success', 'EVENT_ENDED': 'badge-danger',
        'UPCOMING': 'badge-warning', 'ONGOING': 'badge-success',
    };
    return `<span class="badge ${map[status] || 'badge-neutral'}">${escapeHtml(status)}</span>`;
}

function initTheme() {
    const saved = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeIcon(saved);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    updateThemeIcon(next);
}

function updateThemeIcon(theme) {
    const btns = document.querySelectorAll('.theme-toggle');
    const icon = theme === 'dark' ? ICONS.sun : ICONS.moon;
    btns.forEach(btn => {
        btn.innerHTML = icon;
        btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    });
}

initTheme();

function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    if (sidebar) sidebar.classList.toggle('open');
    if (overlay) overlay.classList.toggle('active');
}

function closeSidebar() {
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('active');
}

function getAdminSidebar(currentPage) {
    const items = [
        { href: '/admin/dashboard', icon: 'dashboard', label: 'Dashboard', key: 'dashboard' },
        { href: '/admin/teams', icon: 'teams', label: 'Teams', key: 'teams' },
        { href: '/admin/users', icon: 'users', label: 'User Management', key: 'users' },
        { href: '/admin/challenges', icon: 'archive', label: 'Repositories', key: 'challenges' },
        { href: '/admin/submissions', icon: 'fileText', label: 'Submissions', key: 'submissions' },
        { href: '/admin/leaderboard', icon: 'trophy', label: 'Leaderboard', key: 'leaderboard' },
        { href: '/admin/event', icon: 'clock', label: 'Event Control', key: 'event' },
        { href: '/admin/audit', icon: 'clipboard', label: 'Audit Logs', key: 'audit' },
    ];
    let html = '';
    for (const item of items) {
        const active = item.key === currentPage ? ' active' : '';
        html += `<a href="${item.href}" class="${active}" onclick="closeSidebar()"><span class="nav-icon">${ICONS[item.icon]}</span>${item.label}</a>`;
    }
    return html;
}

function renderAdminLayout(currentPage, pageTitle) {
    const theme = localStorage.getItem('theme') || 'dark';
    const themeIcon = theme === 'dark' ? ICONS.sun : ICONS.moon;
    return `
    <div class="sidebar-overlay" onclick="closeSidebar()"></div>
    <div class="dashboard-layout">
        <aside class="sidebar">
            <div class="sidebar-header">
                <div class="brand">
                    <div class="brand-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                    </div>
                    <div>
                        <div class="brand-text">Legacy Code Rescue</div>
                        <div class="brand-sub">Admin Portal</div>
                    </div>
                </div>
            </div>
            <nav>${getAdminSidebar(currentPage)}</nav>
            <div class="sidebar-footer">
                <a href="#" class="nav-link sidebar-logout" onclick="event.preventDefault(); logout();">
                    <span class="nav-icon">${ICONS.logout}</span>Logout
                </a>
            </div>
        </aside>
        <main class="main-content">
            <div class="topbar">
                <div class="topbar-left">
                    <button class="hamburger-btn" onclick="toggleSidebar()" aria-label="Toggle navigation">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
                    </button>
                    <span class="page-title">${pageTitle}</span>
                </div>
                <div class="topbar-right">
                    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">${themeIcon}</button>
                    <div class="user-badge">
                        <span class="user-avatar">A</span>
                        <span>admin</span>
                    </div>
                </div>
            </div>
            <div class="page-content" id="pageContent">
                <div class="flex-center" style="padding:48px;"><span class="loading-spinner"></span></div>
            </div>
        </main>
    </div>`;
}

function renderParticipantLayout(currentPage, pageTitle) {
    const theme = localStorage.getItem('theme') || 'dark';
    const themeIcon = theme === 'dark' ? ICONS.sun : ICONS.moon;
    return `
    <div class="sidebar-overlay" onclick="closeSidebar()"></div>
    <div class="dashboard-layout">
        <aside class="sidebar">
            <div class="sidebar-header">
                <div class="brand">
                    <div class="brand-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                    </div>
                    <div>
                        <div class="brand-text">Legacy Code Rescue</div>
                        <div class="brand-sub">Team Portal</div>
                    </div>
                </div>
            </div>
            <nav>
                <a href="/participant/dashboard" class="${currentPage === 'dashboard' ? 'active' : ''}" onclick="closeSidebar()"><span class="nav-icon">${ICONS.dashboard}</span>Dashboard</a>
                <a href="/participant/ide" id="ideLink" style="display:none;" class="${currentPage === 'ide' ? 'active' : ''}" onclick="closeSidebar()"><span class="nav-icon">${ICONS.code}</span>IDE</a>
            </nav>
            <div class="sidebar-footer">
                <a href="#" class="nav-link sidebar-logout" onclick="event.preventDefault(); logout();">
                    <span class="nav-icon">${ICONS.logout}</span>Logout
                </a>
            </div>
        </aside>
        <main class="main-content">
            <div class="topbar">
                <div class="topbar-left">
                    <button class="hamburger-btn" onclick="toggleSidebar()" aria-label="Toggle navigation">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
                    </button>
                    <span class="page-title">${pageTitle}</span>
                </div>
                <div class="topbar-right">
                    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">${themeIcon}</button>
                    <div class="user-badge" id="userBadge">
                        <span class="user-avatar">T</span>
                        <span id="teamBadge">Team</span>
                    </div>
                </div>
            </div>
            <div class="page-content" id="pageContent">
                <div class="flex-center" style="padding:48px;"><span class="loading-spinner"></span></div>
            </div>
        </main>
    </div>`;
}
