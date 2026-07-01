// frontend/vendor/glide.js — the shared Glide editable-grid vendor chunk (Phase 6).
// Bundled ONCE to static/lib/glide-vendor.js (+ glide-vendor.css) and loaded AFTER vendor.js
// (React) on the grid pages. React is externalized to window.React (see frontend/vendor/shims/*),
// so Glide binds to the SAME single React the rest of the page uses — no second copy, no
// "Invalid hook call". The page's grid bundle (data_grid.js) reads these globals via the shim.
import DataEditor, { GridCellKind, GridColumnIcon, CompactSelection } from "@glideapps/glide-data-grid";
import "@glideapps/glide-data-grid/dist/index.css";

window.GlideDataGrid = { DataEditor, GridCellKind, GridColumnIcon, CompactSelection };
