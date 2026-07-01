// frontend/vendor/index.js — the shared React vendor chunk (Phase 6).
// Bundled ONCE to static/lib/vendor.js and loaded before any React page bundle; the page
// bundles externalize React to these globals (see frontend/vendor/shims/*), so they don't
// each re-embed React (~140KB). Glide gets the same treatment when the grid pages migrate.
import React from "react";
import ReactDOM from "react-dom";
import { createRoot, hydrateRoot } from "react-dom/client";
import * as jsxRuntime from "react/jsx-runtime";

window.React = React;
window.ReactDOM = ReactDOM;
window.ReactDOMClient = { createRoot, hydrateRoot };
window.ReactJsxRuntime = jsxRuntime;
