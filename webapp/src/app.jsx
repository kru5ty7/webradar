import ReactDOM from "react-dom/client";
import { useEffect, useRef, useState } from "react";
import "./App.css";
import PlayerCard from "./components/PlayerCard";
import Radar from "./components/Radar";
import { getLatency, Latency } from "./components/latency";
import MaskedIcon from "./components/maskedicon";
import EspOverlay from "./components/EspOverlay";

/* change this to '1' if you want to use offline (your own pc only) */
const USE_LOCALHOST = 0;

/* you can get your public ip from https://ipinfo.io/ip */
const PUBLIC_IP = "192.168.31.17".trim();
const PORT = 22006;

/*
 * For ngrok: set VITE_WS_URL in a .env file or when starting vite, e.g.:
 *   VITE_WS_URL=wss://xxxx-xxxx.ngrok-free.app/cs2_webradar
 * This tells the frontend where the WebSocket ngrok tunnel is.
 */
const NGROK_WS_URL = import.meta.env.VITE_WS_URL || null;

const EFFECTIVE_IP = USE_LOCALHOST ? "localhost" : window.location.hostname;

// Detect mode from URL param
const _urlMode = new URLSearchParams(window.location.search).get("mode");
const IS_MINIMAP = _urlMode === "minimap";
const IS_ESP     = _urlMode === "esp";
const IS_OVERLAY = IS_MINIMAP || IS_ESP;

// Force transparent document background for ESP mode
if (IS_ESP) {
  document.documentElement.style.background = "transparent";
  document.body.style.background = "transparent";
}


const DEFAULT_SETTINGS = {
  dotSize: 1,
  bombSize: 0.5,
  showAllNames: false,
  showEnemyNames: true,
  showViewCones: false,
  showSmoke: true,
  showMolly: true,
  showFlash: true,
  showCallouts: true,
  bombColor: "#ff4500",
  bombHighlight: true,
  showDeathCross: true,
  showDropped: true,
  droppedOpacity: 1,
  autoUpdate: true,
};

// Always merge saved settings with defaults so new keys are never undefined
const loadSettings = () => {
  try {
    const saved = localStorage.getItem("radarSettings");
    return saved ? { ...DEFAULT_SETTINGS, ...JSON.parse(saved) } : DEFAULT_SETTINGS;
  } catch {
    return DEFAULT_SETTINGS;
  }
};

// Module-level WS ref so settings panels can send messages to the Python backend
let _backendWs = null;

// Byte-rate tracking for the speed indicator
let _bytesThisSec = 0;
let _kbpsSnapshot = 0;
setInterval(() => { _kbpsSnapshot = _bytesThisSec / 1024; _bytesThisSec = 0; }, 1000);
function sendToBackend(data) {
  if (_backendWs && _backendWs.readyState === WebSocket.OPEN) {
    _backendWs.send(JSON.stringify(data));
  }
}

// ── Settings popup for overlay mode ──────────────────────────────────────────
// Custom toggle — no <input type="checkbox"> to avoid React controlled-input
// fighting in WebView2. Plain div with onClick, visual state driven by `checked`.
const SettingRow = ({ label, checked, onToggle }) => (
  <div onClick={onToggle} style={{
    display:"flex", justifyContent:"space-between", alignItems:"center",
    padding:"7px 0", borderBottom:"1px solid rgba(255,255,255,0.05)",
    cursor:"pointer", userSelect:"none",
  }}>
    <span style={{ color:"#8ab", fontSize:12 }}>{label}</span>
    <div style={{
      width:28, height:16, borderRadius:8, flexShrink:0,
      background: checked ? "#4ade80" : "rgba(255,255,255,0.18)",
      position:"relative", transition:"background 0.15s",
    }}>
      <div style={{
        position:"absolute", width:12, height:12, borderRadius:"50%",
        background:"#fff", top:2, transition:"left 0.15s",
        left: checked ? 14 : 2,
      }} />
    </div>
  </div>
);

const BOMB_PRESETS = ["#ff4500","#ffdd00","#ffffff","#00cfff","#c90b0b"];

