// frontend/pages/camera.jsx — Image Capture (Phase 6 React; tool pages T5).
// React port of the 521-line vanilla camera.js: attach an image to a noun instance — pick project /
// entity type / item, choose a source (file drag-drop/browse OR webcam capture), preview, and upload.
// Reuses camera.css (the .control-panel/.capture-section/.file-drop-area/.active/.drag-over + #id
// contract is reproduced: #projectDropdown/#nounSelect/#itemSelect/#fileInput/#video/#canvas/#preview…).
//
// Upload mutation (multipart): POST /camera/upload/{project}/{noun}  FormData { item_id, run_id?, file }.
// Under the app-shell the orchestrate wrapper sidecar-logs the multipart envelope (serialized form
// fields) then performs the real upload — cameragshot.py asserts that envelope ({item_id, run_id?, file}).
// run_id comes from the URL ?run= (deep link); ?project=/?noun= seed the initial selection.
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const notify = (m, k = "info") => toast(m, k === "error" || k === "warn" ? "err" : k === "success" ? "ok" : k);

function Camera() {
  const params = new URLSearchParams(location.search);
  const urlNoun = params.get("noun");
  const runID = params.get("run");

  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [nouns, setNouns] = useState([]);
  const [noun, setNoun] = useState("");
  const [items, setItems] = useState([]);     // string ids
  const [itemId, setItemId] = useState("");
  const [mode, setMode] = useState("file");   // file | webcam
  const [hasImage, setHasImage] = useState(false);
  const [previewSrc, setPreviewSrc] = useState("");
  const [progress, setProgress] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const fileRef = useRef(null);
  const firstInit = useRef(true);

  // ── init: projects → first/url project ──
  useEffect(() => {
    fetchJSON("/camera/projects").then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      if (!list.length) { setProjects([]); notify("No projects available", "warn"); return; }
      setProjects(list);
      const urlProject = params.get("project");
      setProject(list.includes(urlProject) ? urlProject : list[0]);
    }).catch(() => { setProjects([]); notify("Failed to load projects", "error"); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // project change → load noun types
  useEffect(() => {
    if (!project) return;
    setNoun(""); setItems([]); setItemId(""); setHasImage(false); setPreviewSrc("");
    fetchJSON(`/camera/project/${enc(project)}/noun_types`).then((nt) => {
      const types = Object.keys(nt || {});
      setNouns(types);
      if (!types.length) { notify("No nouns found in this project", "warn"); return; }
      const useUrl = firstInit.current && urlNoun && types.includes(urlNoun);
      firstInit.current = false;
      setNoun(useUrl ? urlNoun : types[0]);
    }).catch(() => { setNouns([]); notify("Failed to load nouns", "error"); });
  }, [project]); // eslint-disable-line react-hooks/exhaustive-deps

  // noun change → load items (+ primary-id field from schema)
  useEffect(() => {
    if (!project || !noun) { setItems([]); setItemId(""); return; }
    let live = true;
    (async () => {
      try {
        const its = await fetchJSON(`/camera/project/${enc(project)}/noun/${enc(noun)}/items`).catch(() => []);
        if (!Array.isArray(its) || !its.length) { if (live) { setItems([]); setItemId(""); notify(`No ${noun} items found. You may need to create some first.`, "warn"); } return; }
        const schema = await fetchJSON(`/camera/project/${enc(project)}/noun_types/${enc(noun)}`).catch(() => ({}));
        const pidField = schema.primary_id_field || `${noun.toLowerCase()}_id`;
        const ids = its.map((it) => it[pidField]).filter((x) => x != null).map(String);
        if (live) { setItems(ids); setItemId(""); notify(`Loaded ${its.length} items`, "info"); }
      } catch (e) { if (live) { setItems([]); notify(`Failed to load items: ${e.message || e}`, "error"); } }
    })();
    return () => { live = false; };
  }, [project, noun]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── webcam lifecycle ──
  const startWebcam = async () => {
    try { const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }); if (videoRef.current) videoRef.current.srcObject = stream; notify("Camera activated", "info"); }
    catch { notify("Could not access camera", "error"); setMode("file"); }
  };
  const stopWebcam = () => { const v = videoRef.current; if (v && v.srcObject) { v.srcObject.getTracks().forEach((t) => t.stop()); v.srcObject = null; } };
  useEffect(() => { if (mode === "webcam") startWebcam(); else stopWebcam(); return () => stopWebcam(); }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleMode = () => { setHasImage(false); setPreviewSrc(""); setMode((m) => (m === "file" ? "webcam" : "file")); };

  const snap = () => {
    const v = videoRef.current, c = canvasRef.current; if (!v || !c) return;
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    setPreviewSrc(c.toDataURL("image/jpeg", 0.85)); setHasImage(true); notify("Photo captured. Ready to upload.", "info");
  };
  const retake = () => { setHasImage(false); setPreviewSrc(""); notify("Photo discarded. Capture a new image.", "info"); };

  const handleFile = (file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) { notify("Please select an image file", "error"); return; }
    const reader = new FileReader();
    reader.onload = (e) => { setPreviewSrc(e.target.result); setHasImage(true); notify("Image selected. Ready to upload.", "info"); };
    reader.readAsDataURL(file);
  };

  const upload = async () => {
    if (!noun) { notify("Please select a noun type", "error"); return; }
    if (!itemId) { notify("Please select an item", "error"); return; }
    if (!hasImage) { notify("Please capture or select an image first", "error"); return; }
    const fd = new FormData();
    fd.append("item_id", itemId);
    if (runID) fd.append("run_id", runID);
    let file;
    if (mode === "file" && fileRef.current && fileRef.current.files.length) file = fileRef.current.files[0];
    else {
      const c = canvasRef.current;
      file = await new Promise((res) => c.toBlob((blob) => { const ts = new Date().toISOString().replace(/[:.]/g, ""); res(new File([blob], `webcam_capture_${ts}.jpg`, { type: "image/jpeg" })); }, "image/jpeg", 0.85));
    }
    fd.append("file", file);
    setProgress(true);
    try {
      await fetchJSON(`/camera/upload/${enc(project)}/${enc(noun)}`, { method: "POST", body: fd });
      notify("Image uploaded successfully!", "success");
      if (mode === "file" && fileRef.current) fileRef.current.value = "";
      setHasImage(false); setPreviewSrc("");
    } catch (e) { notify(`Upload failed: ${e.message || e}`, "error"); }
    finally { setProgress(false); }
  };

  const showSection = (s) => mode === s && !hasImage;
  const uploadDisabled = !noun || !itemId || !hasImage;

  return (
    <>
      <section className="panel cam-toolbar">
        <div className="panel-head"><Icon name="camera" /><span className="panel-title">Capture target</span></div>
        <div className="panel-body">
          <div id="project-info" className="cam-project">
            <label className="field cam-project-field"><span className="field-label">Project</span>
              <select id="projectDropdown" className="input select project-dropdown" value={project} onChange={(e) => setProject(e.target.value)}>
                {projects == null ? <option value="">Loading projects...</option> : !projects.length ? <option value="">No projects available</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
              </select></label>
            {urlNoun ? <span className="chip accent cam-context">Noun: {urlNoun}</span> : null}
            {runID ? <span className="chip cam-context">Run: {runID}</span> : null}
          </div>
        </div>
      </section>

      <section className="panel control-panel">
        <div className="selection-controls">
          <label className="field control-group"><span className="field-label">Entity Type</span>
            <select id="nounSelect" className="input select input-field" value={noun} onChange={(e) => setNoun(e.target.value)}>
              {!nouns.length ? <option value="" disabled>No nouns available</option> : nouns.map((n) => <option key={n} value={n}>{n}</option>)}
            </select></label>
          <label className="field control-group"><span className="field-label">Select Item</span>
            <select id="itemSelect" className="input select input-field" value={itemId} onChange={(e) => setItemId(e.target.value)}>
              <option value="" disabled>{items.length ? "Pick an item" : "Select a noun first"}</option>
              {items.map((id) => <option key={id} value={id}>{id}</option>)}
            </select></label>
          <div className="control-group button-group"><span className="field-label">Source</span>
            <button id="toggleModeBtn" className="btn ghost secondary-button" onClick={toggleMode}>
              <Icon name={mode === "file" ? "webcam" : "folder"} /> {mode === "file" ? "Switch to Webcam" : "Switch to File Upload"}
            </button>
          </div>
        </div>

        <div className="capture-area">
          <div id="fileUploadSection" className={"capture-section" + (showSection("file") ? " active" : "")}>
            <div className={"file-drop-area" + (dragOver ? " drag-over" : "")}
                 onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                 onDragLeave={() => setDragOver(false)}
                 onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) { if (fileRef.current) fileRef.current.files = e.dataTransfer.files; handleFile(e.dataTransfer.files[0]); } }}>
              <svg className="icon file-drop-icon"><use href="/static/icons.svg#i-image" /></svg>
              <span className="file-msg">Drag &amp; drop image or click to browse</span>
              <input type="file" accept="image/*" id="fileInput" ref={fileRef} className="file-input" aria-label="Choose an image file" onChange={(e) => handleFile(e.target.files[0])} />
            </div>
          </div>

          <div id="webcamSection" className={"capture-section" + (showSection("webcam") ? " active" : "")}>
            <video id="video" ref={videoRef} autoPlay playsInline />
            <button id="snapBtn" className="btn-primary primary-button" onClick={snap}><Icon name="camera" /> Capture Photo</button>
          </div>

          <div id="previewSection" className={"capture-section" + (hasImage ? " active" : "")}>
            <div className="preview-container">
              <img id="preview" alt="Selected or captured image preview" src={previewSrc || undefined} />
              <button id="retakeBtn" className="btn ghost secondary-button" title="Retake" aria-label="Retake image" onClick={retake}><Icon name="rotate" /></button>
            </div>
          </div>
        </div>

        <div className="action-buttons">
          <button id="uploadBtn" className="btn-primary primary-button" disabled={uploadDisabled} onClick={upload}><Icon name="upload" /> Upload Image</button>
          <div id="progress-bar" style={{ display: progress ? "block" : "none" }}><div id="progress-inner" style={{ width: progress ? "90%" : "0" }} /></div>
        </div>
      </section>

      <canvas id="canvas" ref={canvasRef} className="hidden" />
      <div id="status-bar" role="status" aria-live="polite"><div id="status-message" /></div>
    </>
  );
}

mountOnAuth("camera-root", (host) => createRoot(host).render(<Camera />));
