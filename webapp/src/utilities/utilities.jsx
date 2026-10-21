// The team that "ally" and "enemy" are measured against. Prefer the local
// player's own entry in the same frame: the payload's m_local_team can lag
// behind it (the helper falls back to the last known team whenever the local
// controller's team reads invalid for a moment, e.g. around a half-time swap
// or a manual team switch), which flipped every ally/enemy colour.
export const getLocalTeam = (players, fallback) => {
  const me = (players || []).find((p) => p.m_is_local && (p.m_team === 2 || p.m_team === 3));
  return me ? me.m_team : fallback;
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
