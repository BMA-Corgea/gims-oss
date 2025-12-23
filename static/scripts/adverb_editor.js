// Adverb Manager JavaScript

// Immediately invoked function to handle document ready state
(function() {
  // Check if document is already loaded
  if (document.readyState === 'loading') {
    // If not loaded yet, wait for DOMContentLoaded
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    // If already loaded, run initialization immediately
    initialize();
  }

  function initialize() {
    
    // DOM Elements
    const projectSelect = document.getElementById('project-select');
    const verbSelect = document.getElementById('verb-select');
    const adverbTable = document.getElementById('adverb-table');
    const adverbListBody = document.getElementById('adverb-list-body');
    const addAdverbBtn = document.getElementById('add-adverb-btn');
    const adverbFormContainer = document.getElementById('adverb-form-container');
    const adverbForm = document.getElementById('adverb-form');
    const formTitle = document.getElementById('form-title');
    const closeFormBtn = document.getElementById('close-form-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    const adverbClassSelect = document.getElementById('adverb-class');
    const typeConfigSections = document.querySelectorAll('.type-config');
    
    // Type-specific elements
    const tagConfigSection = document.getElementById('tag-config');
    const referenceConfigSection = document.getElementById('reference-config');
    const referenceListConfigSection = document.getElementById('reference-list-config');
    const attributeConfigSection = document.getElementById('attribute-config');
    const addTagOptionBtn = document.getElementById('add-tag-option-btn');
    const tagOptionsList = document.getElementById('tag-options-list');
    const referenceNounSelect = document.getElementById('reference-noun');
    const addFilterBtn = document.getElementById('add-filter-btn');
    const filtersList = document.getElementById('filters-list');
    const addNounBtn = document.getElementById('add-noun-btn');
    const referenceNounsContainer = document.getElementById('reference-nouns-container');
    const fieldTypeSelect = document.getElementById('field-type');
    const dateFormatContainer = document.getElementById('date-format-container');
    
    // State
    let currentProject = '';
    let currentVerb = '';
    let nouns = [];
    let nounFields = {};
    let verbTypes = {};
    
    // Helper function for fetch operations (similar to conjunction.js)
    async function fetchJSON(url, options = {}) {
      const res = await fetch(url, options);
      if (!res.ok) {
        const errorText = await res.text();
        console.error(`Error fetching ${url}: ${errorText}`);
        throw new Error(errorText);
      }
      return res.json();
    }
    
    // Create status bar if it doesn't exist
    let statusBar = document.getElementById('status-bar');
    if (!statusBar) {
      statusBar = document.createElement('div');
      statusBar.id = 'status-bar';
      statusBar.style.position = 'fixed';
      statusBar.style.bottom = '0';
      statusBar.style.left = '0';
      statusBar.style.width = '100%';
      statusBar.style.padding = '10px';
      statusBar.style.textAlign = 'center';
      statusBar.style.color = 'white';
      statusBar.style.fontWeight = 'bold';
      statusBar.style.zIndex = '1000';
      statusBar.style.display = 'none';
      document.body.appendChild(statusBar);
    }
    
    // Helper functions for showing status messages
    function showSuccess(message) {
      statusBar.textContent = `✅ ${message}`;
      statusBar.style.backgroundColor = '#4CAF50'; // Green
      statusBar.style.display = 'block';
      setTimeout(() => {
        statusBar.style.display = 'none';
      }, 3000); // Hide after 3 seconds
    }
    
    function showError(message) {
      statusBar.textContent = `❌ ${message}`;
      statusBar.style.backgroundColor = '#F44336'; // Red
      statusBar.style.display = 'block';
      setTimeout(() => {
        statusBar.style.display = 'none';
      }, 5000); // Hide after 5 seconds
    }
    
    // Initialize
    loadProjects();
    
    // Event Listeners
    projectSelect.addEventListener('change', handleProjectChange);
    verbSelect.addEventListener('change', handleVerbChange);
    addAdverbBtn.addEventListener('click', showNewAdverbForm);
    closeFormBtn.addEventListener('click', closeForm);
    cancelBtn.addEventListener('click', closeForm);
    adverbForm.addEventListener('submit', handleFormSubmit);
    adverbClassSelect.addEventListener('change', updateFormForSelectedType);
    fieldTypeSelect.addEventListener('change', handleFieldTypeChange);
    
    // Type-specific event listeners
    addTagOptionBtn.addEventListener('click', addTagOption);
    addFilterBtn.addEventListener('click', addFilter);
    addNounBtn.addEventListener('click', addReferenceNoun);
    
    // Helper Functions
    async function loadProjects() {
      try {
        const projects = await fetchJSON('/adverb/projects');

        projectSelect.innerHTML = '';

        if (!Array.isArray(projects) || projects.length === 0) {
          projectSelect.innerHTML = '<option value="">No projects available</option>';
          addAdverbBtn.disabled = true;
          clearAdverbList();
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

        // Ensure the select reflects the first project and trigger the usual flow
        projectSelect.value = projects[0];
        await handleProjectChange();
      } catch (error) {
        console.error('Error loading projects:', error);
        projectSelect.innerHTML = '<option value="">Error loading projects</option>';
        addAdverbBtn.disabled = true;
        clearAdverbList();
      }
    }
    
    function handleProjectChange() {
      currentProject = projectSelect.value;
      if (!currentProject) {
        verbSelect.innerHTML = '<option value="">Select a project first</option>';
        return;
      }
      
      loadVerbs(currentProject);
      loadNouns(currentProject);
      clearAdverbList();
      addAdverbBtn.disabled = true;
    }
    
    function loadVerbs(project) {
      // Replace with adverb-specific verb listing endpoint
      fetch(`/adverb/list/${project}`)
        .then(response => response.json())
        .then(data => {
          verbSelect.innerHTML = '';
          verbTypes = data;
          
          if (Object.keys(data).length === 0) {
            verbSelect.innerHTML = '<option value="">No verbs found</option>';
            return;
          }
          
          Object.keys(data).forEach(verb => {
            const option = document.createElement('option');
            option.value = verb;
            option.textContent = verb;
            verbSelect.appendChild(option);
          });
        })
        .catch(error => console.error('Error loading verbs:', error));
    }
    
    function loadNouns(project) {
      // Replace with adverb-specific noun listing endpoint
      fetch(`/adverb/nouns/${project}`)
        .then(response => response.json())
        .then(data => {
          nouns = Object.keys(data);
          
          // Also load field definitions for each noun
          nouns.forEach(noun => {
            nounFields[noun] = data[noun].fields || {};
          });
        })
        .catch(error => console.error('Error loading nouns:', error));
    }
    
    function handleVerbChange() {
      currentVerb = verbSelect.value;
      if (!currentVerb) {
        clearAdverbList();
        addAdverbBtn.disabled = true;
        return;
      }
      
      loadAdverbs(currentProject, currentVerb);
      addAdverbBtn.disabled = false;
    }
    
    function loadAdverbs(project, verb) {
      if (!verbTypes[verb]) {
        clearAdverbList();
        return;
      }
      
      // adverbs is a dict { "adverb_name": { ...config... } }
      const adverbs = verbTypes[verb].adverb_schema || {};

      // ---------------------------------------------------------------
      // [FIX] Update local state *before* rendering
      // This ensures re-renders after CUD operations are correct.
      // ---------------------------------------------------------------
      if (verbTypes[verb] && verbTypes[verb].adverb_schema) {
          verbTypes[verb].adverb_schema = adverbs;
      } else if (verbTypes[verb]) {
          verbTypes[verb].adverb_schema = {};
      }
      
      renderAdverbList(adverbs);
    }
    
    function renderAdverbList(adverbs) {
      adverbListBody.innerHTML = '';
      
      if (Object.keys(adverbs).length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="4">No adverbs found for this verb</td>';
        adverbListBody.appendChild(row);
        return;
      }
      
      Object.entries(adverbs).forEach(([name, config]) => {
        const row = document.createElement('tr');
        
        const nameCell = document.createElement('td');
        nameCell.textContent = name;
        
        const typeCell = document.createElement('td');
        typeCell.textContent = config.adverb_class || 'Attribute';
        
        const requiredCell = document.createElement('td');
        requiredCell.textContent = config.required ? 'Yes' : 'No';
        
        const actionsCell = document.createElement('td');
        
        const editBtn = document.createElement('button');
        editBtn.className = 'edit-btn';
        editBtn.textContent = 'Edit';
        editBtn.addEventListener('click', () => editAdverb(name, config));
        
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'delete-btn';
        deleteBtn.textContent = 'Delete';
        deleteBtn.addEventListener('click', () => deleteAdverb(name));
        
        actionsCell.appendChild(editBtn);
        actionsCell.appendChild(document.createTextNode(' '));
        actionsCell.appendChild(deleteBtn);
        
        row.appendChild(nameCell);
        row.appendChild(typeCell);
        row.appendChild(requiredCell);
        row.appendChild(actionsCell);
        
        adverbListBody.appendChild(row);
      });
    }
    
    function clearAdverbList() {
      adverbListBody.innerHTML = '<tr><td colspan="4">Select a verb to view adverbs</td></tr>';
    }
    
    function showNewAdverbForm() {
      formTitle.textContent = 'New Adverb';
      adverbForm.reset();
      document.getElementById('edit-mode').value = 'new';
      document.getElementById('original-name').value = '';
      
      // Reset type-specific sections
      hideAllTypeConfigs();
      updateFormForSelectedType();
      
      // Show the form
      adverbFormContainer.classList.remove('hidden');
    }
    
    function editAdverb(name, config) {
      formTitle.textContent = 'Edit Adverb';
      document.getElementById('edit-mode').value = 'edit';
      document.getElementById('original-name').value = name;
      
      // Fill in the form fields
      document.getElementById('adverb-name').value = name;
      document.getElementById('adverb-description').value = config.description || '';
      document.getElementById('adverb-class').value = config.adverb_class || 'Attribute';
      document.getElementById('adverb-required').checked = config.required || false;
      
      // Handle type-specific configurations
      hideAllTypeConfigs();
      
      const adverbClass = config.adverb_class || 'Attribute';
      
      if (adverbClass === 'Tag') {
        tagConfigSection.classList.remove('hidden');
        populateTagOptions(config.valid_options || []);
      } 
      else if (adverbClass === 'Reference') {
        referenceConfigSection.classList.remove('hidden');
        populateReferenceFields(config);
      }
      else if (adverbClass === 'ReferenceList') {
        referenceListConfigSection.classList.remove('hidden');
        populateReferenceListFields(config);
      }
      else if (adverbClass === 'Attribute') {
        attributeConfigSection.classList.remove('hidden');
        document.getElementById('field-type').value = config.field_type || 'string';
        
        if (config.field_type === 'date') {
          dateFormatContainer.classList.remove('hidden');
          document.getElementById('date-format').value = config.format || 'yyyy-mm-dd';
        } else {
          dateFormatContainer.classList.add('hidden');
        }
      }
      
      // Show the form
      adverbFormContainer.classList.remove('hidden');
    }
    
    function hideAllTypeConfigs() {
      typeConfigSections.forEach(section => {
        section.classList.add('hidden');
      });
      
      // Clear type-specific containers
      tagOptionsList.innerHTML = '';
      filtersList.innerHTML = '';
      referenceNounsContainer.innerHTML = '';
    }
    
    function updateFormForSelectedType() {
      hideAllTypeConfigs();
      
      const selectedType = adverbClassSelect.value;
      
      if (selectedType === 'Tag') {
        tagConfigSection.classList.remove('hidden');
      } 
      else if (selectedType === 'Reference') {
        referenceConfigSection.classList.remove('hidden');
        populateReferenceNounSelect();
      }
      else if (selectedType === 'ReferenceList') {
        referenceListConfigSection.classList.remove('hidden');
      }
      else if (selectedType === 'Attribute') {
        attributeConfigSection.classList.remove('hidden');
        handleFieldTypeChange();
      }
    }
    
    function handleFieldTypeChange() {
      if (fieldTypeSelect.value === 'date') {
        dateFormatContainer.classList.remove('hidden');
      } else {
        dateFormatContainer.classList.add('hidden');
      }
    }
    
    function closeForm() {
      adverbFormContainer.classList.add('hidden');
    }
    
    function handleFormSubmit(event) {
      event.preventDefault();
      
      const formData = collectFormData();
      const isEdit = document.getElementById('edit-mode').value === 'edit';
      const originalName = document.getElementById('original-name').value;
      
      if (isEdit) {
        // [FIX] Directly call the S3-aware endpoint
        updateAdverbTypes(originalName, formData);
      } else {
        // [FIX] Directly call the S3-aware endpoint
        addToAdverbTypes(formData);
      }
    }
    
    function collectFormData() {
      const formData = {
        adverb: document.getElementById('adverb-name').value,
        verb: currentVerb,
        adverb_class: adverbClassSelect.value,
        description: document.getElementById('adverb-description').value,
        required: document.getElementById('adverb-required').checked
      };
      
      // Add type-specific data
      if (formData.adverb_class === 'Tag') {
        formData.valid_options = collectTagOptions();
      }
      else if (formData.adverb_class === 'Reference') {
        formData.reference_noun = referenceNounSelect.value;
        formData.filters = collectFilters();
      }
      else if (formData.adverb_class === 'ReferenceList') {
        formData.reference_nouns = collectReferenceNouns();
        formData.filters = {};
      }
      else if (formData.adverb_class === 'Attribute') {
        formData.field_type = fieldTypeSelect.value;
        if (formData.field_type === 'date') {
          formData.format = document.getElementById('date-format').value;
        }
      }
      
      return formData;
    }
    
    // [FIX] Removed createAdverb function. Logic moved into addToAdverbTypes.

    // [FIX] Removed updateAdverb function. Logic moved into updateAdverbTypes.

    function deleteAdverb(name) {
      // We can't use a custom modal, so we're stuck with confirm
      if (!confirm(`Are you sure you want to delete the adverb "${name}"?`)) {
        return;
      }
      
      // [FIX] Directly call the S3-aware endpoint
      removeFromAdverbTypes(name);
    }
    
    //
    // [FIX] DELETED THE ENTIRE ROGUE `updateVerbSchema` FUNCTION.
    // It was calling PUT /project/.../verb_types/... and was the
    // source of the local file truncation.
    //
    

    // Functions to manage adverb_types.json - following the pattern from adjective_editor.js
    function addToAdverbTypes(data) {
      // Use the promote endpoint pattern similar to adjective_editor
      fetch(`/adverb/promote/${currentProject}/${currentVerb}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
      .then(response => {
        if (!response.ok) {
          console.error(`Failed to add adverb: ${response.status} ${response.statusText}`);
          return response.text().then(text => {
            throw new Error(`Failed to add adverb: ${text}`);
          });
        }
        return response.json();
      })
      .then(result => {
        showSuccess(`Successfully added adverb "${data.adverb}"`);
        
        // [FIX] Update local state and UI *after* successful save
        if (!verbTypes[currentVerb]) verbTypes[currentVerb] = { adverb_schema: {} };
        if (!verbTypes[currentVerb].adverb_schema) verbTypes[currentVerb].adverb_schema = {};
        verbTypes[currentVerb].adverb_schema[data.adverb] = data;
        
        loadAdverbs(currentProject, currentVerb); // Re-render list
        closeForm(); // Close modal
      })
      .catch(error => {
        showError(`Error adding adverb: ${error.message}`);
        console.error('Error adding adverb:', error);
      });
    }

    function updateAdverbTypes(originalName, data) {
      // Use the update endpoint pattern similar to adjective_editor
      fetch(`/adverb/update/${currentProject}/${currentVerb}/${encodeURIComponent(originalName)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
      .then(response => {
        if (!response.ok) {
          console.error(`Failed to update adverb: ${response.status} ${response.statusText}`);
          return response.text().then(text => {
            throw new Error(`Failed to update adverb: ${text}`);
          });
        }
        return response.json();
      })
      .then(result => {
        showSuccess(`Successfully updated adverb "${data.adverb}"`);
        
        // [FIX] Update local state and UI *after* successful save
        if (verbTypes[currentVerb] && verbTypes[currentVerb].adverb_schema) {
            if (originalName !== data.adverb) {
                delete verbTypes[currentVerb].adverb_schema[originalName];
            }
            verbTypes[currentVerb].adverb_schema[data.adverb] = data;
        }
        
        loadAdverbs(currentProject, currentVerb); // Re-render list
        closeForm(); // Close modal
      })
      .catch(error => {
        showError(`Error updating adverb: ${error.message}`);
        console.error('Error updating adverb:', error);
      });
    }

    function removeFromAdverbTypes(name) {
      // Use the demote endpoint pattern similar to adjective_editor
      fetch(`/adverb/demote/${currentProject}/${currentVerb}/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
      .then(response => {
        if (!response.ok) {
          console.error(`Failed to remove adverb: ${response.status} ${response.statusText}`);
          return response.text().then(text => {
            throw new Error(`Failed to remove adverb: ${text}`);
          });
        }
        return response.json();
      })
      .then(result => {
        showSuccess(`Successfully removed adverb "${name}"`);
        
        // [FIX] Update local state and UI *after* successful save
        if (verbTypes[currentVerb] && verbTypes[currentVerb].adverb_schema) {
            delete verbTypes[currentVerb].adverb_schema[name];
        }
        
        loadAdverbs(currentProject, currentVerb); // Re-render list
        closeForm(); // Close modal
      })
      .catch(error => {
        showError(`Error removing adverb: ${error.message}`);
        console.error('Error removing adverb:', error);
      });
    }
    
    // Tag Options Helpers
    function addTagOption() {
      const optionDiv = document.createElement('div');
      optionDiv.className = 'tag-option-item';
      
      const valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.className = 'tag-option-value';
      valueInput.placeholder = 'Option Value';
      
      const explanationInput = document.createElement('input');
      explanationInput.type = 'text';
      explanationInput.className = 'tag-option-explanation';
      explanationInput.placeholder = 'Explanation (optional)';
      
      const displayCheckbox = document.createElement('input');
      displayCheckbox.type = 'checkbox';
      displayCheckbox.className = 'tag-option-display';
      
      const displayLabel = document.createElement('label');
      displayLabel.textContent = 'Show in ID';
      displayLabel.appendChild(displayCheckbox);
      
      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'delete-btn';
      deleteBtn.textContent = '×';
      deleteBtn.addEventListener('click', () => optionDiv.remove());
      
      optionDiv.appendChild(valueInput);
      optionDiv.appendChild(explanationInput);
      optionDiv.appendChild(displayLabel);
      optionDiv.appendChild(deleteBtn);
      
      tagOptionsList.appendChild(optionDiv);
    }
    
    function populateTagOptions(options) {
      tagOptionsList.innerHTML = '';
      
      options.forEach(option => {
        const optionDiv = document.createElement('div');
        optionDiv.className = 'tag-option-item';
        
        const valueInput = document.createElement('input');
        valueInput.type = 'text';
        valueInput.className = 'tag-option-value';
        valueInput.value = option.value || '';
        
        const explanationInput = document.createElement('input');
        explanationInput.type = 'text';
        explanationInput.className = 'tag-option-explanation';
        explanationInput.value = option.explanation || '';
        
        const displayCheckbox = document.createElement('input');
        displayCheckbox.type = 'checkbox';
        displayCheckbox.className = 'tag-option-display';
        displayCheckbox.checked = option.display_in_label || false;
        
        const displayLabel = document.createElement('label');
        displayLabel.textContent = 'Show in ID';
        displayLabel.appendChild(displayCheckbox);
        
        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'delete-btn';
        deleteBtn.textContent = '×';
        deleteBtn.addEventListener('click', () => optionDiv.remove());
        
        optionDiv.appendChild(valueInput);
        optionDiv.appendChild(explanationInput);
        optionDiv.appendChild(displayLabel);
        optionDiv.appendChild(deleteBtn);
        
        tagOptionsList.appendChild(optionDiv);
      });
    }
    
    function collectTagOptions() {
      const options = [];
      const optionItems = tagOptionsList.querySelectorAll('.tag-option-item');
      
      optionItems.forEach(item => {
        const value = item.querySelector('.tag-option-value').value;
        const explanation = item.querySelector('.tag-option-explanation').value;
        const display = item.querySelector('.tag-option-display').checked;
        
        if (value) {
          options.push({
            value: value,
            explanation: explanation,
            display_in_label: display
          });
        }
      });
      
      return options;
    }
    
    // Reference Helpers
    function populateReferenceNounSelect() {
      referenceNounSelect.innerHTML = '';
      
      nouns.forEach(noun => {
        const option = document.createElement('option');
        option.value = noun;
        option.textContent = noun;
        referenceNounSelect.appendChild(option);
      });
      
      // If there are nouns, trigger a change to load fields for the first one
      if (nouns.length > 0) {
        referenceNounSelect.value = nouns[0];
      }
    }
    
    function populateReferenceFields(config) {
      populateReferenceNounSelect();
      
      if (config.reference_noun) {
        referenceNounSelect.value = config.reference_noun;
      }
      
      // Populate filters
      filtersList.innerHTML = '';
      const filters = config.filters || {};
      
      Object.entries(filters).forEach(([field, value]) => {
        addFilterWithValues(field, value);
      });
    }
    
    function addFilter() {
      const selectedNoun = referenceNounSelect.value;
      const fields = nounFields[selectedNoun] || {};
      
      const filterDiv = document.createElement('div');
      filterDiv.className = 'filter-item';
      
      const fieldSelect = document.createElement('select');
      fieldSelect.className = 'filter-field';
      
      Object.keys(fields).forEach(field => {
        const option = document.createElement('option');
        option.value = field;
        option.textContent = field;
        fieldSelect.appendChild(option);
      });
      
      const valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.className = 'filter-value';
      valueInput.placeholder = 'Required Value';
      
      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'delete-btn';
      deleteBtn.textContent = '×';
      deleteBtn.addEventListener('click', () => filterDiv.remove());
      
      filterDiv.appendChild(fieldSelect);
      filterDiv.appendChild(valueInput);
      filterDiv.appendChild(deleteBtn);
      
      filtersList.appendChild(filterDiv);
    }
    
    function addFilterWithValues(field, value) {
      const selectedNoun = referenceNounSelect.value;
      const fields = nounFields[selectedNoun] || {};
      
      const filterDiv = document.createElement('div');
      filterDiv.className = 'filter-item';
      
      const fieldSelect = document.createElement('select');
      fieldSelect.className = 'filter-field';
      
      Object.keys(fields).forEach(f => {
        const option = document.createElement('option');
        option.value = f;
        option.textContent = f;
        option.selected = (f === field);
        fieldSelect.appendChild(option);
      });
      
      const valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.className = 'filter-value';
      valueInput.value = value;
      
      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'delete-btn';
      deleteBtn.textContent = '×';
      deleteBtn.addEventListener('click', () => filterDiv.remove());
      
      filterDiv.appendChild(fieldSelect);
      filterDiv.appendChild(valueInput);
      filterDiv.appendChild(deleteBtn);
      
      filtersList.appendChild(filterDiv);
    }
    
    function collectFilters() {
      const filters = {};
      const filterItems = filtersList.querySelectorAll('.filter-item');
      
      filterItems.forEach(item => {
        const field = item.querySelector('.filter-field').value;
        const value = item.querySelector('.filter-value').value;
        
        if (field && value) {
          filters[field] = value;
        }
      });
      
      return filters;
    }
    
    // Reference List Helpers
    function addReferenceNoun() {
      const nounDiv = document.createElement('div');
      nounDiv.className = 'reference-noun-item';
      
      const nounSelect = document.createElement('select');
      nounSelect.className = 'reference-noun-select';
      
      nouns.forEach(noun => {
        const option = document.createElement('option');
        option.value = noun;
        option.textContent = noun;
        nounSelect.appendChild(option);
      });
      
      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'delete-btn';
      deleteBtn.textContent = '×';
      deleteBtn.addEventListener('click', () => nounDiv.remove());
      
      nounDiv.appendChild(nounSelect);
      nounDiv.appendChild(deleteBtn);
      
      referenceNounsContainer.appendChild(nounDiv);
    }
    
    function populateReferenceListFields(config) {
      referenceNounsContainer.innerHTML = '';
      
      const nounList = config.reference_nouns || [];
      
      nounList.forEach(noun => {
        const nounDiv = document.createElement('div');
        nounDiv.className = 'reference-noun-item';
        
        const nounSelect = document.createElement('select');
        nounSelect.className = 'reference-noun-select';
        
        nouns.forEach(n => {
          const option = document.createElement('option');
          option.value = n;
          option.textContent = n;
          option.selected = (n === noun);
          nounSelect.appendChild(option);
        });
        
        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'delete-btn';
        deleteBtn.textContent = '×';
        deleteBtn.addEventListener('click', () => nounDiv.remove());
        
        nounDiv.appendChild(nounSelect);
        nounDiv.appendChild(deleteBtn);
        
        referenceNounsContainer.appendChild(nounDiv);
      });
    }
    
    function collectReferenceNouns() {
      const nounList = [];
      const nounItems = referenceNounsContainer.querySelectorAll('.reference-noun-select');
      
      nounItems.forEach(select => {
        nounList.push(select.value);
      });
      
      return nounList;
    }
  }
})();

