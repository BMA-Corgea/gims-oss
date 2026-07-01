// Shim: `import ... from "react"` in page bundles resolves here (esbuild alias) instead of
// node_modules/react, so React is NOT re-bundled — it's read from the global set by vendor.js.
// (Names absent in the installed React version simply export `undefined`, which is harmless.)
const R = window.React;
export default R;
export const {
  Children, Component, Fragment, Profiler, PureComponent, StrictMode, Suspense,
  cloneElement, createContext, createElement, createRef, forwardRef, isValidElement,
  lazy, memo, startTransition, useCallback, useContext, useDebugValue, useDeferredValue,
  useEffect, useId, useImperativeHandle, useInsertionEffect, useLayoutEffect, useMemo,
  useReducer, useRef, useState, useSyncExternalStore, useTransition, version,
} = R;
