document.addEventListener('DOMContentLoaded', function() {
    
    // Helper function for fetch operations (similar to verb_editor.js)
    async function fetchJSON(url, options = {}) {
        try {
            const res = await fetch(url, options);
            if (!res.ok) {
                const errorText = await res.text();
                console.error(`Error fetching ${url}: ${errorText}`);
                throw new Error(errorText);
            }
            const data = await res.json();
            return data;
        } catch (error) {
            console.error(`Fetch error for ${url}:`, error);
            throw error;
        }
    }

    // State
    const state = {
        currentProject: '',
        currentVerb: '',
        currentConjunction: null,
        conjunctions: [],
        fieldTypes: ['text', 'number', 'boolean', 'reference'],
        availableNouns: [] // Add this to store available nouns
    };

    // DOM Elements
    const projectSelect = document.getElementById('project-select');
    const verbSelect = document.getElementById('verb-select');
    const conjunctionTable = document.getElementById('conjunction-table');
    const conjunctionListBody = document.getElementById('conjunction-list-body');
    const addConjunctionBtn = document.getElementById('add-conjunction-btn');
    const conjunctionFormContainer = document.getElementById('conjunction-form-container');
    const conjunctionForm = document.getElementById('conjunction-form');
    const formTitle = document.getElementById('form-title');
    const closeFormBtn = document.getElementById('close-form-btn');
    const editMode = document.getElementById('edit-mode');
    const originalName = document.getElementById('original-name');
    const addFieldBtn = document.getElementById('add-field-btn');
    const fieldsContainer = document.getElementById('fields-container');
    const cancelBtn = document.getElementById('cancel-btn');
    const applyConjunctionContainer = document.getElementById('apply-conjunction-container');

    // Initialize
    initializeProjects();

    // Event Listeners
    projectSelect.addEventListener('change', handleProjectChange);
    verbSelect.addEventListener('change', handleVerbChange);
    addConjunctionBtn.addEventListener('click', showAddConjunctionForm);
    closeFormBtn.addEventListener('click', closeConjunctionForm);
    conjunctionForm.addEventListener('submit', saveConjunction);
    cancelBtn.addEventListener('click', closeConjunctionForm);
    addFieldBtn.addEventListener('click', addNewField);

    // Functions
    async function initializeProjects() {
        try {
            const projects = await fetchJSON('/conjunction/projects');

            projectSelect.innerHTML = '';

            if (!Array.isArray(projects) || projects.length === 0) {
                projectSelect.innerHTML = '<option value="">No projects available</option>';
                showNotification('No projects available', 'warning');
                return;
            }

            // Populate and default-select the first project
            projects.forEach((p, i) => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = p;
                if (i === 0) opt.selected = true;
                projectSelect.appendChild(opt);
            });

            // Set select value and run the usual change workflow
            projectSelect.value = projects[0];
            await handleProjectChange();
        } catch (error) {
            console.error('Error loading projects:', error);
            showNotification('Error loading projects', 'error');
            projectSelect.innerHTML = '<option value="">Error loading projects</option>';
        }
    }

    async function fetchAvailableNouns() {
        if (!state.currentProject || state.availableNouns.length > 0) {
            return; // Skip if we already have nouns or don't have a project
        }
        
        try {
            // Use conjunction-specific endpoint instead of project API
            const nounTypes = await fetchJSON(`/conjunction/nouns/${state.currentProject}`);
            
            // Include 'Run' as a special pseudo-noun (comes first in the list)
            state.availableNouns = ['Run', ...Object.keys(nounTypes)];
        } catch (error) {
            console.warn('Error loading noun types:', error);
            // Always include 'Run' even if we can't load other nouns
            state.availableNouns = ['Run'];
        }
    }

    async function handleProjectChange() {
        state.currentProject = projectSelect.value;
        state.currentVerb = '';
        state.conjunctions = [];
        state.availableNouns = []; // Reset nouns when project changes
        
        verbSelect.innerHTML = '<option value="">Loading verbs...</option>';
        addConjunctionBtn.disabled = true;
        
        if (!state.currentProject) {
            verbSelect.innerHTML = '<option value="">Select a project first</option>';
            return;
        }
        
        try {
            // Load verbs using conjunction-specific endpoint
            const verbs = await fetchJSON(`/conjunction/verbs/${state.currentProject}`);
            
            verbSelect.innerHTML = '';
            // Format is an array of verb names
            verbs.forEach(verb => {
                const option = document.createElement('option');
                option.value = verb;
                option.textContent = verb;
                verbSelect.appendChild(option);
            });
            
            if (verbs.length === 0) {
                verbSelect.innerHTML = '<option value="">No verbs available</option>';
            }
        } catch (error) {
            console.error('Error loading verbs:', error);
            verbSelect.innerHTML = '<option value="">Error loading verbs</option>';
        }
    }

    async function handleVerbChange() {
        state.currentVerb = verbSelect.value;
        closeConjunctionForm();  
        conjunctionListBody.innerHTML = '<tr><td colspan="3">Loading conjunctions...</td></tr>';
        
        if (!state.currentVerb) {
            conjunctionListBody.innerHTML = '<tr><td colspan="3">Select a verb to view conjunctions</td></tr>';
            addConjunctionBtn.disabled = true;
            return;
        }
        
        addConjunctionBtn.disabled = false;
        
        try {
            const response = await fetch(`/conjunction/list/${state.currentProject}/${state.currentVerb}`);
            
            // Set empty array as default for any non-success case
            state.conjunctions = [];
            
            if (response.ok) {
                try {
                    const data = await response.json();
                    // Only set if it's actually an array
                    if (Array.isArray(data)) {
                        state.conjunctions = data;
                    }
                } catch (parseError) {
                    // JSON parse error - just keep empty array
                    console.warn('Could not parse response as JSON');
                }
            }
            
            renderConjunctionList();
        } catch (error) {
            // Network error or other exception
            console.warn('Connection error - showing empty list');
            state.conjunctions = [];
            renderConjunctionList();
        }
    }

    function renderConjunctionList() {
        if (state.conjunctions.length === 0) {
            conjunctionListBody.innerHTML = '<tr><td colspan="3">No conjunctions defined</td></tr>';
            return;
        }
        
        conjunctionListBody.innerHTML = '';
        state.conjunctions.forEach(conj => {
            const row = document.createElement('tr');
            
            const nameCell = document.createElement('td');
            nameCell.textContent = conj.name;
            
            const descCell = document.createElement('td');
            descCell.textContent = conj.description || '';
            
            const actionsCell = document.createElement('td');
            actionsCell.className = 'actions';
            
            const editBtn = document.createElement('button');
            editBtn.textContent = 'Edit';
            editBtn.className = 'edit-btn';
            editBtn.onclick = () => showEditConjunctionForm(conj.name);
            
            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = 'Delete';
            deleteBtn.className = 'delete-btn';
            deleteBtn.onclick = () => deleteConjunction(conj.name);
            
            // Removed Apply button as requested
            
            actionsCell.appendChild(editBtn);
            actionsCell.appendChild(deleteBtn);
            
            row.appendChild(nameCell);
            row.appendChild(descCell);
            row.appendChild(actionsCell);
            
            conjunctionListBody.appendChild(row);
        });
    }

    function showAddConjunctionForm() {
        editMode.value = 'new';
        originalName.value = '';
        formTitle.textContent = 'New Conjunction';
        
        // Reset form
        conjunctionForm.reset();
        fieldsContainer.innerHTML = '';
        
        // Show form
        conjunctionFormContainer.classList.remove('hidden');
    }

    async function showEditConjunctionForm(name) {
        const conjunction = state.conjunctions.find(c => c.name === name);
        if (!conjunction) return;
        
        // Fetch available nouns first
        await fetchAvailableNouns();
        
        state.currentConjunction = conjunction;
        editMode.value = 'edit';
        originalName.value = name;
        formTitle.textContent = `Edit Conjunction: ${name}`;
        
        // Populate form
        document.getElementById('conj-name').value = conjunction.name;
        document.getElementById('conj-description').value = conjunction.description || '';
        document.getElementById('conj-category').value = conjunction.status || 'failure'; // Use status instead of category
        
        // Populate fields
        fieldsContainer.innerHTML = '';
        if (conjunction.fields && conjunction.fields.length > 0) {
            conjunction.fields.forEach(field => {
                // Convert string fields to proper field objects
                if (typeof field === 'string') {
                    addFieldToForm({
                        name: field,
                        type: 'text',
                        required: true,
                        description: ''
                    });
                } else {
                    addFieldToForm(field);
                }
            });
        }
        
        // Show form
        conjunctionFormContainer.classList.remove('hidden');
    }

    function closeConjunctionForm() {
        conjunctionFormContainer.classList.add('hidden');
        state.currentConjunction = null;
    }

    function addNewField() {
        addFieldToForm({
            name: '',
            type: 'text',
            required: false,
            description: ''
        });
    }

    function addFieldToForm(field) {
        const fieldDiv = document.createElement('div');
        fieldDiv.className = 'field-item';
        
        // For reference fields, use label as the name
        const fieldName = field.type === 'reference' ? (field.label || '') : (field.name || '');
        
        const fieldHtml = `
            <div class="field-row">
                <div class="field-group">
                    <label>Name:</label>
                    <input type="text" class="field-name" value="${fieldName}" required>
                </div>
                <div class="field-group">
                    <label>Type:</label>
                    <select class="field-type">
                        ${state.fieldTypes.map(type => 
                            `<option value="${type}" ${field.type === type ? 'selected' : ''}>${type}</option>`
                        ).join('')}
                    </select>
                </div>
                <div class="field-group field-required">
                    <label>
                        <input type="checkbox" class="field-required" ${field.required ? 'checked' : ''}>
                        Required
                    </label>
                </div>
                <button type="button" class="remove-field-btn">Remove</button>
            </div>
            <div class="field-row">
                <div class="field-group full-width">
                    <label>Description:</label>
                    <input type="text" class="field-description" value="${field.description || ''}">
                </div>
            </div>
            ${field.type === 'reference' ? `
            <div class="field-row reference-config">
                <div class="field-group">
                    <label>Reference Noun:</label>
                    <select class="field-reference-noun">
                        <option value="">Select a noun type</option>
                        ${state.availableNouns.map(noun => 
                            `<option value="${noun}" ${field.reference_noun === noun ? 'selected' : ''}>${noun}</option>`
                        ).join('')}
                    </select>
                </div>
            </div>` : ''}
        `;
        
        fieldDiv.innerHTML = fieldHtml;
        
        // Set up event handlers
        fieldDiv.querySelector('.remove-field-btn').addEventListener('click', function() {
            fieldDiv.remove();
        });
        
        const typeSelect = fieldDiv.querySelector('.field-type');
        typeSelect.addEventListener('change', async function() {
            const isReference = this.value === 'reference';
            let refConfig = fieldDiv.querySelector('.reference-config');
            
            if (isReference && !refConfig) {
                // Fetch nouns if needed
                await fetchAvailableNouns();
                
                refConfig = document.createElement('div');
                refConfig.className = 'field-row reference-config';
                refConfig.innerHTML = `
                    <div class="field-group">
                        <label>Reference Noun:</label>
                        <select class="field-reference-noun">
                            <option value="">Select a noun type</option>
                            ${state.availableNouns.map(noun => 
                                `<option value="${noun}">${noun}</option>`
                            ).join('')}
                        </select>
                    </div>
                `;
                fieldDiv.appendChild(refConfig);
            } else if (!isReference && refConfig) {
                refConfig.remove();
            }
        });
        
        fieldsContainer.appendChild(fieldDiv);
    }

    async function saveConjunction(event) {
        event.preventDefault();
        
        const name = document.getElementById('conj-name').value.trim();
        const description = document.getElementById('conj-description').value.trim();
        const status = document.getElementById('conj-category').value; // Get value from category dropdown
        
        // Collect fields
        const fields = [];
        const fieldItems = fieldsContainer.querySelectorAll('.field-item');
        fieldItems.forEach(item => {
            const fieldName = item.querySelector('.field-name').value.trim();
            const fieldType = item.querySelector('.field-type').value;
            const fieldRequired = item.querySelector('.field-required').checked;
            const fieldDescription = item.querySelector('.field-description').value.trim();
            
            // For reference fields, create a full object structure
            if (fieldType === 'reference') {
                const refSelect = item.querySelector('.field-reference-noun');
                if (refSelect && refSelect.value) {
                    fields.push({
                        type: 'reference',
                        mode: 'ReferenceList',
                        label: fieldName,
                        reference_noun: refSelect.value,
                        filters: {},
                        description: fieldDescription || undefined
                    });
                }
            } 
            // For all other fields, check if there's a description
            else if (fieldDescription) {
                // If there's a description, use an object
                fields.push({
                    name: fieldName,
                    description: fieldDescription,
                    required: fieldRequired
                });
            }
            // Otherwise use a simple string
            else {
                fields.push(fieldName);
            }
        });
        
        const conjunctionData = {
            name,
            description,
            status,
            fields
        };
        
        try {
            let response;
            
            if (editMode.value === 'edit') {
                const originalNameValue = originalName.value;
                response = await fetch(`/conjunction/update/${state.currentProject}/${state.currentVerb}/${originalNameValue}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(conjunctionData)
                });
            } else {
                response = await fetch(`/conjunction/register/${state.currentProject}/${state.currentVerb}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(conjunctionData)
                });
            }
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to save conjunction');
            }
            
            // Refresh list
            await handleVerbChange();
            closeConjunctionForm();
            showNotification(`Conjunction ${editMode.value === 'edit' ? 'updated' : 'created'} successfully`, 'success');
        } catch (error) {
            console.error('Error saving conjunction:', error);
            showNotification(error.message, 'error');
        }
    }

    async function deleteConjunction(name) {
        if (!confirm(`Are you sure you want to delete conjunction "${name}"?`)) {
            return;
        }
        
        try {
            const response = await fetch(`/conjunction/delete/${state.currentProject}/${state.currentVerb}/${name}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to delete conjunction');
            }
            
            // Refresh list
            await handleVerbChange();
            showNotification(`Conjunction "${name}" deleted successfully`, 'success');
        } catch (error) {
            console.error('Error deleting conjunction:', error);
            showNotification(error.message, 'error');
        }
    }

    function showApplyConjunctionForm(name) {
        // Implementation for applying conjunctions would go here
        // This would involve showing the apply form, populating verb groups and run IDs,
        // and then rendering the appropriate fields based on the conjunction schema
        alert('Apply conjunction functionality would be implemented here');
    }

    function showNotification(message, type = 'info') {
        // Simple notification - replace with your preferred notification system
        alert(`${type.toUpperCase()}: ${message}`);
    }
});