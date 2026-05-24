import { useEffect, useRef, useCallback } from "react";

// Bone connections mirrored from cs2-external-esp Bones.hpp
// indices: pelvis=1, spine_1=3, spine_2=4, chest=23, neck=6, head=7,
//          shoulder_L=9, elbow_L=10, hand_L=11,
//          shoulder_R=13, elbow_R=14, hand_R=15,
//          hip_L=17, knee_L=18, foot_heel_L=19,
//          hip_R=20, knee_R=21, foot_heel_R=22
const BONE_CONNECTIONS = [
  [1, 3], [3, 4], [4, 23], [23, 6], [6, 7],
  [6, 9], [9, 10], [10, 11],
  [6, 13], [13, 14], [14, 15],
  [1, 17], [17, 18], [18, 19],
  [1, 20], [20, 21], [21, 22],
];

function wts(m, x, y, z, W, H) {
  const view = m[12] * x + m[13] * y + m[14] * z + m[15];
  if (view <= 0.01) return null;
  const sx = W / 2 + (m[0] * x + m[1] * y + m[2] * z + m[3]) / view * (W / 2);
  const sy = H / 2 - (m[4] * x + m[5] * y + m[6] * z + m[7]) / view * (H / 2);
  if (sx < -200 || sx > W + 200 || sy < -200 || sy > H + 200) return null;
  return [sx, sy];
}

function hpColor(hp) {
  const t = hp / 100;
  return `rgb(${Math.round(255 * (1 - t))},${Math.round(200 * t + 55)},50)`;
}

export default function EspOverlay({ playerArray, localTeam, viewMatrix, bombData, settings }) {
  const canvasRef = useRef(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const W = canvas.width = window.innerWidth;
    const H = canvas.height = window.innerHeight;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, W, H);

    if (!viewMatrix?.length || !playerArray?.length) return;
    const m = viewMatrix;

    for (const p of playerArray) {
      if (p.m_is_local || p.m_is_dead) continue;

      const { x, y, z } = p.m_position;
      const feet = wts(m, x, y, z, W, H);
      const head = wts(m, x, y, z + 70, W, H);
      if (!feet || !head) continue;

      const isEnemy = p.m_team !== localTeam;
      const boxColor = isEnemy ? "rgba(255,80,80,0.9)" : "rgba(80,220,80,0.9)";
      const skelColor = isEnemy ? "rgba(255,140,140,0.7)" : "rgba(140,255,140,0.7)";

      const height = feet[1] - head[1];
      if (height < 4) continue;
      const width = height / 2.4;
      const bx = head[0] - width / 2;
      const by = head[1];

      // Bounding box
      ctx.strokeStyle = boxColor;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(bx, by, width, height);

      // Health bar — left side
      const hp = Math.max(0, Math.min(100, p.m_health));
      const filled = height * (hp / 100);
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(bx - 5, by, 3, height);
      ctx.fillStyle = hpColor(hp);
      ctx.fillRect(bx - 5, by + (height - filled), 3, filled);

      // Armor bar — bottom edge
      if (p.m_armor > 0) {
        const armorFill = width * (p.m_armor / 100);
        ctx.fillStyle = "rgba(0,0,0,0.45)";
        ctx.fillRect(bx, by + height + 3, width, 3);
        ctx.fillStyle = "rgba(120,140,255,0.85)";
        ctx.fillRect(bx, by + height + 3, armorFill, 3);
      }

      // Name
      ctx.font = "bold 12px monospace";
      ctx.textAlign = "center";
      ctx.fillStyle = "rgba(255,255,255,0.9)";
      ctx.fillText(p.m_name, head[0], by - 4);

      // Active weapon
      const wname = p.m_weapons?.m_active || "";
      if (wname) {
        ctx.font = "11px monospace";
        ctx.fillStyle = "rgba(200,200,200,0.8)";
        ctx.fillText(wname, head[0], by + height + 14);
      }

      // Skeleton (bones array from payload, if present)
      if (p.m_bones?.length >= 23) {
        ctx.strokeStyle = skelColor;
        ctx.lineWidth = 1;
        for (const [a, b] of BONE_CONNECTIONS) {
          const ba = p.m_bones[a];
          const bb = p.m_bones[b];
          if (!ba || !bb) continue;
          const sa = wts(m, ba.x, ba.y, ba.z, W, H);
          const sb = wts(m, bb.x, bb.y, bb.z, W, H);
          if (!sa || !sb) continue;
          ctx.beginPath();
          ctx.moveTo(sa[0], sa[1]);
          ctx.lineTo(sb[0], sb[1]);
          ctx.stroke();
        }
      }
    }

    // Bomb world marker
    if (bombData?.m_blow_time > 0 && !bombData?.m_is_defused) {
      const bm = wts(m, bombData.x, bombData.y, bombData.z ?? 0, W, H);
      if (bm) {
        const label = `BOMB ${bombData.m_blow_time.toFixed(1)}s` +
          (bombData.m_is_defusing ? ` (${bombData.m_defuse_time.toFixed(1)}s)` : "");
        ctx.font = "bold 13px monospace";
        ctx.textAlign = "center";
        ctx.fillStyle = bombData.m_is_defusing ? "rgba(80,255,180,1)" : "rgba(255,120,30,1)";
        ctx.fillText(label, bm[0], bm[1] - 10);
      }
    }
  }, [playerArray, localTeam, viewMatrix, bombData]);

  useEffect(() => { draw(); }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: "fixed",
        top: 0, left: 0,
        width: "100vw", height: "100vh",
        pointerEvents: "none",
        background: "transparent",
      }}
    />
  );
}
