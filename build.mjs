// build.mjs — esbuild bundler for the React front-end (Phase 6 full React migration).
//
// Emits TWO kinds of bundle to static/lib/ (committed; FastAPI serves them, no node at serve time):
//   • vendor.js          — the shared React + ReactDOM chunk, bundled ONCE. Loaded before any page.
//   • <page>.js          — one per frontend/pages/*.jsx, with React EXTERNALIZED to the vendor
//                          globals via frontend/vendor/shims/* (so pages don't re-embed React).
// A React page's node loads `vendor.js` then its `<page>.js` (deferred, in order).
//
//   node build.mjs            # one-shot production build (minified)
//   node build.mjs --watch    # rebuild on change (dev)
//   node build.mjs --dev      # unminified + sourcemaps
import * as esbuild from "esbuild";
import { readdirSync } from "node:fs";
import path from "node:path";

const watch = process.argv.includes("--watch");
const dev = process.argv.includes("--dev") || watch;

const shimDir = path.resolve("frontend/vendor/shims");
// page bundles resolve these specifiers to the global-reading shims instead of node_modules
const pageAlias = {
  "react": path.join(shimDir, "react.js"),
  "react-dom": path.join(shimDir, "react-dom.js"),
  "react-dom/client": path.join(shimDir, "react-dom-client.js"),
  "react/jsx-runtime": path.join(shimDir, "react-jsx-runtime.js"),
};
// the grid bundle additionally externalizes Glide to the window global set by glide-vendor.js
const gridAlias = { ...pageAlias, "@glideapps/glide-data-grid": path.join(shimDir, "glide-data-grid.js") };

const common = {
  bundle: true,
  format: "iife",
  jsx: "automatic",
  loader: { ".jsx": "jsx" },
  target: ["es2020"],
  minify: !dev,
  sourcemap: dev ? "inline" : false,
  legalComments: "none",
  logLevel: "info",
  define: { "process.env.NODE_ENV": dev ? '"development"' : '"production"' },
};

const pageEntries = readdirSync("frontend/pages")
  .filter((f) => f.endsWith(".jsx"))
  .map((f) => `frontend/pages/${f}`);

// vendor: the REAL react (no alias) → window globals
const vendorOpts = { ...common, entryPoints: ["frontend/vendor/index.js"], outfile: "static/lib/vendor.js" };
// glide vendor: the REAL Glide (+ its CSS → glide-vendor.css) → window.GlideDataGrid; React externalized
const glideVendorOpts = { ...common, entryPoints: ["frontend/vendor/glide.js"], outfile: "static/lib/glide-vendor.js", alias: pageAlias };
// pages: react aliased out to the vendor globals
const pagesOpts = { ...common, entryPoints: pageEntries, outdir: "static/lib", entryNames: "[name]", alias: pageAlias };
// grid: the editable Glide grid, dynamically import()-ed (ESM, exports mountDataGrid/defaultEndpoints);
// React + Glide externalized to the vendor globals.
const gridOpts = { ...common, format: "esm", entryPoints: ["frontend/grid/data_grid.js"], outfile: "static/lib/data_grid.js", alias: gridAlias };

if (watch) {
  const ctxs = await Promise.all([vendorOpts, glideVendorOpts, pagesOpts, gridOpts].map((o) => esbuild.context(o)));
  await Promise.all(ctxs.map((c) => c.watch()));
  console.log("[build] watching frontend/** → static/lib/ (vendor + glide-vendor + pages + grid)");
} else {
  await Promise.all([vendorOpts, glideVendorOpts, pagesOpts, gridOpts].map((o) => esbuild.build(o)));
  console.log(`[build] built vendor.js + glide-vendor.js + ${pageEntries.length} page bundle(s) + data_grid.js → static/lib/`);
}
