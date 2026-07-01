// frontend/runlog/components/GateSignoffModal.jsx — §11.200 electronic-signature modal for gate
// sign-off / reopen (faithful React port of gateSignoffPrompt). Preserves the gs-* ids/classes and
// the EXACT contract: resolves { password, reason } with reason "" → null, or cancels. The password
// field is masked and never logged. Escape / overlay-click / Cancel all cancel; Enter submits.
import { useEffect, useRef, useState } from "react";
import { Icon } from "../../lib/ui.jsx";

export function GateSignoffModal({ isSignoff, onConfirm, onCancel }) {
  const verb = isSignoff ? "Sign off" : "Reopen";
  const passRef = useRef(null);
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onCancel(); };
    document.addEventListener("keydown", onKey);
    const t = setTimeout(() => { if (passRef.current) passRef.current.focus(); }, 0);
    return () => { document.removeEventListener("keydown", onKey); clearTimeout(t); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    if (!password) { setErr("Password is required."); if (passRef.current) passRef.current.focus(); return; }
    onConfirm({ password, reason: reason || null });
  };

  return (
    <div className="gs-overlay" onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="gs-modal" role="dialog" aria-modal="true" aria-label={`${verb}: electronic signature`}>
        <div className="gs-head">
          <span className="gs-lock"><Icon name="lock" /></span>
          <div>
            <div className="gs-title">{verb}: electronic signature</div>
            <div className="gs-sub">Re-enter your password to {verb.toLowerCase()} this gate (21 CFR Part 11 §11.200).</div>
          </div>
        </div>

        <label className="gs-label" htmlFor="gs-reason">Reason (optional)</label>
        <input id="gs-reason" type="text" className="input gs-input" placeholder="e.g. analysis reviewed"
               value={reason} onChange={(e) => setReason(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (passRef.current) passRef.current.focus(); } }} />

        <label className="gs-label" htmlFor="gs-pass">Password</label>
        <input id="gs-pass" ref={passRef} type="password" className="input gs-input" placeholder="••••••••"
               autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }} />

        <div id="gs-err" className="gs-err" role="alert">{err}</div>

        <div className="gs-actions">
          <button id="gs-cancel" type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
          <button id="gs-ok" type="button" className="btn-primary" onClick={submit}>{verb}</button>
        </div>
      </div>
    </div>
  );
}
