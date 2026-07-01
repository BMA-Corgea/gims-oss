// Shim: the automatic-JSX runtime import (`react/jsx-runtime`) → the global from vendor.js.
const J = window.ReactJsxRuntime;
export const jsx = J.jsx;
export const jsxs = J.jsxs;
export const Fragment = J.Fragment;
