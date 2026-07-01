// frontend/runlog/components/RefCheckboxGrid.jsx — a ReferenceList checkbox grid (port of the
// override/adverb checkbox-grid). Fetches /conjunction/reference_options for (noun, params), renders
// a checkbox per option with Select all / None, and reports the selected values via onChange(label, []).
// Shared by the Overrides (S10) and Adverbs (S11) tabs.
import { useEffect, useState } from "react";
import { refOptions } from "../api.js";

export function RefCheckboxGrid({ project, noun, params, label, onChange }) {
  const [options, setOptions] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const key = JSON.stringify({ project, noun, params });

  useEffect(() => {
    let live = true;
    setOptions(null);
    setSelected(new Set());
    refOptions(project, noun, params)
      .then((o) => { if (live) setOptions(o); })
      .catch(() => { if (live) setOptions([]); });
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const emit = (set) => { if (onChange) onChange(label, [...set]); };
  const toggle = (val, on) => setSelected((s) => { const n = new Set(s); if (on) n.add(val); else n.delete(val); emit(n); return n; });
  const all = (on) => { const n = on ? new Set((options || []).map((o) => String(o.value))) : new Set(); setSelected(n); emit(n); };

  if (options === null) return <div className="muted">Loading…</div>;
  if (!options.length) return <div className="muted">No options found for {noun}</div>;

  return (
    <>
      <div className="checkbox-grid">
        {options.map((opt) => (
          <label className="cb" key={opt.value}>
            <input type="checkbox" checked={selected.has(String(opt.value))}
                   onChange={(e) => toggle(String(opt.value), e.target.checked)} />
            <span>{opt.label}</span>
          </label>
        ))}
      </div>
      <div className="mini-tools">
        <button type="button" className="mini" onClick={() => all(true)}>Select all</button>
        <button type="button" className="mini" onClick={() => all(false)}>None</button>
      </div>
    </>
  );
}
