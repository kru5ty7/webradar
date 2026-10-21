import { useEffect, useRef, useState } from "react";

// In-page colour chooser. The native <input type="color"> opens an OS popup the
// page can't position, so it ran off the screen (and out of the small minimap
// window). This one opens inline, under the swatches, inside the scrollable
// settings panel: hue / saturation / brightness sliders plus a hex field.

const hexToHsv = (hex) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return null;
  const n = parseInt(m[1], 16);
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
  }
  return { h, s: max ? d / max : 0, v: max };
};

const hsvToHex = ({ h, s, v }) => {
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] :
    h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  const to = (f) => Math.round((f + m) * 255).toString(16).padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
};

const STYLE = `
  .cf-range { -webkit-appearance: none; appearance: none; width: 100%; height: 10px;
              border-radius: 5px; outline: none; margin: 0; cursor: pointer; }
  .cf-range::-webkit-slider-thumb { -webkit-appearance: none; width: 14px; height: 14px;
              border-radius: 50%; background: #fff; border: 2px solid #1b2a38; }
  .cf-range::-moz-range-thumb { width: 12px; height: 12px; border-radius: 50%;
              background: #fff; border: 2px solid #1b2a38; }
`;

const ColorField = ({ value, onChange, size = 20 }) => {
  const [open, setOpen] = useState(false);
  const [hsv, setHsv] = useState(() => hexToHsv(value) || { h: 0, s: 0, v: 1 });
  const [text, setText] = useState(value || "");
  const panelRef = useRef(null);

  // Bring the opened chooser fully into view inside the scrolling panel.
  useEffect(() => {
    if (open) panelRef.current?.scrollIntoView({ block: "nearest" });
  }, [open]);

  // A preset click (or another tab) changed the value from outside.
  useEffect(() => {
    if (hsvToHex(hsv).toLowerCase() === String(value).toLowerCase()) return;
    const next = hexToHsv(value);
    if (next) setHsv(next);
    setText(value || "");
  }, [value]);

  const apply = (next) => {
    setHsv(next);
    const hex = hsvToHex(next);
    setText(hex);
    onChange(hex);
  };

  // Plain function (not a component) so it isn't remounted on every change mid-drag.
  const slider = (label, max, step, field, track) => (
    <label key={field} style={{ display: "block", marginBottom: 6 }}>
      <span style={{ fontSize: 10, opacity: 0.6, display: "block", marginBottom: 2 }}>{label}</span>
      <input
        type="range" className="cf-range" min={0} max={max} step={step}
        value={hsv[field]}
        onChange={(e) => apply({ ...hsv, [field]: parseFloat(e.target.value) })}
        style={{ background: track }}
      />
    </label>
  );

  return (
    <>
      <style>{STYLE}</style>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Custom colour"
        style={{
          width: size, height: size, borderRadius: "50%", padding: 0, cursor: "pointer", flexShrink: 0,
          background: "conic-gradient(red, yellow, lime, cyan, blue, magenta, red)",
          border: open ? "2px solid #fff" : "2px solid rgba(255,255,255,0.25)",
        }}
      />
      {open && (
        <div ref={panelRef} style={{
          flexBasis: "100%", width: "100%", boxSizing: "border-box", marginTop: 4,
          padding: "8px 10px", borderRadius: 8, background: "rgba(255,255,255,0.05)",
          border: "1px solid rgba(255,255,255,0.1)",
        }}>
          {slider("Hue", 360, 1, "h",
            "linear-gradient(to right, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00)")}
          {slider("Saturation", 1, 0.01, "s",
            `linear-gradient(to right, ${hsvToHex({ h: hsv.h, s: 0, v: hsv.v })}, ${hsvToHex({ h: hsv.h, s: 1, v: hsv.v })})`)}
          {slider("Brightness", 1, 0.01, "v",
            `linear-gradient(to right, #000, ${hsvToHex({ h: hsv.h, s: hsv.s, v: 1 })})`)}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 22, height: 22, borderRadius: 4, flexShrink: 0, background: hsvToHex(hsv),
                           border: "1px solid rgba(255,255,255,0.3)" }} />
            <input
              type="text" value={text} spellCheck={false} maxLength={7}
              onChange={(e) => {
                const t = e.target.value;
                setText(t);
                const parsed = /^#[0-9a-f]{6}$/i.test(t) ? hexToHsv(t) : null;
                if (parsed) { setHsv(parsed); onChange(t.toLowerCase()); }
              }}
              style={{ flex: 1, minWidth: 0, fontFamily: "monospace", fontSize: 12, padding: "3px 6px",
                       borderRadius: 4, border: "1px solid rgba(255,255,255,0.2)",
                       background: "rgba(0,0,0,0.3)", color: "#e8eef2" }}
            />
          </div>
        </div>
      )}
    </>
  );
};

export default ColorField;
