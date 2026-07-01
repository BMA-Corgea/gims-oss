// Shim: `import ... from "react-dom"` → the global from vendor.js.
const D = window.ReactDOM;
export default D;
export const { createPortal, flushSync, findDOMNode, render, unmountComponentAtNode } = D;
