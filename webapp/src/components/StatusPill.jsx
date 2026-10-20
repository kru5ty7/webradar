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
  no_helper:     "#f87171",
  no_access:     "#f87171",
  no_players:    "#f87171",
  stale_offsets: "#f87171",
};

const StatusPill = ({ status, compact = false }) => {
  if (!status) return null;
  const color = COLORS[status.state] || "#9ca3af";
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
    </div>
  );
};

export default StatusPill;