const OverlaySettingsPopup = ({ settings, setSettings, onClose }) => {
  const toggle = (key) => setSettings(s => ({ ...s, [key]: !s[key] }));
  const setVal  = (key, val) => setSettings(s => ({ ...s, [key]: val }));

  return (
    <div style={{
      position:"absolute", inset:0, zIndex:99999,
      background:"rgba(7,18,28,0.97)",
      display:"flex", flexDirection:"column",
      padding:"10px 12px", overflowY:"auto",
    }}>
      {/* Header */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8 }}>
        <span style={{ color:"#b1d0e7", fontWeight:700, fontSize:13, letterSpacing:"0.05em" }}>
          Settings
        </span>
        <span onClick={onClose} style={{ color:"#f55", cursor:"pointer", fontSize:18, lineHeight:1, padding:"0 2px" }}>×</span>
      </div>

      {/* Sliders — uncontrolled (defaultValue) + onInput for live updates */}
      {[
        { label:"Dot Size",        key:"dotSize",       min:0.5, max:2,   step:0.1 },
        { label:"Bomb Size",       key:"bombSize",      min:0.1, max:2,   step:0.1 },
        { label:"Dropped Opacity", key:"droppedOpacity",min:0.1, max:1,   step:0.05, fmt: v => Math.round(v*100)+"%" },
      ].map(({ label, key, min, max, step, fmt }) => (
        <div key={key} style={{ marginBottom:8 }}>
          <div style={{ display:"flex", justifyContent:"space-between", marginBottom:3 }}>
            <span style={{ color:"#8ab", fontSize:12 }}>{label}</span>
            <span id={`lbl-${key}`} style={{ color:"#b1d0e7", fontSize:12, fontFamily:"monospace" }}>
              {fmt ? fmt(settings[key] ?? 1) : (settings[key] ?? 1).toFixed(1) + "x"}
            </span>
          </div>
          <input type="range" min={min} max={max} step={step}
            defaultValue={settings[key]}
            onInput={e => {
              const v = parseFloat(e.target.value);
              document.getElementById(`lbl-${key}`).textContent = fmt ? fmt(v) : v.toFixed(1) + "x";
              setVal(key, v);
            }}
            style={{ width:"100%", accentColor:"#4ade80", cursor:"pointer" }} />
        </div>
      ))}

      {/* Toggles */}
      <SettingRow label="Ally Names"  checked={!!settings.showAllNames}   onToggle={() => toggle("showAllNames")} />
      <SettingRow label="Enemy Names" checked={!!settings.showEnemyNames} onToggle={() => toggle("showEnemyNames")} />
      <SettingRow label="View Cones"  checked={!!settings.showViewCones}  onToggle={() => toggle("showViewCones")} />
      <SettingRow label="Smoke"       checked={!!settings.showSmoke}      onToggle={() => toggle("showSmoke")} />
      <SettingRow label="Molotov"     checked={!!settings.showMolly}      onToggle={() => toggle("showMolly")} />
      <SettingRow label="Flash"       checked={!!settings.showFlash}      onToggle={() => toggle("showFlash")} />
      <SettingRow label="Callouts"    checked={!!settings.showCallouts}   onToggle={() => toggle("showCallouts")} />
      <SettingRow label="Death Cross"    checked={!!settings.showDeathCross} onToggle={() => toggle("showDeathCross")} />
      <SettingRow label="Bomb Pulse"     checked={!!settings.bombHighlight}  onToggle={() => toggle("bombHighlight")} />
      <SettingRow label="Dropped Weapons" checked={!!(settings.showDropped ?? true)} onToggle={() => toggle("showDropped")} />
      <SettingRow label="Auto Update"    checked={!!(settings.autoUpdate ?? true)} onToggle={() => toggle("autoUpdate")} />

      {/* Bomb color */}
      <div style={{ marginTop:8 }}>
        <span style={{ color:"#8ab", fontSize:12, display:"block", marginBottom:6 }}>Bomb Color</span>
        <div style={{ display:"flex", gap:6, flexWrap:"wrap", alignItems:"center" }}>
          {BOMB_PRESETS.map(c => (
            <div key={c} onClick={() => setVal("bombColor", c)}
              style={{ width:20, height:20, borderRadius:"50%", background:c, cursor:"pointer",
                border: settings.bombColor===c ? "2px solid #fff" : "2px solid transparent" }} />
          ))}
          <input type="color" defaultValue={settings.bombColor ?? "#ff4500"}
            onInput={e => setVal("bombColor", e.target.value)}
            style={{ width:20, height:20, padding:0, border:"none", borderRadius:"50%",
              cursor:"pointer", background:"none" }} />
        </div>
      </div>
    </div>
  );
};

