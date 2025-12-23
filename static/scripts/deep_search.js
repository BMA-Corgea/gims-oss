document.addEventListener('DOMContentLoaded', initialize);

function initialize() {
    // DOM Elements
    const projectSelect = document.getElementById('project-select');
    const searchInput = document.getElementById('search-input');
    const searchButton = document.getElementById('search-button');
    const resultsContainer = document.getElementById('results-list');
    const noResults = document.getElementById('no-results');
    const searchStats = document.getElementById('search-stats');
    const loadingIndicator = document.getElementById('loading-indicator');
    const tabButtons = document.querySelectorAll('.tab-button');
    
    // Status bar for feedback
    const statusBar = document.getElementById('status-bar');
    
    // State
    let currentProject = '';
    let currentResults = null;
    let currentTab = 'all';
    
    // Load projects on startup
    loadProjects();
    
    // Event Listeners
    projectSelect.addEventListener('change', () => {
        currentProject = projectSelect.value;
        clearResults();
    });
    
    searchButton.addEventListener('click', performSearch);
    
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            performSearch();
        }
    });
    
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            currentTab = button.dataset.tab;
            
            // Update active tab
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            
            // Rerender results for selected tab
            if (currentResults) {
                renderResults(currentResults);
            }
        });
    });
    
    // Functions
    async function loadProjects() {
        try {
            const response = await fetch('/deep_search/projects');
            if (!response.ok) {
                throw new Error('Failed to load projects');
            }
            
            const projects = await response.json();

            projectSelect.innerHTML = '';
            if (!Array.isArray(projects) || projects.length === 0) {
                projectSelect.innerHTML = '<option value="">No projects available</option>';
                showError('No projects available');
                return;
            }

            // Populate and default-select the first project
            projects.forEach((project, i) => {
                const option = document.createElement('option');
                option.value = project;
                option.textContent = project;
                if (i === 0) option.selected = true;
                projectSelect.appendChild(option);
            });

            // Set state and fire change to run normal selection logic
            currentProject = projects[0];
            projectSelect.value = currentProject;
            projectSelect.dispatchEvent(new Event('change'));
        } catch (error) {
            showError('Error loading projects: ' + error.message);
        }
    }
    
    async function performSearch() {
        const searchTerm = searchInput.value.trim();
        if (!searchTerm) {
            showError('Please enter a search term');
            return;
        }
        
        if (!currentProject) {
            showError('Please select a project');
            return;
        }
        
        // Show loading indicator
        loadingIndicator.classList.remove('hidden');
        noResults.classList.add('hidden');
        resultsContainer.innerHTML = '';
        searchStats.textContent = '';
        
        try {
            const response = await fetch(`/deep_search/${currentProject}?term=${encodeURIComponent(searchTerm)}`);
            
            if (!response.ok) {
                if (response.status === 500) {
                    throw new Error('Internal server error - Search engine encountered a problem');
                } else {
                    throw new Error(`Search failed with status: ${response.status}`);
                }
            }
            
            const data = await response.json();
            currentResults = data.results;
            
            renderResults(currentResults);
        } catch (error) {
            console.error('Search error:', error);
            
            noResults.innerHTML = `
                <div class="error-message">
                    <h3>Search Error</h3>
                    <p>${error.message}</p>
                    <p class="error-help">Please try a different search term or contact your administrator.</p>
                </div>
            `;
            noResults.classList.remove('hidden');
            searchStats.textContent = 'Search failed';
            
            showError('Search error: ' + error.message);
        } finally {
            loadingIndicator.classList.add('hidden');
        }
    }
    
    function renderResults(results) {
        resultsContainer.innerHTML = '';
        
        const totalResults = countResults(results);
        if (totalResults === 0) {
            noResults.textContent = 'No results found';
            noResults.classList.remove('hidden');
            searchStats.textContent = 'No matches found';
            return;
        }
        
        noResults.classList.add('hidden');
        searchStats.textContent = `Found ${totalResults} matches`;
        
        // Filter results by tab
        const filteredResults = filterResultsByTab(results, currentTab);
        
        // Render schema matches
        if (filteredResults.schema && filteredResults.schema.length > 0) {
            const section = createResultSection('Schema Matches', 'schema');
            filteredResults.schema.forEach(match => {
                section.appendChild(createSchemaResultItem(match));
            });
            resultsContainer.appendChild(section);
        }
        
        // Render noun matches
        if (filteredResults.noun_instances && filteredResults.noun_instances.length > 0) {
            const section = createResultSection('Noun Matches', 'noun');
            filteredResults.noun_instances.forEach(match => {
                section.appendChild(createNounResultItem(match));
            });
            resultsContainer.appendChild(section);
        }
        
        // Render verb matches
        if (filteredResults.verb_runs && filteredResults.verb_runs.length > 0) {
            const section = createResultSection('Verb Matches', 'verb');
            filteredResults.verb_runs.forEach(match => {
                section.appendChild(createVerbResultItem(match));
            });
            resultsContainer.appendChild(section);
        }
    }
    
    function filterResultsByTab(results, tab) {
        if (tab === 'all') {
            return results;
        }
        
        const filtered = {};
        
        if (tab === 'schema' && results.schema) {
            filtered.schema = results.schema;
        }
        
        if (tab === 'noun' && results.noun_instances) {
            filtered.noun_instances = results.noun_instances;
        }
        
        if (tab === 'verb' && results.verb_runs) {
            filtered.verb_runs = results.verb_runs;
        }
        
        return filtered;
    }
    
    function countResults(results) {
        let count = 0;
        
        if (results.schema) {
            count += results.schema.length;
        }
        
        if (results.noun_instances) {
            count += results.noun_instances.length;
        }
        
        if (results.verb_runs) {
            count += results.verb_runs.length;
        }
        
        return count;
    }
    
    function createResultSection(title, type) {
        const section = document.createElement('div');
        section.className = 'result-section';
        section.dataset.type = type;
        
        const header = document.createElement('h2');
        header.textContent = title;
        section.appendChild(header);
        
        return section;
    }
    
    function createSchemaResultItem(match) {
        const item = document.createElement('div');
        item.className = 'result-item schema-result';
        
        const title = document.createElement('h3');
        title.textContent = `${match.schema_type}: ${match.schema_name}`;
        
        const path = document.createElement('div');
        path.className = 'result-path';
        path.textContent = match.path || '';
        
        const details = document.createElement('div');
        details.className = 'result-details';
        details.innerHTML = `<strong>Match:</strong> ${highlightMatch(JSON.stringify(match.match_context, null, 2))}`;
        
        item.appendChild(title);
        item.appendChild(path);
        item.appendChild(details);
        
        return item;
    }
    
    function createNounResultItem(match) {
        const item = document.createElement('div');
        item.className = 'result-item noun-result';
        
        // Get primary ID based on the noun type
        const nounType = match._noun_type || 'Unknown Type';
        let primaryIdValue = '';
        let primaryIdField = '';
        
        // Find primary ID from match context if available
        if (match.match_context) {
            // The match_context contains the field that matched
            const contextField = Object.keys(match.match_context)[0];
            const contextValue = match.match_context[contextField];
            
            // If this is the submission_id or other primary ID field, use it
            if (contextField.toLowerCase().includes('id') || 
                contextField.toLowerCase() === 'name' ||
                contextField.toLowerCase() === primaryIdField.toLowerCase()) {
                primaryIdValue = contextValue;
                primaryIdField = contextField;
            }
        }
        
        // If we couldn't find from context, look for common ID fields
        if (!primaryIdValue) {
            // Add specific check for "Sample ID" with space
            if (match["Sample ID"]) {
                primaryIdValue = match["Sample ID"];
                primaryIdField = "Sample ID";
            } else {
                // Add more variations that might contain spaces
                const possibleIdFields = [
                    `${nounType.toLowerCase()}_id`,
                    `${nounType} ID`,
                    `${nounType} Id`,
                    `${nounType.toLowerCase()} id`,
                    'id', 
                    'ID',
                    'sample_id',
                    'Sample ID',  // Add this specific case
                    'batch_id',
                    'Batch ID',
                    'submission_id',
                    'Submission ID',
                    'run_id',
                    'Run ID',
                    'name'
                ];
                
                for (const field of possibleIdFields) {
                    if (match[field]) {
                        primaryIdValue = match[field];
                        primaryIdField = field;
                        break;
                    }
                }
            }
        }
        
        // Create title with primary ID if found, otherwise use noun type
        const title = document.createElement('h3');
        if (primaryIdValue) {
            title.textContent = primaryIdValue;
            title.dataset.nounType = nounType; // Store noun type as data attribute
        } else if (match.name || match["Name"]) {
            title.textContent = match.name || match["Name"];
            title.dataset.nounType = nounType;
        } else {
            title.textContent = nounType;
        }
        
        // Create subtitle with noun type
        const subTitle = document.createElement('div');
        subTitle.className = 'result-subtitle';
        subTitle.textContent = `Type: ${nounType}`;
        
        // Create path/metadata section
        const path = document.createElement('div');
        path.className = 'result-path';
        
        // Add primary ID field if not already in title
        if (primaryIdValue && title.textContent !== primaryIdValue) {
            path.innerHTML = `<strong>${primaryIdField}:</strong> ${primaryIdValue}<br>`;
        }
        
        // Add other metadata
        if (match._runID) {
            path.innerHTML += `<strong>Run ID:</strong> ${match._runID}<br>`;
        }
        
        // Add match context section
        const details = document.createElement('div');
        details.className = 'result-details';
        
        if (match.match_context) {
            const contextField = Object.keys(match.match_context)[0];
            const contextValue = match.match_context[contextField];
            details.innerHTML = `<strong>Matched in field "${contextField}":</strong> ${highlightMatch(contextValue)}`;
        } else {
            details.innerHTML = `<strong>Match:</strong> ${highlightMatch(JSON.stringify(match, null, 2))}`;
        }
        
        // Assemble the card
        item.appendChild(title);
        item.appendChild(subTitle);
        item.appendChild(path);
        item.appendChild(details);
        
        return item;
    }

    function createVerbResultItem(match) {
        const item = document.createElement('div');
        item.className = 'result-item verb-result';
        
        // Log the actual data for debugging
        console.log("Verb run data:", match);
        
        // Get the primary ID field that was identified by the backend
        let runId = 'Unknown Run';
        if (match._primary_id_field_resolved || match._primary_id_field) {
            const primaryField = match._primary_id_field_resolved || match._primary_id_field;
            runId = match[primaryField] || 'Unknown Run';
        } else {
            // Fallback to checking common variations including "general ID"
            runId = match["general ID"] || match.run_ID || match.run_id || match.runID || 
                    match.RunID || match["Run ID"] || match.id || match.ID || 'Unknown Run';
        }
        
        // Use run_id as primary title if available
        const title = document.createElement('h3');
        title.textContent = runId;
        
        // Create subtitle with verb group
        const subTitle = document.createElement('div');
        subTitle.className = 'result-subtitle';
        subTitle.textContent = `Verb: ${match._verb_group || 'Unknown'}`;
        
        // Additional metadata
        const path = document.createElement('div');
        path.className = 'result-path';
        
        // Add test type if available
        if (match.test_type) {
            path.innerHTML += `<strong>Test Type:</strong> ${match.test_type}<br>`;
        }
        
        // Add date if available with various field name possibilities
        const dateField = match.date_tested || match.date || match.run_date || match.timestamp || null;
        if (dateField) {
            path.innerHTML += `<strong>Date:</strong> ${dateField}<br>`;
        }
        
        // Add status if available
        if (match.status) {
            path.innerHTML += `<strong>Status:</strong> ${match.status}<br>`;
        }
        
        // Add additional important fields that are commonly found in verb runs
        if (match.result) {
            path.innerHTML += `<strong>Result:</strong> ${match.result}<br>`;
        }
        
        if (match.operator) {
            path.innerHTML += `<strong>Operator:</strong> ${match.operator}<br>`;
        }
        
        // Match context
        const details = document.createElement('div');
        details.className = 'result-details';
        
        if (match.match_context) {
            const contextField = Object.keys(match.match_context)[0];
            const contextValue = match.match_context[contextField];
            details.innerHTML = `<strong>Matched in field "${contextField}":</strong> ${highlightMatch(contextValue)}`;
        } else {
            details.innerHTML = `<strong>Match:</strong> ${highlightMatch(JSON.stringify(match, null, 2))}`;
        }
        
        // Add match score if available (helps debug)
        if (match.match_score) {
            details.innerHTML += `<br><small>Match score: ${match.match_score} (${match.match_type || 'unknown type'})</small>`;
        }
        
        item.appendChild(title);
        item.appendChild(subTitle);
        item.appendChild(path);
        item.appendChild(details);
        
        return item;
    }
    
    function highlightMatch(text) {
        const searchTerm = searchInput.value.trim();
        if (!searchTerm) return text;
        
        // Basic escape for regex special characters
        const escapedTerm = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(escapedTerm, 'gi');
        
        return text.replace(regex, match => `<mark>${match}</mark>`);
    }
    
    function clearResults() {
        resultsContainer.innerHTML = '';
        noResults.textContent = 'Enter a search term to begin';
        noResults.classList.remove('hidden');
        searchStats.textContent = '';
        currentResults = null;
    }
    
    // Status bar functions - show only errors
    function showSuccess(message) {
        // Success messages disabled
        return;
    }
    
    function showError(message) {
        statusBar.textContent = `❌ ${message}`;
        statusBar.className = 'error';
        statusBar.style.display = 'block';
        setTimeout(() => {
            statusBar.style.display = 'none';
        }, 5000);
    }
}