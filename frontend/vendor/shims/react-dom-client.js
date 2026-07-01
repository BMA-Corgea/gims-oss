// Shim: `import { createRoot } from "react-dom/client"` → the global from vendor.js.
const C = window.ReactDOMClient;
export const createRoot = C.createRoot;
export const hydrateRoot = C.hydrateRoot;
