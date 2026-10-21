import { useMemo, useState } from "react";
import { getRadarPosition } from "../utilities/utilities";
import Player from "./player";
import Bomb from "./bomb";
import GrenadeLayer from "./GrenadeLayer";
import CalloutLayer from "./CalloutLayer";
import DroppedItemLayer from "./DroppedItemLayer";

const Radar = ({
  playerArray,
  radarImage,
  mapData,
  localTeam,
  averageLatency,
  bombData,
  grenades,
  dropped,
  settings
}) => {
  // Callback ref — triggers a re-render when the img element mounts,
  // so child components receive the actual DOM element (not null).
  const [imgEl, setRadarImageEl] = useState(undefined);

  // Layers measure the image to place themselves. Zoom is a CSS transform on the
  // wrapper, which would scale getBoundingClientRect(), so they get the
  // untransformed layout size instead.
  const radarImageEl = useMemo(() => {
    if (!imgEl) return undefined;
    const size = () => ({ width: imgEl.offsetWidth, height: imgEl.offsetHeight });
    return {
      get offsetWidth() { return imgEl.offsetWidth; },
      get offsetHeight() { return imgEl.offsetHeight; },
      getBoundingClientRect: size,
    };
  }, [imgEl]);

  const zoom = Math.max(1, settings?.zoom ?? 1);
  const follow = settings?.followMe ?? false;
  const me = follow ? playerArray.find((p) => p.m_is_local && !p.m_is_dead) : null;
  const w = imgEl?.offsetWidth ?? 0;
  const h = imgEl?.offsetHeight ?? 0;

  // Zoom around the middle of the map, or keep the local player centred when
  // following. Clamped so the map edge never leaves the frame.
  let tx = (w - w * zoom) / 2;
  let ty = (h - h * zoom) / 2;
  if (me && w > 0 && mapData) {
    const pos = getRadarPosition(mapData, me.m_position);
    tx = w / 2 - pos.x * w * zoom;
    ty = h / 2 - pos.y * h * zoom;
  }
  tx = Math.min(0, Math.max(w - w * zoom, tx));
  ty = Math.min(0, Math.max(h - h * zoom, ty));
  const zoomed = zoom > 1;

  return (
    <div id="radar" className="relative h-full aspect-square" style={{ overflow: zoomed ? "hidden" : "visible" }}>
      <div
        style={{
          position: "absolute", inset: 0, transformOrigin: "0 0",
          transform: zoomed ? `translate(${tx}px, ${ty}px) scale(${zoom})` : "none",
          transition: "transform 150ms linear",
        }}
      >
      <img ref={setRadarImageEl} className="w-full h-auto" src={radarImage} />

      <CalloutLayer
        mapName={mapData?.name}
        radarImage={radarImageEl}
        enabled={settings?.showCallouts ?? true}
      />

      {playerArray.map((player) => (
        <Player
          key={player.m_idx}
          playerData={player}
          mapData={mapData}
          radarImage={radarImageEl}
          localTeam={localTeam}
          averageLatency={averageLatency}
          settings={settings}
        />
      ))}

      {bombData && bombData.x !== undefined && (
        <Bomb
          bombData={bombData}
          mapData={mapData}
          radarImage={radarImageEl}
          localTeam={localTeam}
          averageLatency={averageLatency}
          settings={settings}
        />
      )}

      <GrenadeLayer
        grenades={grenades}
        mapData={mapData}
        radarImage={radarImageEl}
        settings={settings}
      />

      <DroppedItemLayer
        items={dropped}
        mapData={mapData}
        radarImage={radarImageEl}
        settings={settings}
      />
      </div>
    </div>
  );
};

export default Radar;

