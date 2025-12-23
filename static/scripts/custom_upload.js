// API Configuration
const API_BASE = '/custom_upload';

// State Management
let currentProject = '';
let parsersList = [];
let pphrasesList = [];
let deleteTarget = null;
let availableProjects = [];
let availableVerbs = [];
let verbAssignments = {}; // Track which parsers are assigned to which verbs

// DOM Elements
const elements = {
    projectSelect: document.getElementById('projectSelect'),
    uploadForm: document.getElementById('uploadForm'),
    fileInput: document.getElementById('fileInput'),
    fileName: document.getElementById('fileName'),
    parserName: document.getElementById('parserName'),
    parserKind: document.getElementById('parserKind'),
    verbSelect: document.getElementById('verbSelect'),
    overwrite: document.getElementById('overwrite'),
    parserList: document.getElementById('parserList'),
    refreshBtn: document.getElementById('refreshBtn'),
    assignForm: document.getElementById('assignForm'),
    assignParser: document.getElementById('assignParser'),
    assignVerb: document.getElementById('assignVerb'),
    deleteModal: document.getElementById('deleteModal'),
    unlinkOnly: document.getElementById('unlinkOnly'),
    confirmDelete: document.getElementById('confirmDelete'),
    cancelDelete: document.getElementById('cancelDelete'),
    toastContainer: document.getElementById('toastContainer')
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
  initializeEventListeners();

  // Load and render projects
  await loadProjects();

  // Prefer ?project= from URL, else default to first project
  const url = new URL(location.href);
  const preset = url.searchParams.get('project');
  currentProject = (preset && availableProjects.includes(preset))
    ? preset
    : (availableProjects[0] || '');

  if (currentProject) {
    elements.projectSelect.value = currentProject;
    elements.projectSelect.dispatchEvent(new Event('change'));
  } else {
    renderEmptyState();
  }
});

// Event Listeners
function initializeEventListeners() {
    // Project selection change
    elements.projectSelect?.addEventListener('change', async (e) => {
        currentProject = e.target.value || '';
        if (!currentProject) {
            availableVerbs = [];
            parsersList = [];
            verbAssignments = {};
            renderVerbSelects();
            renderEmptyState();
            return;
        }
        await loadVerbs();
        await loadParsers();
    });

    // File input change
    elements.fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            elements.fileName.textContent = file.name;
            elements.fileName.classList.add('has-file');
            if (!elements.parserName.value) {
                elements.parserName.placeholder = file.name.replace('.py', '');
            }
        } else {
            elements.fileName.textContent = 'No file selected';
            elements.fileName.classList.remove('has-file');
        }
    });

    // Parser kind change - show/hide verb assignment
    elements.parserKind.addEventListener('change', () => {
        updateVerbAssignmentVisibility();
    });

    // Upload form submit
    elements.uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        await uploadParser();
    });

    // Refresh button
    elements.refreshBtn.addEventListener('click', async () => {
        await loadParsers();
    });

    // Assign form submit
    elements.assignForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        await assignParser();
    });

    // Delete modal buttons
    elements.confirmDelete.addEventListener('click', async () => {
        if (deleteTarget) {
            await deleteParser(deleteTarget); // Always unlink, no checkbox
            closeDeleteModal();
        }
    });

    elements.cancelDelete.addEventListener('click', closeDeleteModal);

    // Click outside modal to close
    elements.deleteModal.addEventListener('click', (e) => {
        if (e.target === elements.deleteModal) {
            closeDeleteModal();
        }
    });
}

// API Functions
async function loadProjects() {
    try {
        const response = await fetch(`${API_BASE}/projects`);
        if (!response.ok) throw new Error(`Failed to load projects: ${response.statusText}`);

        availableProjects = await response.json();
        renderProjectSelect();

        await loadVerbs();
        updateVerbAssignmentVisibility(); // Set initial state
    } catch (error) {
        console.error('Error loading projects:', error);
        showToast(`Error loading projects: ${error.message}`, 'error');
    }
}

