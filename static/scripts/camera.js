document.addEventListener('DOMContentLoaded', function() {
  // DOM Elements
  const projectInfo = document.getElementById('project-info');
  const nounSelect = document.getElementById('nounSelect');
  const itemSelect = document.getElementById('itemSelect');
  const fileInput = document.getElementById('fileInput');
  const fileUploadSection = document.getElementById('fileUploadSection');
  const webcamSection = document.getElementById('webcamSection');
  const previewSection = document.getElementById('previewSection');
  const video = document.getElementById('video');
  const canvas = document.getElementById('canvas');
  const preview = document.getElementById('preview');
  const statusMessage = document.getElementById('status-message');
  const progressBar = document.getElementById('progress-bar');
  const progressInner = document.getElementById('progress-inner');
  const statusBar = document.getElementById('status-bar');
  
  // Buttons
  const toggleModeBtn = document.getElementById('toggleModeBtn');
  const snapBtn = document.getElementById('snapBtn');
  const retakeBtn = document.getElementById('retakeBtn');
  const uploadBtn = document.getElementById('uploadBtn');
  // Remove addItemBtn reference completely

  // Get URL parameters
  const urlParams = new URLSearchParams(window.location.search);
  let projectName = urlParams.get('project');
  const nounName = urlParams.get('noun');
  const runID = urlParams.get('run'); // may be null

  // State variables
  let currentMode = 'file';
  let hasImage = false;

  // Always load projects dropdown first
  loadProjectsDropdown();

  // Hide controls until the project dropdown decides what to do
  document.querySelector('.control-panel').style.display = 'none';

  // Event listeners
  toggleModeBtn.addEventListener('click', toggleMode);
  snapBtn.addEventListener('click', snapPhoto);
  retakeBtn.addEventListener('click', retakePhoto);
  uploadBtn.addEventListener('click', uploadImage);
  // Remove addItemBtn listener completely
  nounSelect.addEventListener('change', loadItems);
  
  // File drop area functionality
  const fileDropArea = document.querySelector('.file-drop-area');
  
  fileDropArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    fileDropArea.classList.add('drag-over');
  });
  
  fileDropArea.addEventListener('dragleave', () => {
    fileDropArea.classList.remove('drag-over');
  });
  
  fileDropArea.addEventListener('drop', (e) => {
    e.preventDefault();
    fileDropArea.classList.remove('drag-over');
    
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      handleFileSelection();
    }
  });
  
  fileInput.addEventListener('change', handleFileSelection);

  // Modified function to load projects dropdown and handle missing project case
  function loadProjectsDropdown() {
    // Replace the static project info with a dropdown
    projectInfo.innerHTML = `
      <div class="project-selector-header">
        <select id="projectDropdown" class="project-dropdown"></select>
        ${nounName ? `<span class="header-divider">•</span> Noun: ${nounName}` : ''}
        ${runID ? `<span class="header-divider">•</span> Run: ${runID}` : ''}
      </div>
    `;
    
    const projectDropdown = document.getElementById('projectDropdown');
    
    // Add loading option
    projectDropdown.innerHTML = '<option value="">Loading projects...</option>';
    
    // Fetch projects from API
    fetch('/camera/projects')
      .then(response => response.json())
      .then(projects => {
        projectDropdown.innerHTML = ''; // Clear loading option

        if (projects.length === 0) {
          projectDropdown.innerHTML = '<option value="">No projects available</option>';
          showWarning('No projects available');
          return;
        }

        // Populate options
        projects.forEach(project => {
          const option = document.createElement('option');
          option.value = project;
          option.textContent = project;
          projectDropdown.appendChild(option);
        });

        // Auto-select: URL ?project= if valid, else first project
        if (!projectName || !projects.includes(projectName)) {
          projectName = projects[0];
        }
        projectDropdown.value = projectName;

        // Show controls and initialize for the selected project (no navigation)
        document.querySelector('.control-panel').style.display = '';
        initializeInterface();

        // Change handler: update state + re-init (no page reload)
        projectDropdown.addEventListener('change', function () {
          const newProject = this.value || '';
          if (!newProject || newProject === projectName) return;

          projectName = newProject;

          // quick UI reset
          nounSelect.innerHTML = '<option disabled selected>Loading…</option>';
          itemSelect.innerHTML = '<option disabled selected>Pick an item</option>';
          previewSection.classList.remove('active');
          hasImage = false;
          updateUploadButtonState();

          initializeInterface();
        });
      })
      .catch(err => {
        console.error('Error loading projects:', err);
        projectDropdown.innerHTML = '<option value="">Error loading projects</option>';
        showError('Failed to load projects');
      });
  }

  function initializeInterface() {
    // Always ensure controls are visible when initializing
    document.querySelector('.control-panel').style.display = '';

    // Fetch nouns for the active project, and only honor URL noun on first init
    fetch(`/camera/project/${projectName}/noun_types`)
      .then(r => {
        if (!r.ok) throw new Error(`Failed nouns: ${r.statusText}`);
        return r.json();
      })
      .then(nouns => {
        nounSelect.innerHTML = "";
        const nounTypes = Object.keys(nouns || {});

        if (!nounTypes.length) {
          showWarning("No nouns found in this project");
          nounSelect.innerHTML = "<option disabled selected>No nouns available</option>";
          itemSelect.innerHTML = "<option disabled selected>—</option>";
          return;
        }

        // Populate nouns
        nounTypes.forEach(noun => {
          const opt = document.createElement("option");
          opt.value = noun;
          opt.textContent = noun;
          nounSelect.appendChild(opt);
        });

        // First load can use ?noun= if valid; subsequent in-page switches ignore URL noun
        const firstTime = !initializeInterface._didInitOnce;
        const canUseUrlNoun = firstTime && nounName && nounTypes.includes(nounName);
        nounSelect.value = canUseUrlNoun ? nounName : nounTypes[0];

        initializeInterface._didInitOnce = true;

        loadItems();
      })
      .catch(err => {
        console.error("Failed to load nouns:", err);
        showError("Failed to load nouns");
        nounSelect.innerHTML = "<option disabled selected>Error</option>";
      });
  }

  function loadItems() {
    console.log("Loading items for noun:", nounSelect.value);
    const noun = nounSelect.value;
    
    if (!noun) {
      itemSelect.innerHTML = "<option disabled selected>Select a noun first</option>";
      return;
    }

    itemSelect.innerHTML = "<option disabled selected>Loading items...</option>";
    uploadBtn.disabled = true;
    
    // Get noun items from the API
    const url = `/camera/project/${projectName}/noun/${noun}/items`;
    console.log("Fetching items from:", url);
    
    fetch(url)
      .then(response => {
        console.log("Items response status:", response.status);
        if (!response.ok && response.status !== 404) {
          throw new Error(`Failed to load items: ${response.statusText}`);
        }
        return response.json();
      })
      .then(items => {
        console.log("Items loaded:", items);
        itemSelect.innerHTML = "";
        
        if (!items || items.length === 0) {
          itemSelect.innerHTML = "<option disabled selected>No items found</option>";
          showWarning(`No ${noun} items found. You may need to create some first.`);
          uploadBtn.disabled = true;
          return;
        }
        
        // Attempt to get the primary ID field from schema
        console.log("Loading schema for noun:", noun);
        return fetch(`/camera/project/${projectName}/noun_types/${noun}`)
          .then(response => {
            console.log("Schema response status:", response.status);
            return response.json();
          })
          .then(schema => {
            console.log("Schema loaded:", schema);
            const primaryIdField = schema.primary_id_field || `${noun.toLowerCase()}_id`;
            console.log("Using primary ID field:", primaryIdField);
            
            // Add each item using its primary ID
            items.forEach(item => {
              const id = item[primaryIdField];
              if (id) {
                const opt = document.createElement("option");
                opt.value = id;
                opt.textContent = id;
                itemSelect.appendChild(opt);
              }
            });
            
            showInfo(`Loaded ${items.length} items`);
            updateUploadButtonState();
          });
      })
      .catch(err => {
        console.error("Error loading items:", err);
        itemSelect.innerHTML = "<option disabled selected>Error loading items</option>";
        showError(`Failed to load items: ${err.message}`);
        uploadBtn.disabled = true;
      });
  }

  function toggleMode() {
    if (currentMode === "file") {
      // Switch to webcam mode
      currentMode = "webcam";
      fileUploadSection.classList.remove('active');
      webcamSection.classList.add('active');
      previewSection.classList.remove('active');
      toggleModeBtn.innerHTML = '<span class="icon">📁</span> Switch to File Upload';
      startWebcam();
    } else {
      // Switch to file upload mode
      currentMode = "file";
      fileUploadSection.classList.add('active');
      webcamSection.classList.remove('active');
      previewSection.classList.remove('active');
      toggleModeBtn.innerHTML = '<span class="icon">🎥</span> Switch to Webcam';
      stopWebcam();
    }
    
    // Reset upload button state
    updateUploadButtonState();
  }

  async function startWebcam() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ 
        video: { facingMode: 'environment' }
      });
      video.srcObject = stream;
      showInfo('Camera activated');
    } catch (err) {
      console.error("Webcam error:", err);
      showError("Could not access camera");
      toggleMode(); // Switch back to file mode
    }
  }

  function stopWebcam() {
    if (video.srcObject) {
      const tracks = video.srcObject.getTracks();
      tracks.forEach(track => track.stop());
      video.srcObject = null;
    }
  }

  function snapPhoto() {
    const ctx = canvas.getContext('2d');
    
    // Set canvas dimensions to match video
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    
    // Draw video frame to canvas
    ctx.drawImage(video, 0, 0);
    
    // Convert to image and show preview
    const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
    preview.src = dataUrl;
    
    // Show preview section
    webcamSection.classList.remove('active');
    previewSection.classList.add('active');
    
    hasImage = true;
    updateUploadButtonState();
    showInfo('Photo captured. Ready to upload.');
  }

  function retakePhoto() {
    if (currentMode === 'webcam') {
      // Go back to webcam view
      webcamSection.classList.add('active');
      previewSection.classList.remove('active');
    } else {
      // Go back to file upload
      fileUploadSection.classList.add('active');
      previewSection.classList.remove('active');
    }
    
    hasImage = false;
    updateUploadButtonState();
    showInfo('Photo discarded. Capture a new image.');
  }

  function handleFileSelection() {
    if (fileInput.files.length > 0) {
      const file = fileInput.files[0];
      
      // Check if it's an image
      if (!file.type.startsWith('image/')) {
        showError('Please select an image file');
        return;
      }
      
      // Create preview
      const reader = new FileReader();
      reader.onload = function(e) {
        preview.src = e.target.result;
        fileUploadSection.classList.remove('active');
        previewSection.classList.add('active');
        hasImage = true;
        updateUploadButtonState();
        showInfo('Image selected. Ready to upload.');
      };
      reader.readAsDataURL(file);
    }
  }

  function uploadImage() {
    const noun = nounSelect.value;
    const itemId = itemSelect.value;
    
    if (!noun) {
      showError('Please select a noun type');
      return;
    }
    
    if (!itemId) {
      showError('Please select an item');
      return;
    }
    
    if (!hasImage) {
      showError('Please capture or select an image first');
      return;
    }
    
    // Prepare FormData
    const formData = new FormData();
    formData.append('item_id', itemId);
    if (runID) formData.append('run_id', runID);
    
    // Get the image data
    let imagePromise;
    
    if (currentMode === 'file' && fileInput.files.length > 0) {
      // Use selected file
      imagePromise = Promise.resolve(fileInput.files[0]);
    } else if (currentMode === 'webcam' || previewSection.classList.contains('active')) {
      // Use canvas/preview image
      imagePromise = new Promise(resolve => {
        canvas.toBlob(blob => {
          const timestamp = new Date().toISOString().replace(/[:.]/g, '');
          resolve(new File([blob], `webcam_capture_${timestamp}.jpg`, { type: 'image/jpeg' }));
        }, 'image/jpeg', 0.85);
      });
    } else {
      showError('No image available');
      return;
    }
    
    // Show progress bar
    showProgress();
    
    // Upload the image
    imagePromise.then(file => {
      formData.append('file', file);
      
      // Use the camera_gui endpoint
      return fetch(`/camera/upload/${projectName}/${noun}`, {
        method: 'POST',
        body: formData
      });
    })
    .then(response => {
      if (!response.ok) {
        return response.json().then(data => {
          throw new Error(data.detail || 'Upload failed');
        });
      }
      return response.json();
    })
    .then(data => {
      hideProgress();
      showSuccess('Image uploaded successfully!');
      console.log('Upload result:', data);
      
      // Reset image state
      if (currentMode === 'file') {
        fileInput.value = '';
        fileUploadSection.classList.add('active');
      } else {
        webcamSection.classList.add('active');
      }
      
      previewSection.classList.remove('active');
      hasImage = false;
      updateUploadButtonState();
    })
    .catch(err => {
      console.error('Upload error:', err);
      hideProgress();
      showError(`Upload failed: ${err.message}`);
    });
  }

  function updateUploadButtonState() {
    const noun = nounSelect.value;
    const itemId = itemSelect.value;
    
    uploadBtn.disabled = !noun || !itemId || !hasImage;
  }

  // Status message functions
  function showInfo(message) {
    statusMessage.textContent = `ℹ️ ${message}`;
    statusBar.className = 'info';
  }
  
  function showSuccess(message) {
    statusMessage.textContent = `✅ ${message}`;
    statusBar.className = 'success';
    
    // Auto clear after delay
    setTimeout(() => {
      if (statusBar.className === 'success') {
        statusMessage.textContent = '';
        statusBar.className = '';
      }
    }, 5000);
  }
  
  function showWarning(message) {
    statusMessage.textContent = `⚠️ ${message}`;
    statusBar.className = 'warning';
  }
  
  function showError(message) {
    statusMessage.textContent = `❌ ${message}`;
    statusBar.className = 'error';
  }
  
  function showProgress() {
    progressBar.style.display = 'block';
    progressInner.style.width = '90%'; // Indeterminate progress
  }
  
  function hideProgress() {
    progressBar.style.display = 'none';
    progressInner.style.width = '0';
  }
});