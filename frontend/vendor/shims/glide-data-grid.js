// Shim: `import DataEditor, { GridCellKind } from "@glideapps/glide-data-grid"` in the grid
// bundle resolves here (esbuild alias) instead of node_modules, so Glide is NOT re-bundled —
// it's read from the global set by glide-vendor.js (which binds to window.React).
const G = window.GlideDataGrid;
export default G.DataEditor;
export const { GridCellKind, GridColumnIcon, CompactSelection } = G;