async function loadVerbs() {
    if (!currentProject) return;
    try {
        const response = await fetch(`${API_BASE}/${encodeURIComponent(currentProject)}/verbs`);
        if (!response.ok) {
            console.warn(`Failed to load verbs: ${response.statusText}`);
            availableVerbs = [];
            verbAssignments = {};
        } else {
            const data = await response.json();
            availableVerbs = data.verbs || [];
            await loadVerbAssignments();
        }
        renderVerbSelects();
    } catch (error) {
        console.warn('Error loading verbs:', error);
        availableVerbs = [];
        verbAssignments = {};
        renderVerbSelects();
    }
}

async function loadVerbAssignments() {
    if (!currentProject) return;
    try {
        const response = await fetch(`${API_BASE}/${encodeURIComponent(currentProject)}/assignments`);
        if (response.ok) {
            const data = await response.json();
            verbAssignments = data.assignments || {};
        } else {
            console.warn('Could not load verb assignments');
            verbAssignments = {};
        }
    } catch (error) {
        console.warn('Error loading verb assignments:', error);
        verbAssignments = {};
    }
}

async function loadParsers() {
    if (!currentProject) return;
    try {
        showLoading();
        const response = await fetch(`${API_BASE}/${encodeURIComponent(currentProject)}/list`);
        if (!response.ok) throw new Error(`Failed to load parsers: ${response.statusText}`);

        const data = await response.json();
        parsersList = data.parsers || [];
        pphrasesList = data.pphrases || [];

        const combinedList = [
            ...parsersList.map(p => ({ ...p, kind: "parser" })),
            ...pphrasesList.map(p => ({ ...p, kind: "pphrase" }))
        ];

        if (Object.keys(verbAssignments).length === 0) {
            await loadVerbAssignments();
        }

        renderParserList(combinedList);
        renderParserDropdown();

        if (combinedList.length > 0) {
            showToast('Custom scripts loaded successfully', 'success');
        }
    } catch (error) {
        showToast(`Error loading custom scripts: ${error.message}`, 'error');
        renderEmptyState();
    }
}

