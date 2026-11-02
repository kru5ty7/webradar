// The team that "ally" and "enemy" are measured against. Prefer the local
// player's own entry in the same frame: the payload's m_local_team can lag
// behind it (the helper falls back to the last known team whenever the local
// controller's team reads invalid for a moment, e.g. around a half-time swap
// or a manual team switch), which flipped every ally/enemy colour.
export const getLocalTeam = (players, fallback) => {
  const me = (players || []).find((p) => p.m_is_local && (p.m_team === 2 || p.m_team === 3));
  return me ? me.m_team : fallback;
};

// Multi-level maps (nuke, vertigo) ship a second radar image for everything
// below `lowerBelow` (z). Maps without it always use the single image.
export const isLowerLevel = (mapData, z) =>
  mapData?.lowerBelow !== undefined && typeof z === "number" && z < mapData.lowerBelow;

// Where `player` stands relative to `ref` (normally you): "above", "below" or null.
// A different level of a stacked map always counts; otherwise it needs at least
// `threshold` units of height difference (a boosted player is ~54-72 above).
export const getHeightRelation = (mapData, player, ref, threshold = 48) => {
  const z = player?.m_position?.z, rz = ref?.m_position?.z;
  if (!ref || player === ref || typeof z !== "number" || typeof rz !== "number") return null;
  const lower = isLowerLevel(mapData, z);
  if (lower !== isLowerLevel(mapData, rz)) return lower ? "below" : "above";
  const dz = z - rz;
  return dz >= threshold ? "above" : dz <= -threshold ? "below" : null;
};

export const getRadarPosition = (mapData, entityCoords) => {
  if (!entityCoords.x || !entityCoords.y) {
    return { x: 0, y: 0 };
  }

  if (!mapData.x || !mapData.y) {
    return { x: 0, y: 0 };
  }

  const position = {
    x: (entityCoords.x - mapData.x) / mapData.scale / 1024,
    y: (((entityCoords.y - mapData.y) / mapData.scale) * -1.0) / 1024,
  };

  return position;
};

export const playerColors = [
  // blue
  "#84c8ed",

  // green
  "#009a7d",

  // yellow
  "#eadd40",

  // orange
  "#df7d29",

  // purple
  "#b72b92",

  // white
  "#ffffff",
];

export const teamEnum = {
  none: 0,
  spectator: 1,
  terrorist: 2,
  counterTerrorist: 3,
};
