/**
 * Spreadsheet Importer Forge Panel
 *
 * Imports Google Sheets data into Unreal DataTables.
 * UI: Presets -> Spreadsheet dropdown -> Available Tabs list -> Import Queue list
 * Auto-detects target DataTable from cell A1 of each sheet.
 *
 * Supports two data fetch modes:
 * - 'hodor': Uses Hodor MCP for Google APIs (team-friendly, no local credentials)
 * - 'direct': Uses Python toolset's direct Google API calls (requires local OAuth tokens)
 */

const PRESETS_STORAGE_KEY = 'spreadsheet_importer_presets';
const CONFIG_STORAGE_KEY = 'spreadsheet_importer_config';

// Default config - can be changed via UI or localStorage
const DEFAULT_CONFIG = {
    fetchMode: 'hodor',  // 'hodor' or 'direct'
    nameFilter: 'TSV'    // Filter spreadsheets by name
};

host.registerPanel({
    id: 'Spreadsheet_Importer',

    // State
    config: { ...DEFAULT_CONFIG },
    spreadsheets: [],
    selectedSpreadsheet: null,
    selectedPreset: null,
    availableTabs: [],
    importQueue: [],
    presets: {},
    isLoading: false,

    async render(root) {
        root.classList.add('ue-root');

        // Load saved config and presets
        this.loadConfig();
        this.loadPresets();

        root.innerHTML = `
            <div class="panel-container">
                <div class="main-column">
                    <h2 class="ue-head">Spreadsheet Importer</h2>

                    <div class="section">
                        <label class="ue-label">Preset</label>
                        <div class="row">
                            <div class="custom-dropdown flex-1" id="presetDropdown">
                                <div class="dropdown-header" id="presetHeader">
                                    <span class="dropdown-text">-- Select Preset --</span>
                                    <span class="dropdown-arrow">▼</span>
                                </div>
                                <div class="dropdown-list" id="presetList"></div>
                            </div>
                            <button id="savePresetBtn" class="ue-btn" title="Save current queue as preset">Save</button>
                            <button id="deletePresetBtn" class="ue-btn" title="Delete selected preset">Del</button>
                        </div>
                        <div class="row preset-save-row" id="presetSaveRow" style="display: none;">
                            <input type="text" id="presetNameInput" class="ue-input flex-1" placeholder="Enter preset name...">
                            <button id="confirmSaveBtn" class="ue-btn">OK</button>
                            <button id="cancelSaveBtn" class="ue-btn">Cancel</button>
                        </div>
                    </div>

                    <div class="section">
                        <label class="ue-label">Google Spreadsheet <button id="refreshSpreadsheetsBtn" class="ue-btn-small" title="Refresh">↻</button></label>
                        <div class="custom-dropdown" id="spreadsheetDropdown">
                            <div class="dropdown-header" id="spreadsheetHeader">
                                <span class="dropdown-text">Loading spreadsheets...</span>
                                <span class="dropdown-arrow">▼</span>
                            </div>
                            <div class="dropdown-list" id="spreadsheetList"></div>
                        </div>
                    </div>

                    <div class="lists-container">
                        <div class="list-panel">
                            <label class="ue-label">Available Tabs</label>
                            <div class="list-box" id="availableTabsList"></div>
                        </div>

                        <div class="list-controls">
                            <button id="addToQueueBtn" class="ue-btn" title="Add selected">→</button>
                            <button id="addAllToQueueBtn" class="ue-btn" title="Add all">⇉</button>
                            <button id="removeFromQueueBtn" class="ue-btn" title="Remove">←</button>
                            <button id="clearQueueBtn" class="ue-btn" title="Clear">✕</button>
                        </div>

                        <div class="list-panel">
                            <label class="ue-label">Import Queue (<span id="queueCount">0</span>)</label>
                            <div class="list-box" id="importQueueList"></div>
                        </div>
                    </div>

                    <div class="section">
                        <button id="importBtn" class="ue-btn ue-btn-primary import-btn" disabled>
                            Import Queue
                        </button>
                    </div>
                </div>

                <div class="results-column">
                    <div class="status-bar" id="statusBar"></div>
                    <div class="results" id="resultsSection">
                        <h3 class="ue-head">Import Results</h3>
                        <div id="resultsContent" class="results-empty">Run an import to see results</div>
                    </div>
                    <div class="config-section">
                        <label class="ue-label">Data Source</label>
                        <div class="row">
                            <button id="modeHodorBtn" class="ue-btn mode-btn ${this.config.fetchMode === 'hodor' ? 'active' : ''}" title="Use Hodor (team OAuth)">Hodor</button>
                            <button id="modeDirectBtn" class="ue-btn mode-btn ${this.config.fetchMode === 'direct' ? 'active' : ''}" title="Use direct API (local OAuth)">Direct</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        this.bindEvents(root);
        this.renderPresetDropdown();
        await this.loadSpreadsheets();
    },

    // Config Management
    loadConfig() {
        try {
            const stored = localStorage.getItem(CONFIG_STORAGE_KEY);
            if (stored) {
                this.config = { ...DEFAULT_CONFIG, ...JSON.parse(stored) };
            }
        } catch (e) {
            this.config = { ...DEFAULT_CONFIG };
        }
    },

    saveConfig() {
        try {
            localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(this.config));
        } catch (e) {
            host.log('error', 'Failed to save config:', e);
        }
    },

    setFetchMode(mode) {
        this.config.fetchMode = mode;
        this.saveConfig();

        // Update button states
        document.getElementById('modeHodorBtn')?.classList.toggle('active', mode === 'hodor');
        document.getElementById('modeDirectBtn')?.classList.toggle('active', mode === 'direct');

        this.setStatus(`Switched to ${mode === 'hodor' ? 'Hodor' : 'Direct'} mode`);
        setTimeout(() => this.setStatus(''), 2000);

        // Reload spreadsheets with new mode
        this.loadSpreadsheets();
    },

    bindEvents(root) {
        const savePresetBtn = root.querySelector('#savePresetBtn');
        const deletePresetBtn = root.querySelector('#deletePresetBtn');
        const refreshSpreadsheetsBtn = root.querySelector('#refreshSpreadsheetsBtn');
        const addToQueueBtn = root.querySelector('#addToQueueBtn');
        const addAllToQueueBtn = root.querySelector('#addAllToQueueBtn');
        const removeFromQueueBtn = root.querySelector('#removeFromQueueBtn');
        const clearQueueBtn = root.querySelector('#clearQueueBtn');
        const importBtn = root.querySelector('#importBtn');

        // Mode toggle buttons
        root.querySelector('#modeHodorBtn')?.addEventListener('click', () => this.setFetchMode('hodor'));
        root.querySelector('#modeDirectBtn')?.addEventListener('click', () => this.setFetchMode('direct'));

        // Custom preset dropdown handlers
        const presetDropdown = root.querySelector('#presetDropdown');
        const presetHeader = root.querySelector('#presetHeader');
        const presetList = root.querySelector('#presetList');

        presetHeader.addEventListener('click', () => {
            presetDropdown.classList.toggle('open');
        });

        presetList.addEventListener('click', (e) => {
            const item = e.target.closest('.list-item');
            if (!item) return;

            const presetName = item.dataset.presetName;
            this.selectedPreset = presetName;
            root.querySelector('#presetHeader .dropdown-text').textContent = presetName;
            presetDropdown.classList.remove('open');
            this.loadPreset(presetName);
        });

        savePresetBtn.addEventListener('click', () => this.showPresetSaveInput());
        deletePresetBtn.addEventListener('click', () => this.deletePreset());

        // Preset save input handlers
        const confirmSaveBtn = root.querySelector('#confirmSaveBtn');
        const cancelSaveBtn = root.querySelector('#cancelSaveBtn');
        const presetNameInput = root.querySelector('#presetNameInput');

        confirmSaveBtn.addEventListener('click', () => this.confirmSavePreset());
        cancelSaveBtn.addEventListener('click', () => this.hidePresetSaveInput());
        presetNameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.confirmSavePreset();
            if (e.key === 'Escape') this.hidePresetSaveInput();
        });

        // Custom dropdown handlers
        const dropdown = root.querySelector('#spreadsheetDropdown');
        const header = root.querySelector('#spreadsheetHeader');
        const list = root.querySelector('#spreadsheetList');

        header.addEventListener('click', () => {
            dropdown.classList.toggle('open');
        });

        list.addEventListener('click', async (e) => {
            const item = e.target.closest('.list-item');
            if (!item) return;

            // Update selection
            this.selectedSpreadsheet = {
                id: item.dataset.spreadsheetId,
                name: item.dataset.spreadsheetName
            };

            // Update header text and close dropdown
            root.querySelector('#spreadsheetHeader .dropdown-text').textContent = item.dataset.spreadsheetName;
            dropdown.classList.remove('open');

            await this.loadTabs();
        });

        // Close dropdowns when clicking outside
        document.addEventListener('click', (e) => {
            if (!dropdown.contains(e.target)) {
                dropdown.classList.remove('open');
            }
            if (!presetDropdown.contains(e.target)) {
                presetDropdown.classList.remove('open');
            }
        });

        refreshSpreadsheetsBtn.addEventListener('click', () => this.loadSpreadsheets());
        addToQueueBtn.addEventListener('click', () => this.addSelectedToQueue());
        addAllToQueueBtn.addEventListener('click', () => this.addAllToQueue());
        removeFromQueueBtn.addEventListener('click', () => this.removeSelectedFromQueue());
        clearQueueBtn.addEventListener('click', () => this.clearQueue());
        importBtn.addEventListener('click', () => this.doImport());

        root.querySelector('#availableTabsList').addEventListener('dblclick', (e) => {
            if (e.target.classList.contains('list-item')) {
                this.addTabToQueue(e.target.dataset.tabName);
            }
        });

        root.querySelector('#importQueueList').addEventListener('dblclick', (e) => {
            if (e.target.classList.contains('list-item')) {
                this.removeFromQueueByIndex(parseInt(e.target.dataset.index));
            }
        });
    },

    // Preset Management
    loadPresets() {
        try {
            const stored = localStorage.getItem(PRESETS_STORAGE_KEY);
            this.presets = stored ? JSON.parse(stored) : {};
        } catch (e) {
            this.presets = {};
        }
    },

    savePresetsToStorage() {
        try {
            localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(this.presets));
        } catch (e) {
            host.log('error', 'Failed to save presets:', e);
        }
    },

    renderPresetDropdown() {
        const list = document.getElementById('presetList');
        const headerText = document.querySelector('#presetHeader .dropdown-text');
        if (!list) return;

        const presetNames = Object.keys(this.presets).sort();

        if (presetNames.length === 0) {
            list.innerHTML = '<div class="list-empty">No presets saved</div>';
        } else {
            list.innerHTML = presetNames.map(name =>
                `<div class="list-item" data-preset-name="${name}">${name}</div>`
            ).join('');
        }

        // Reset header if selected preset was deleted
        if (this.selectedPreset && !this.presets[this.selectedPreset]) {
            this.selectedPreset = null;
            if (headerText) headerText.textContent = '-- Select Preset --';
        }
    },

    showPresetSaveInput() {
        if (this.importQueue.length === 0) {
            this.setStatus('Queue is empty - nothing to save', true);
            return;
        }
        const row = document.getElementById('presetSaveRow');
        const input = document.getElementById('presetNameInput');
        if (row && input) {
            row.style.display = 'flex';
            input.value = '';
            input.focus();
        }
    },

    hidePresetSaveInput() {
        const row = document.getElementById('presetSaveRow');
        if (row) row.style.display = 'none';
    },

    confirmSavePreset() {
        const input = document.getElementById('presetNameInput');
        const name = input?.value?.trim();
        if (!name) {
            this.setStatus('Enter a preset name', true);
            return;
        }

        this.presets[name] = [...this.importQueue];
        this.selectedPreset = name;
        this.savePresetsToStorage();
        this.renderPresetDropdown();
        this.hidePresetSaveInput();

        // Update header to show saved preset
        const headerText = document.querySelector('#presetHeader .dropdown-text');
        if (headerText) headerText.textContent = name;

        this.setStatus(`Preset "${name}" saved`);
        setTimeout(() => this.setStatus(''), 2000);
    },

    loadPreset(name) {
        if (!name || !this.presets[name]) return;

        this.importQueue = [...this.presets[name]];
        this.renderImportQueue();
        this.setStatus(`Loaded preset "${name}"`);
        setTimeout(() => this.setStatus(''), 2000);
    },

    deletePreset() {
        if (!this.selectedPreset) {
            this.setStatus('Select a preset to delete', true);
            return;
        }

        const name = this.selectedPreset;
        if (!confirm(`Delete preset "${name}"?`)) return;

        delete this.presets[name];
        this.selectedPreset = null;
        this.savePresetsToStorage();
        this.renderPresetDropdown();

        // Reset header text
        const headerText = document.querySelector('#presetHeader .dropdown-text');
        if (headerText) headerText.textContent = '-- Select Preset --';

        this.setStatus(`Preset "${name}" deleted`);
        setTimeout(() => this.setStatus(''), 2000);
    },

    setStatus(message, isError = false) {
        const statusBar = document.getElementById('statusBar');
        if (statusBar) {
            statusBar.textContent = message;
            statusBar.className = 'status-bar' + (isError ? ' error' : '') + (message ? ' visible' : '');
        }
    },

    setLoading(loading) {
        this.isLoading = loading;
        const importBtn = document.getElementById('importBtn');
        if (importBtn) {
            importBtn.disabled = loading || this.importQueue.length === 0;
            importBtn.textContent = loading ? 'Importing...' : 'Import Queue';
        }
    },

    updateImportButton() {
        const importBtn = document.getElementById('importBtn');
        if (importBtn) {
            importBtn.disabled = this.isLoading || this.importQueue.length === 0;
        }
        const queueCount = document.getElementById('queueCount');
        if (queueCount) {
            queueCount.textContent = this.importQueue.length;
        }
    },

    parseResult(result) {
        // Fast path for string result
        if (typeof result === 'string') return JSON.parse(result);
        // Handle returnValue wrapper
        if (result && result.returnValue !== undefined) {
            return typeof result.returnValue === 'string' ? JSON.parse(result.returnValue) : result.returnValue;
        }
        // Handle MCP content array format
        if (result && Array.isArray(result.content)) {
            for (let i = 0; i < result.content.length; i++) {
                if (result.content[i].type === 'text' && result.content[i].text) {
                    return JSON.parse(result.content[i].text);
                }
            }
        }
        return result;
    },

    // =========================================================================
    // HODOR MODE: Google API calls via Hodor MCP
    // =========================================================================

    async hodorCall(toolName, args) {
        await mcp.ready();
        const result = await mcp.call('mcp__hodor__hodor_execute_tool', {
            tool_name: toolName,
            arguments: args
        });
        return this.parseResult(result);
    },

    async loadSpreadsheetsHodor() {
        const query = `mimeType='application/vnd.google-apps.spreadsheet' and name contains '${this.config.nameFilter}' and trashed=false`;
        const result = await this.hodorCall('google_drive_list', {
            query: query,
            pageSize: 50
        });

        if (result.files) {
            return result.files.map(f => ({
                id: f.id,
                name: f.name,
                modifiedTime: f.modifiedTime
            }));
        }
        return [];
    },

    async loadTabsHodor(spreadsheetId) {
        const result = await this.hodorCall('google_sheets_get_sheet_names', {
            spreadsheetId: spreadsheetId
        });

        if (result.sheets) {
            return result.sheets.map(s => s.title);
        }
        return [];
    },

    async fetchSheetDataHodor(spreadsheetId, tabName) {
        const range = `'${tabName}'!A:ZZ`;
        const result = await this.hodorCall('google_sheets_read_range', {
            spreadsheetId: spreadsheetId,
            range: range
        });

        return result.values || [];
    },

    // Convert 2D array to TSV string
    valuesToTsv(values) {
        if (!values || values.length === 0) return '';
        return values.map(row =>
            row.map(cell => {
                const str = cell != null ? String(cell) : '';
                // Escape tabs and newlines
                return str.replace(/\t/g, ' ').replace(/\n/g, '\\n').replace(/\r/g, '');
            }).join('\t')
        ).join('\n');
    },

    async importTabHodor(spreadsheetId, tabName) {
        // 1. Fetch sheet data via Hodor
        const values = await this.fetchSheetDataHodor(spreadsheetId, tabName);

        if (values.length < 2) {
            return { success: false, error: 'Sheet has no data rows' };
        }

        // 2. Get A1 value (DataTable name) and replace with "---" for row key
        const dtName = values[0][0] || '';
        values[0][0] = '---';

        // 3. Convert to TSV
        const tsvContent = this.valuesToTsv(values);

        // 4. Call Python toolset to do the actual import
        await mcp.ready();
        const result = await mcp.call('tsv_import_toolset.TSVImportToolset.import_tsv_string', {
            tsv_content: tsvContent,
            dt_path: dtName  // Let Python resolve the DataTable path
        });

        return this.parseResult(result);
    },

    // =========================================================================
    // DIRECT MODE: Google API calls via Python toolset (existing behavior)
    // =========================================================================

    async loadSpreadsheetsDirect() {
        const result = await mcp.call('tsv_import_toolset.TSVImportToolset.list_spreadsheets', {
            name_filter: this.config.nameFilter
        });
        const parsed = this.parseResult(result);
        if (!parsed.success) throw new Error(parsed.error || 'Failed to load spreadsheets');
        return parsed.spreadsheets || [];
    },

    async loadTabsDirect(spreadsheetId) {
        const result = await mcp.call('tsv_import_toolset.TSVImportToolset.get_spreadsheet_tabs', {
            spreadsheet_id: spreadsheetId
        });
        const parsed = this.parseResult(result);
        if (!parsed.success) throw new Error(parsed.error || 'Failed to load tabs');
        return parsed.tabs || [];
    },

    async importTabDirect(spreadsheetId, tabName) {
        const result = await mcp.call('tsv_import_toolset.TSVImportToolset.import_tab_to_datatable', {
            spreadsheet_id: spreadsheetId,
            tab_name: tabName,
            dt_path: ''
        });
        return this.parseResult(result);
    },

    // =========================================================================
    // UNIFIED API (routes to Hodor or Direct based on config)
    // =========================================================================

    async loadSpreadsheets() {
        const header = document.getElementById('spreadsheetHeader');
        const list = document.getElementById('spreadsheetList');
        const headerText = header?.querySelector('.dropdown-text');

        // Show loading state immediately
        if (headerText) headerText.textContent = 'Loading...';
        if (list) list.innerHTML = '';
        this.setStatus(`Loading spreadsheets (${this.config.fetchMode} mode)...`);

        try {
            await mcp.ready();

            this.spreadsheets = this.config.fetchMode === 'hodor'
                ? await this.loadSpreadsheetsHodor()
                : await this.loadSpreadsheetsDirect();

            const count = this.spreadsheets.length;

            // Update header
            if (headerText) {
                headerText.textContent = count === 0 ? 'No spreadsheets found' : '-- Select Spreadsheet --';
            }

            // Populate dropdown list
            if (list) {
                list.innerHTML = this.spreadsheets.map(ss =>
                    `<div class="list-item" data-spreadsheet-id="${ss.id}" data-spreadsheet-name="${ss.name}">${ss.name}</div>`
                ).join('');
            }

            this.setStatus(`Loaded ${count} spreadsheets`);
            setTimeout(() => this.setStatus(''), 2000);

        } catch (err) {
            host.log('error', 'Failed to load spreadsheets:', err);
            if (headerText) headerText.textContent = 'Failed to load';
            this.setStatus(err.message, true);
        }
    },

    async loadTabs() {
        if (!this.selectedSpreadsheet) return;

        this.setStatus('Loading tabs...');
        try {
            await mcp.ready();

            this.availableTabs = this.config.fetchMode === 'hodor'
                ? await this.loadTabsHodor(this.selectedSpreadsheet.id)
                : await this.loadTabsDirect(this.selectedSpreadsheet.id);

            this.renderAvailableTabs();

            this.setStatus(`Loaded ${this.availableTabs.length} tabs`);
            setTimeout(() => this.setStatus(''), 2000);

        } catch (err) {
            host.log('error', 'Failed to load tabs:', err);
            this.setStatus(err.message, true);
        }
    },

    async doImport() {
        if (this.importQueue.length === 0 || this.isLoading) return;

        this.setLoading(true);
        this.setStatus(`Importing ${this.importQueue.length} sheets (${this.config.fetchMode} mode)...`);

        const results = [];
        let totalRows = 0;
        let totalErrors = 0;
        let allSuccess = true;

        try {
            await mcp.ready();

            for (const item of this.importQueue) {
                this.setStatus(`Importing ${item.tabName}...`);

                try {
                    let result;
                    if (this.config.fetchMode === 'hodor') {
                        result = await this.importTabHodor(item.spreadsheetId, item.tabName);
                    } else {
                        result = await this.importTabDirect(item.spreadsheetId, item.tabName);
                    }

                    results.push({
                        tab_name: item.tabName,
                        datatable: result.datatable_used || result.dt_path || '',
                        success: result.success,
                        rows_imported: result.rows_imported || 0,
                        errors: result.errors || []
                    });

                    totalRows += result.rows_imported || 0;
                    totalErrors += (result.errors || []).length;
                    if (!result.success) allSuccess = false;

                } catch (err) {
                    results.push({
                        tab_name: item.tabName,
                        datatable: '',
                        success: false,
                        rows_imported: 0,
                        errors: [`Import failed: ${err.message}`]
                    });
                    totalErrors++;
                    allSuccess = false;
                }
            }

            const parsed = {
                success: allSuccess,
                results: results,
                total_rows: totalRows,
                total_errors: totalErrors
            };

            this.showResults(parsed);

            if (allSuccess) {
                this.setStatus('Import complete!');
                host.notify(`Imported ${totalRows} rows from ${this.importQueue.length} sheets`, 'success');
            } else {
                this.setStatus(`Import completed with ${totalErrors} errors`, true);
            }

        } catch (err) {
            host.log('error', 'Import failed:', err);
            this.setStatus('Import failed: ' + err.message, true);
            host.notify('Import failed: ' + err.message, 'error');
        } finally {
            this.setLoading(false);
        }
    },

    renderAvailableTabs() {
        const list = document.getElementById('availableTabsList');
        if (!list) return;

        if (this.availableTabs.length === 0) {
            list.innerHTML = '<div class="list-empty">Select a spreadsheet</div>';
            return;
        }

        list.innerHTML = this.availableTabs.map(tabName =>
            `<div class="list-item" data-tab-name="${tabName}">${tabName}</div>`
        ).join('');

        // Force Chromium repaint
        requestAnimationFrame(() => {
            list.style.display = 'none';
            list.offsetHeight;
            list.style.display = '';
        });

        list.querySelectorAll('.list-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.ctrlKey || e.metaKey) {
                    item.classList.toggle('selected');
                } else {
                    list.querySelectorAll('.list-item').forEach(i => i.classList.remove('selected'));
                    item.classList.add('selected');
                }
            });
        });
    },

    renderImportQueue() {
        const list = document.getElementById('importQueueList');
        if (!list) return;

        if (this.importQueue.length === 0) {
            list.innerHTML = '<div class="list-empty">Add tabs to import</div>';
            this.updateImportButton();
            return;
        }

        list.innerHTML = this.importQueue.map((item, index) => `
            <div class="list-item" data-index="${index}">
                <span class="queue-tab">${item.tabName}</span>
                <span class="queue-source">${item.spreadsheetName}</span>
            </div>
        `).join('');

        list.querySelectorAll('.list-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.ctrlKey || e.metaKey) {
                    item.classList.toggle('selected');
                } else {
                    list.querySelectorAll('.list-item').forEach(i => i.classList.remove('selected'));
                    item.classList.add('selected');
                }
            });
        });

        this.updateImportButton();
    },

    addTabToQueue(tabName) {
        if (!this.selectedSpreadsheet) return;

        const exists = this.importQueue.some(
            item => item.spreadsheetId === this.selectedSpreadsheet.id && item.tabName === tabName
        );
        if (exists) return;

        this.importQueue.push({
            spreadsheetId: this.selectedSpreadsheet.id,
            spreadsheetName: this.selectedSpreadsheet.name,
            tabName: tabName
        });

        this.renderImportQueue();
    },

    addSelectedToQueue() {
        const list = document.getElementById('availableTabsList');
        const selected = list.querySelectorAll('.list-item.selected');
        selected.forEach(item => this.addTabToQueue(item.dataset.tabName));
    },

    addAllToQueue() {
        this.availableTabs.forEach(tabName => this.addTabToQueue(tabName));
    },

    removeFromQueueByIndex(index) {
        if (index >= 0 && index < this.importQueue.length) {
            this.importQueue.splice(index, 1);
            this.renderImportQueue();
        }
    },

    removeSelectedFromQueue() {
        const list = document.getElementById('importQueueList');
        const selected = list.querySelectorAll('.list-item.selected');
        const indices = Array.from(selected).map(item => parseInt(item.dataset.index)).sort((a, b) => b - a);
        indices.forEach(index => this.importQueue.splice(index, 1));
        this.renderImportQueue();
    },

    clearQueue() {
        this.importQueue = [];
        this.renderImportQueue();
    },

    showResults(result) {
        const content = document.getElementById('resultsContent');
        if (!content) return;

        let html = `
            <div class="result-row">
                <span class="result-label">Status:</span>
                <span class="result-value ${result.success ? 'success' : 'error'}">
                    ${result.success ? 'Success' : 'Errors'}
                </span>
            </div>
            <div class="result-row">
                <span class="result-label">Total Rows:</span>
                <span class="result-value">${result.total_rows || 0}</span>
            </div>
        `;

        if (result.results?.length > 0) {
            html += '<ul class="result-list">';
            result.results.forEach(r => {
                const status = r.success ? '✓' : '✗';
                const statusClass = r.success ? 'success' : 'error';
                const dtName = r.datatable ? r.datatable.split('/').pop().split('.')[0] : 'N/A';
                html += `<li class="${statusClass}">
                    ${status} <strong>${r.tab_name}</strong> → ${dtName} (${r.rows_imported || 0} rows)
                </li>`;

                // Show ALL errors, not just the first one
                if (r.errors?.length > 0) {
                    html += '<ul class="error-list">';
                    r.errors.forEach(err => {
                        // Clean up error message for display
                        const cleanErr = this.formatError(err, r.tab_name);
                        html += `<li class="error">${cleanErr}</li>`;
                    });
                    html += '</ul>';
                }
            });
            html += '</ul>';
        }

        content.innerHTML = html;
    },

    formatError(err, tabName) {
        // Remove redundant [TabName] prefix if present (since we group by tab)
        let msg = err.replace(new RegExp(`^\\[${tabName}\\]\\s*`, 'i'), '');

        // Enhance cryptic Unreal errors with context
        const colMatch = msg.match(/Missing.*?column\s*(\d+)/i) ||
                         msg.match(/Name not found.*?Column\s*(\d+)/i);
        if (colMatch) {
            const colNum = colMatch[1];
            // Add hint about what this means
            msg += ` (TSV column ${colNum} doesn't match any DataTable property - check column header spelling)`;
        }

        return msg;
    },

    onActivate() {},
    onDeactivate() {},
    onClose() {},

    onError(err) {
        host.log('error', 'Panel error:', err);
        this.setStatus('Error: ' + err.message, true);
    }
});
