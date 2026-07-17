#!/usr/bin/env python3
"""
Bridge: avitran cs2-radar (ws://localhost:9001) → webradar webapp (ws://localhost:22006)

avitran sends: JSON array of Player objects
webradar expects: { m_players, m_local_team, m_map, m_bomb }
"""

import asyncio, json, sys, logging
import websockets

SRC_URL  = "ws://localhost:9001"
DST_PORT = 22006
WS_PATH  = "/cs2_webradar"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("bridge")

_clients: set = set()
_latest: str  = json.dumps({"m_players": [], "m_local_team": 0, "m_map": "invalid", "m_bomb": None})


def convert(players: list) -> dict:
    out = []
    local_team = 0
    for i, p in enumerate(players):
        hp   = p.get("health", 0)
        team = p.get("team", 0)
        converted = {
            "m_idx":      i,
            "m_name":     p.get("name", ""),
            "m_health":   hp,
            "m_team":     team,
            "m_is_dead":  hp <= 0,
            "m_is_local": p.get("active_player", False),
            "m_eye_angle": p.get("rotation", 0),
            "m_position": p.get("position", {"x": 0, "y": 0, "z": 0}),
            "m_weapons":  p.get("weapons", []),
            "m_armor":    p.get("armor", 0),
            "m_has_helmet":  p.get("has_helmet", False),
            "m_has_defuser": p.get("has_defuser", False),
        }
        out.append(converted)
        if p.get("active_player") and team in (2, 3):
            local_team = team

    return {
        "m_players":    out,
        "m_local_team": local_team,
        "m_map":        "invalid",   # avitran doesn't expose map name
        "m_bomb":       None,
    }


async def ws_handler(websocket):
    _clients.add(websocket)
    log.info("browser connected  (total: %d)", len(_clients))
    try:
        await websocket.send(_latest)
        await websocket.wait_closed()
    finally:
        _clients.discard(websocket)
        log.info("browser disconnected (total: %d)", len(_clients))


async def reader():
    global _latest
    while True:
        try:
            log.info("connecting to avitran radar at %s ...", SRC_URL)
            async with websockets.connect(SRC_URL) as ws:
                log.info("connected to avitran radar")
                async for raw in ws:
                    try:
                        players = json.loads(raw)
                        if isinstance(players, list):
                            payload = convert(players)
                            _latest = json.dumps(payload)
                            dead = set()
                            for client in _clients:
                                try:
                                    await client.send(_latest)
                                except Exception:
                                    dead.add(client)
                            _clients.difference_update(dead)
                    except Exception as e:
                        log.warning("parse error: %s", e)
        except Exception as e:
            log.warning("avitran connection lost: %s — retrying in 2s", e)
            await asyncio.sleep(2)


async def main():
    log.info("bridge starting — serving webapp WebSocket on port %d", DST_PORT)
    async with websockets.serve(ws_handler, "0.0.0.0", DST_PORT):
        await reader()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped")