// ── Drag bar for overlay mode ─────────────────────────────────────────────────
const DragBar = ({ bombData, onSettingsClick }) => {
  const onMouseDown = (e) => {
    if (e.button !== 0) return;
    const api = window.pywebview?.api;
    if (!api) return;

    let originX = null;
    let originY = null;

    const onMove = async (me) => {
      if (originX === null) {
        try {
          const pos = await api.get_position?.();
          originX = pos ? pos[0] : 10;
          originY = pos ? pos[1] : 10;
        } catch { originX = 10; originY = 10; }
      }
      api.move(originX + (me.screenX - e.screenX), originY + (me.screenY - e.screenY));
    };

    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const hasBomb = bombData && bombData.m_blow_time > 0 && !bombData.m_is_defused;

  return (
    <div onMouseDown={onMouseDown} style={{
      height: 24, background:"rgba(0,0,0,0.6)",
      display:"flex", alignItems:"center", justifyContent:"space-between",
      padding:"0 8px", cursor:"grab", flexShrink:0,
      borderBottom:"1px solid rgba(255,255,255,0.08)",
    }}>
      <span style={{ color:"rgba(255,255,255,0.45)", fontSize:10, letterSpacing:"0.1em" }}>
        CS2 RADAR
      </span>

      {hasBomb && (
        <span style={{ color:bombData.m_is_defusing?"#4fc":"#f84",
          fontSize:11, fontFamily:"monospace", fontWeight:700 }}>
          {bombData.m_blow_time.toFixed(1)}s
          {bombData.m_is_defusing && ` (${bombData.m_defuse_time.toFixed(1)}s)`}
        </span>
      )}

      <div onMouseDown={e => e.stopPropagation()}
        style={{ display:"flex", alignItems:"center", gap:8 }}>
        {/* Settings gear */}
        <span onClick={onSettingsClick} title="Settings"
          style={{ color:"rgba(255,255,255,0.5)", fontSize:13, cursor:"pointer", lineHeight:1 }}
          onMouseEnter={e => e.target.style.color="#fff"}
          onMouseLeave={e => e.target.style.color="rgba(255,255,255,0.5)"}>
          ⚙
        </span>
        {/* Close */}
        <span onClick={() => window.pywebview?.api?.close()}
          style={{ color:"rgba(255,255,255,0.4)", fontSize:14, cursor:"pointer", lineHeight:1 }}
          onMouseEnter={e => e.target.style.color="#f55"}
          onMouseLeave={e => e.target.style.color="rgba(255,255,255,0.4)"}>
          ×
        </span>
      </div>
    </div>
  );
};

const App = () => {
  const [averageLatency, setAverageLatency] = useState(0);
  const [playerArray, setPlayerArray] = useState([]);
  const [mapData, setMapData] = useState();
  const lastMapRef = useRef(null);
  const [localTeam, setLocalTeam] = useState();
  const [bombData, setBombData] = useState();
  const [grenades, setGrenades] = useState([]);
  const [dropped, setDropped]   = useState([]);
  const [viewMatrix, setViewMatrix] = useState([]);
  const [settings, setSettings] = useState(loadSettings());
  const [serverAddr, setServerAddr] = useState(null);
  const [tailscaleAddr, setTailscaleAddr] = useState(null);
  const [funnelAddr, setFunnelAddr] = useState(null);
  const [kbps, setKbps] = useState(0);
  const [bannerOpened, setBannerOpened] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Persist settings and sync auto-update preference to backend
  useEffect(() => {
    localStorage.setItem("radarSettings", JSON.stringify(settings));
    sendToBackend({ type: "set_auto_update", value: !!(settings.autoUpdate ?? true) });
  }, [settings]);

  useEffect(() => {
    const wsUrl = NGROK_WS_URL
      || (USE_LOCALHOST
          ? `ws://localhost:${PORT}/cs2_webradar`
          : window.location.protocol === 'https:'
            ? `wss://${window.location.host}/cs2_webradar`
            : `ws://${EFFECTIVE_IP}:${PORT}/cs2_webradar`);

    let ws = null;
    let retryTimer = null;
    let alive = true;

    const connect = () => {
      if (!alive) return;
      try {
        ws = new WebSocket(wsUrl);
        _backendWs = ws;
      } catch (e) {
        console.error("WS init failed:", e);
        retryTimer = setTimeout(connect, 3000);
        return;
      }

      ws.onopen = () => console.info("WS connected →", wsUrl);

      ws.onclose = () => {
        console.warn("WS closed — retrying in 3 s");
        if (alive) retryTimer = setTimeout(connect, 3000);
      };

      ws.onerror = (e) => console.error("WS error", e);

      ws.onmessage = async (event) => {
        setAverageLatency(getLatency());
        const raw = typeof event.data === "string" ? event.data : await event.data.text();
        const parsedData = JSON.parse(raw);
        setPlayerArray(parsedData.m_players);
        setLocalTeam(parsedData.m_local_team);
        setBombData(parsedData.m_bomb);
        setGrenades(parsedData.m_grenades || []);
        setDropped(parsedData.m_dropped   || []);
        if (parsedData.m_view_matrix?.length === 16)
          setViewMatrix(parsedData.m_view_matrix);
        _bytesThisSec += raw.length;
        setKbps(parseFloat(_kbpsSnapshot.toFixed(1)));
        if (parsedData.m_server_ip && parsedData.m_http_port) {
          const port = parsedData.m_http_port;
          setServerAddr(`http://${parsedData.m_server_ip}:${port}`);
          if (parsedData.m_tailscale_ip)
            setTailscaleAddr(`http://${parsedData.m_tailscale_ip}:${port}`);
          if (parsedData.m_funnel_url)
            setFunnelAddr(parsedData.m_funnel_url);
        }

        const map = parsedData.m_map;
        if (map !== "invalid" && map !== lastMapRef.current) {
          lastMapRef.current = map;
          try {
            const res = await fetch(`data/${map}/data.json`);
            if (res.ok) {
              setMapData({ ...(await res.json()), name: map });
              document.body.style.backgroundImage = IS_OVERLAY
                ? "none"
                : `url(./data/${map}/background.png)`;
            } else {
              console.warn(`No map data for "${map}" (${res.status})`);
            }
          } catch (e) {
            console.warn(`Failed to load map data for "${map}":`, e);
          }
        }
      };
    };

    connect();
    return () => {
      alive = false;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);



  // ── ESP overlay mode ─────────────────────────────────────────────────────
  if (IS_ESP) {
    return (
      <div style={{
        width: "100vw", height: "100vh",
        background: "transparent",
        overflow: "hidden",
        position: "fixed", top: 0, left: 0,
      }}>
        <EspOverlay
          playerArray={playerArray}
          localTeam={localTeam}
          viewMatrix={viewMatrix}
          bombData={bombData}
          settings={settings}
        />
      </div>
    );
  }

  // ── Minimap overlay mode ──────────────────────────────────────────────────
  if (IS_MINIMAP) {
    return (
      <div style={{
        width: "100vw", height: "100vh",
        display: "flex", flexDirection: "column",
        background: "rgba(10, 20, 30, 0.92)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 6,
        overflow: "hidden",
        userSelect: "none",
        position: "relative",
      }}>

        {/* Settings popup — renders over everything when open */}
        {settingsOpen && (
          <OverlaySettingsPopup
            settings={settings}
            setSettings={setSettings}
            onClose={() => setSettingsOpen(false)}
          />
        )}

        {/* ── Drag bar ── */}
        <DragBar bombData={bombData} onSettingsClick={() => setSettingsOpen(o => !o)} />

        {/* ── Radar ── */}
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          {mapData && playerArray.length > 0 ? (
            <Radar
              playerArray={playerArray}
              radarImage={`./data/${mapData.name}/radar.png`}
              mapData={mapData}
              localTeam={localTeam}
              averageLatency={averageLatency}
              bombData={bombData}
              grenades={grenades}
              dropped={dropped}
              settings={settings}
            />
          ) : (
            <div style={{
              height: "100%", display: "flex", alignItems: "center",
              justifyContent: "center", color: "rgba(255,255,255,0.4)",
              fontSize: 12,
            }}>
              Waiting for game data…
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Normal radar mode ─────────────────────────────────────────────────────
  return (
    <div className="w-screen h-screen flex flex-col"
      style={{
        background: `radial-gradient(50% 50% at 50% 50%, rgba(20, 40, 55, 0.95) 0%, rgba(7, 20, 30, 0.95) 100%)`,
        backdropFilter: `blur(7.5px)`,
      }}
    >
      <div className={`w-full h-full flex flex-col justify-center overflow-hidden relative`}>
        {bombData && bombData.m_blow_time > 0 && !bombData.m_is_defused && (
          <div className={`absolute left-1/2 top-2 flex-col items-center gap-1 z-50`}>
            <div className={`flex justify-center items-center gap-1`}>
              <MaskedIcon
                path={`./assets/icons/c4_sml.png`}
                height={32}
                color={
                  (bombData.m_is_defusing &&
                    bombData.m_blow_time - bombData.m_defuse_time > 0 &&
                    `bg-radar-green`) ||
                  (bombData.m_blow_time - bombData.m_defuse_time < 0 &&
                    `bg-radar-red`) ||
                  `bg-radar-secondary`
                }
              />
              <span>{`${bombData.m_blow_time.toFixed(1)}s ${(bombData.m_is_defusing &&
                `(${bombData.m_defuse_time.toFixed(1)}s)`) ||
                ""
                }`}</span>
            </div>
          </div>
        )}

        {/* Latency/settings overlay — absolutely positioned, not in flex flow */}
        <Latency
          value={averageLatency}
          settings={settings}
          setSettings={setSettings}
        />

        <div className={`flex items-center justify-evenly w-full h-full overflow-hidden`}>
          <ul id="terrorist" className="lg:flex hidden flex-col justify-center gap-2 m-0 p-0 shrink-0 overflow-hidden max-h-full">
            {playerArray
              .filter((player) => player.m_team == 2)
              .map((player) => (
                <PlayerCard
                  right={false}
                  key={player.m_idx}
                  playerData={player}
                />
              ))}
          </ul>

          {(playerArray.length > 0 && mapData && (
            <Radar
              playerArray={playerArray}
              radarImage={`./data/${mapData.name}/radar.png`}
              mapData={mapData}
              localTeam={localTeam}
              averageLatency={averageLatency}
              bombData={bombData}
              grenades={grenades}
              dropped={dropped}
              settings={settings}
            />
          )) || (
            <div id="radar" className="relative flex items-center justify-center">
              <h1 className="radar_message">
                Connected! Waiting for data from usermode
              </h1>
            </div>
          )}

          <ul
            id="counterTerrorist"
            className="lg:flex hidden flex-col justify-center gap-2 m-0 p-0 shrink-0 overflow-hidden max-h-full"
          >
            {playerArray
              .filter((player) => player.m_team == 3)
              .map((player) => (
                <PlayerCard
                  right={true}
                  key={player.m_idx}
                  playerData={player}
                  settings={settings}
                />
              ))}
          </ul>
        </div>
      </div>

      {/* Network address pill — bottom-left, click row to copy */}
      {!IS_OVERLAY && serverAddr && (
        <div style={{
          position: "fixed", bottom: 10, left: 12,
          display: "flex", flexDirection: "column", gap: 4,
          background: "rgba(0,0,0,0.55)", border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 6, padding: "6px 10px", userSelect: "none", zIndex: 50,
        }}>
          {[
            { label: "LAN",       url: serverAddr,    color: "#7ec8e3" },
            ...(tailscaleAddr ? [{ label: "TAILSCALE", url: tailscaleAddr, color: "#9f7aea" }] : []),
            ...(funnelAddr    ? [{ label: "FUNNEL",    url: funnelAddr,    color: "#68d391" }] : []),
          ].map(({ label, url, color }) => (
            <div key={label}
              title="Click to copy"
              onClick={() => navigator.clipboard?.writeText(url)}
              style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
            >
              <span style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", letterSpacing: "0.08em", width: 62, textAlign: "right" }}>
                {label}
              </span>
              <span style={{ fontSize: 11, color, fontFamily: "monospace" }}>
                {url}
              </span>
            </div>
          ))}
          <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", marginTop: 3, paddingTop: 3, textAlign: "right" }}>
            <span style={{ fontSize: 9, color: "rgba(255,255,255,0.28)", fontFamily: "monospace" }}>
              ↓ {kbps.toFixed(1)} KB/s
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;
