// Helper status, as reported by the Python backend (or "no_helper" when the
// WebSocket itself is down). Green = data flowing, amber = waiting on the game,
// red = something needs fixing.
const COLORS = {
  ok:            "#4ade80",
  starting:      "#fbbf24",
  connecting:    "#fbbf24",
  no_game:       "#fbbf24",
  loading:       "#fbbf24",
  menu:          "#fbbf24",
  refreshing:    "#60a5fa",
  no_helper:     "#f87171",
  no_access:     "#f87171",
  no_players:    "#f87171",
  stale_offsets: "#f87171",
};

// States where re-fetching offsets is the likely fix — the button gets a label.
const OFFSET_TROUBLE = ["stale_offsets", "no_players"];
// No helper to talk to (or it is already refreshing) — hide the button.
const NO_REFRESH = ["connecting", "no_helper", "refreshing"];

const StatusPill = ({ status, compact = false, onRefresh }) => {
  if (!status) return null;
  const color = COLORS[status.state] || "#9ca3af";
  const canRefresh = onRefresh && !NO_REFRESH.includes(status.state);
  const urgent = OFFSET_TROUBLE.includes(status.state);
  return (
    <div
      title={status.detail}
      style={{
        position: "absolute", top: 10, left: 12, zIndex: 50,
        display: "flex", alignItems: "center", gap: 6,
        maxWidth: compact ? "60vw" : "40vw",
        background: "rgba(0,0,0,0.55)", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 6, padding: compact ? "2px 6px" : "4px 10px",
        fontSize: compact ? 10 : 12, color: "rgba(255,255,255,0.8)",
        userSelect: "none", pointerEvents: "auto",
      }}
    >
      <span style={{
        width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
        background: color, boxShadow: `0 0 6px ${color}`,
      }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {status.detail || status.state}
      </span>
      {canRefresh && (
        <button
          onClick={onRefresh}
          title="Re-download offsets and reattach (rate-limited to once per 30 s)"
          style={{
            flexShrink: 0, marginLeft: 2, cursor: "pointer",
            border: urgent ? "1px solid rgba(248,113,113,0.6)" : "none",
            borderRadius: 4, padding: urgent ? "0 6px" : 0,
            background: urgent ? "rgba(248,113,113,0.15)" : "transparent",
            color: urgent ? "#fecaca" : "rgba(255,255,255,0.5)",
            fontSize: "inherit", lineHeight: 1.4,
          }}
        >
          {urgent ? "⟳ Refresh offsets" : "⟳"}
        </button>
      )}
    </div>
  );
};

export default StatusPill;
