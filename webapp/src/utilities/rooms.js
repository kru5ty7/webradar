// Maps made of separate arenas (Rush: rush_001) list them in data.json as
// `rooms`: each has its own radar image (radar_<name>.png), its own x / y /
// scale, and the world-space `bounds` of the floor drawn on that image.

// Whether `pos` falls on the room's (1024 px square) image. With no room the
// whole map is shown, so everything does.
export const isShown = (room, pos) => {
  if (!room) return true;
  if (!pos) return false;
  const size = 1024 * room.scale;
  return pos.x >= room.x && pos.x <= room.x + size && pos.y <= room.y && pos.y >= room.y - size;
};

const distanceToFloor = ({ bounds: b }, pos) =>
  Math.hypot(Math.max(b.minX - pos.x, 0, pos.x - b.maxX), Math.max(b.minY - pos.y, 0, pos.y - b.maxY));

// The room `pos` is in. Players can stand off the drawn floor, so any spot on a
// room's image counts; where images overlap, the nearer drawn floor wins.
const roomAt = (rooms, pos) =>
  rooms
    .filter((room) => pos && isShown(room, pos))
    .sort((a, b) => distanceToFloor(a, pos) - distanceToFloor(b, pos))[0] ?? null;

// The room holding the most of `players`, or null if none of them is in one.
const busiestRoom = (rooms, players) => {
  let best = null;
  let bestCount = 0;
  const occupied = players.map((p) => roomAt(rooms, p.m_position));
  for (const room of rooms) {
    const count = occupied.filter((r) => r === room).length;
    if (count > bestCount) {
      best = room;
      bestCount = count;
    }
  }
  return best;
};

// The room to show: the one you are alive in, else the one with the most living
// players (you are dead or spectating), else the one with the most bodies.
export const getActiveRoom = (mapData, players) => {
  const rooms = mapData?.rooms;
  if (!rooms) return null;
  const alive = players.filter((p) => !p.m_is_dead);
  const me = alive.find((p) => p.m_is_local);
  return (
    (me && roomAt(rooms, me.m_position)) ||
    busiestRoom(rooms, alive) ||
    busiestRoom(rooms, players)
  );
};

// Coordinates and image file to draw with: the active room's, or the map's own.
export const getRadarView = (mapData, players) => {
  const room = getActiveRoom(mapData, players);
  if (!room) return { room: null, mapData, image: "radar.png" };
  const { x, y, scale } = room;
  return { room, mapData: { ...mapData, x, y, scale }, image: `radar_${room.name}.png` };
};