async function uploadParser() {
    if (!currentProject) {
        showToast('Please select a project', 'warning');
        return;
    }

    const file = elements.fileInput.files[0];
    if (!file) {
        showToast('Please select a file', 'warning');
        return;
    }

    const parserKind = elements.parserKind.value;
    const verbValue = elements.verbSelect.value;

    // Validation based on parser type
    if (parserKind === 'parser' && !verbValue) {
        showToast('Please select a verb for parser type', 'warning');
        return;
    }

    const formData = new FormData();
    // project now in PATH, not the form body
    formData.append('file', file);
    formData.append('kind', parserKind);
    formData.append('overwrite', elements.overwrite.checked);

    if (!elements.parserName.value.trim()) {
        showToast('Please enter a parser name', 'warning');
        return;
    }
    formData.append('explicit_name', elements.parserName.value.trim());

    if (verbValue && parserKind === 'parser') {
        formData.append('verb', verbValue);
    }

    try {
        const response = await fetch(`${API_BASE}/${encodeURIComponent(currentProject)}/upload_parser`, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Upload failed');

        showToast('Parser uploaded successfully!', 'success');
        resetUploadForm();
        await loadVerbs();
        await loadParsers();
    } catch (error) {
        showToast(`Upload failed: ${error.message}`, 'error');
    }
}

async function assignParser() {
    if (!currentProject) {
        showToast('Please select a project', 'warning');
        return;
    }
    const parserName = elements.assignParser.value;
    const verbName = elements.assignVerb.value;

    if (!parserName || !verbName) {
        showToast('Please select both parser and verb', 'warning');
        return;
    }

    try {
        const params = new URLSearchParams({
            verb: verbName,
            parser_name: parserName
        });

        const response = await fetch(`${API_BASE}/${encodeURIComponent(currentProject)}/assign?${params.toString()}`, {
            method: 'POST'
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Assignment failed');

        showToast(`Parser "${parserName}" assigned to verb "${verbName}"`, 'success');
        elements.assignForm.reset();
        await loadVerbs();
        await loadParsers();
    } catch (error) {
        showToast(`Assignment failed: ${error.message}`, 'error');
    }
}

async function unassignParser(parserName, verbName) {
    if (!currentProject) {
        showToast('Please select a project', 'warning');
        return;
    }
    try {
        const params = new URLSearchParams({
            parser_name: parserName,
            verb: verbName
        });

        const response = await fetch(`${API_BASE}/${encodeURIComponent(currentProject)}/unassign?${params.toString()}`, {
            method: 'DELETE'
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unassignment failed');

        showToast(`Parser "${parserName}" unassigned from verb "${verbName}"`, 'success');
        await loadVerbs();
        await loadParsers();
    } catch (error) {
        showToast(`Unassignment failed: ${error.message}`, 'error');
    }
}

async function deleteParser(parserName) {
    if (!currentProject) {
        showToast('Please select a project', 'warning');
        return;
    }
    try {
        // Backend only ever unlinks from verbs, no file deletion
        const response = await fetch(
            `${API_BASE}/${encodeURIComponent(currentProject)}/${encodeURIComponent(parserName)}`,
            { method: 'DELETE' }
        );

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Delete failed');

        showToast(`Parser "${parserName}" unlinked successfully`, 'success');
        await loadVerbs();
        await loadParsers();
    } catch (error) {
        showToast(`Delete failed: ${error.message}`, 'error');
    }
}

// UI Rendering Functions
function renderProjectSelect() {
    if (!elements.projectSelect) return;

    let html = '<option value="">Select project</option>';
    availableProjects.forEach(project => {
        html += `<option value="${escapeHtml(project)}">${escapeHtml(project)}</option>`;
    });

    elements.projectSelect.innerHTML = html;
}

function renderVerbSelects() {
    // Render verb dropdown for upload form
    if (elements.verbSelect) {
        const parserKind = elements.parserKind.value;
        let html = '';

        if (parserKind === 'parser') {
            html = '<option value="">Select a verb *</option>';
            availableVerbs.forEach(verb => {
                html += `<option value="${escapeHtml(verb)}">${escapeHtml(verb)}</option>`;
            });
        } else {
            html = '<option value="">Not applicable for prepositional phrases</option>';
        }

        elements.verbSelect.innerHTML = html;
        elements.verbSelect.disabled = (parserKind !== 'parser');
    }

    // Render verb dropdown for assign form
    if (elements.assignVerb) {
        let html = '<option value="">Select a verb</option>';
        availableVerbs.forEach(verb => {
            html += `<option value="${escapeHtml(verb)}">${escapeHtml(verb)}</option>`;
        });
        elements.assignVerb.innerHTML = html;
    }
}

function renderParserDropdown() {
    if (elements.assignParser) {
        let html = '<option value="">Select a parser</option>';
        parsersList.forEach(parser => {
            html += `<option value="${escapeHtml(parser.name)}">${escapeHtml(parser.name)}</option>`;
        });
        elements.assignParser.innerHTML = html;
    }
}

function updateVerbAssignmentVisibility() {
    const parserKind = elements.parserKind.value;
    const verbContainer = elements.verbSelect.parentElement;

    if (parserKind === 'parser') {
        verbContainer.style.display = 'block';
        elements.verbSelect.required = true;
    } else {
        verbContainer.style.display = 'none';
        elements.verbSelect.required = false;
        elements.verbSelect.value = '';
    }

    renderVerbSelects();
}

function renderParserList(list = parsersList) {
    if (list.length === 0) {
        renderEmptyState();
        return;
    }

    let html = '';
    list.forEach(parser => {
        const fileList = (parser.files && parser.files.length > 0)
            ? parser.files.map(f => escapeHtml(f)).join(', ')
            : 'No .py file found';

        // Find which verbs this parser is assigned to
        const assignedVerbs = [];
        for (const [verbName, parsers] of Object.entries(verbAssignments)) {
            if (parsers.includes(parser.name)) {
                assignedVerbs.push(verbName);
            }
        }

        const isParser = (parser.kind === 'parser');

        const assignmentsHtml = isParser
        ? (assignedVerbs.length > 0
            ? `
                <div class="parser-assignments">
                <span class="assignment-label">Assigned to:</span>
                ${assignedVerbs.map(verb => `
                    <span class="verb-tag">
                    ${escapeHtml(verb)}
                    <button class="unassign-btn"
                            onclick="unassignParser('${escapeHtml(parser.name)}','${escapeHtml(verb)}')"
                            title="Unassign from ${escapeHtml(verb)}">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                    </span>
                `).join('')}
                </div>`
            : `<div class="parser-assignments"><span class="no-assignments">Not assigned to any verbs</span></div>`)
        : `<div class="parser-assignments"><span class="no-assignments">Prepositional phrase (no verb assignment)</span></div>`;

        const actionsHtml = isParser
        ? `
            <div class="parser-actions">
            <button class="btn btn-sm btn-secondary" onclick="quickAssign('${escapeHtml(parser.name)}')">
                <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
                </svg>
                Assign
            </button>
            <button class="btn btn-sm btn-danger" onclick="showDeleteModal('${escapeHtml(parser.name)}')">
                <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
                Unassign All
            </button>
            </div>`
        : ``;

        html += `
        <div class="parser-item">
            <div class="parser-info">
            <div class="parser-name">
                ${escapeHtml(parser.name)}${isParser ? '' : ' <span class="verb-tag">pphrase</span>'}
            </div>
            <div class="parser-details">
                <span class="parser-detail">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                </svg>
                ${formatFileSize(parser.size)} — ${fileList}
                </span>
            </div>
            ${assignmentsHtml}
            </div>
            ${actionsHtml}
        </div>
        `;

            });

    elements.parserList.innerHTML = html;
}

function renderEmptyState() {
    elements.parserList.innerHTML = `
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path>
                <polyline points="13 2 13 9 20 9"></polyline>
            </svg>
            <h3>No parsers found</h3>
            <p>Upload your first parser to get started</p>
        </div>
    `;
}

function showLoading() {
    elements.parserList.innerHTML = '<div class="loading">Loading parsers...</div>';
}

// Modal Functions
function showDeleteModal(parserName) {
    deleteTarget = parserName;
    elements.deleteModal.classList.add('active');
}

function closeDeleteModal() {
    deleteTarget = null;
    elements.deleteModal.classList.remove('active');
}

// Toast Notification System
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const iconSvg = getToastIcon(type);
    toast.innerHTML = `
        ${iconSvg}
        <span class="toast-message">${escapeHtml(message)}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        </button>
    `;
    elements.toastContainer.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);
}

function getToastIcon(type) {
    const icons = {
        success: `
            <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
        `,
        error: `
            <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
        `,
        warning: `
            <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                <line x1="12" y1="9" x2="12" y2="13"></line>
                <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
        `,
        info: `
            <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="16" x2="12" y2="12"></line>
                <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
        `
    };
    return icons[type] || icons.info;
}

// Helper Functions
function resetUploadForm() {
    elements.uploadForm.reset();
    elements.fileName.textContent = 'No file selected';
    elements.fileName.classList.remove('has-file');
    elements.overwrite.checked = false;
    updateVerbAssignmentVisibility();
}

function quickAssign(parserName) {
    elements.assignParser.value = parserName;
    elements.assignVerb.focus();
    document.querySelector('.assign-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function escapeHtml(text) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return text.replace(/[&<>"']/g, m => map[m]);
}

// Export for inline handlers
window.quickAssign = quickAssign;
window.showDeleteModal = showDeleteModal;
window.unassignParser = unassignParser;

document.addEventListener('DOMContentLoaded', async () => {
    // Existing init code...
    initializeEventListeners();
    await loadProjects();
    const url = new URL(location.href);
    const preset = url.searchParams.get('project');
    currentProject = (preset && availableProjects.includes(preset))
      ? preset
      : (availableProjects[0] || '');
    if (currentProject) {
      elements.projectSelect.value = currentProject;
      elements.projectSelect.dispatchEvent(new Event('change'));
    } else {
      renderEmptyState();
    }

    // NEW: Check if RDS mode is active
    try {
        const resp = await fetch(`${API_BASE}/rds_mode`);
        if (resp.ok) {
            const data = await resp.json();
            if (data.rds_enabled) {
                document.getElementById('uploadCard').style.opacity = '0.6';
                document.getElementById('uploadDisabledOverlay').style.display = 'flex';
            } else {
                document.getElementById('uploadCard').style.opacity = '1';
                document.getElementById('uploadDisabledOverlay').style.display = 'none';
                document.getElementById('uploadCard').style.pointerEvents = 'auto';
            }
        }
    } catch (err) {
        console.warn('Could not determine RDS mode:', err);
    }
});

