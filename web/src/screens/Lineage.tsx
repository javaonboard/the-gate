import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import type { AgentEvent } from "../api";

/** Where the call came from.
 *
 *  The crew and the sources they draw on, lighting up as the work happens.
 *  Not decoration — it is the honest answer to "how do you know that?", and
 *  it makes clear which parts are computed and which are reasoned. */

type NodeSpec = {
  key: string;
  label: string;
  sub: string;
  pos: [number, number, number];
  kind: "desk" | "crew" | "source" | "maths";
};

const NODES: NodeSpec[] = [
  { key: "orchestrator", label: "The Gate", sub: "production desk", pos: [0, 0, 0], kind: "desk" },

  { key: "vision", label: "Scripty", sub: "script supervisor", pos: [-5.2, 2.6, 0.4], kind: "crew" },
  { key: "script", label: "Breakdown", sub: "what the scene needs", pos: [-5.6, -0.4, -1.2], kind: "crew" },
  { key: "historian", label: "The Book", sub: "production records", pos: [-3.4, -3.0, 0.8], kind: "crew" },
  { key: "scout", label: "Scout", sub: "location scout", pos: [3.4, 3.0, -0.8], kind: "crew" },
  { key: "compliance", label: "Steward", sub: "union rules", pos: [5.4, 0.2, 1.0], kind: "crew" },
  { key: "planner", label: "1st AD", sub: "makes the call", pos: [2.6, -3.2, -0.6], kind: "crew" },
  { key: "control_room", label: "Video Village", sub: "puts it on screen", pos: [0.4, -5.0, 0.6], kind: "crew" },

  { key: "simulator", label: "The Clock", sub: "10,000 runs · numpy", pos: [0, 3.6, 1.4], kind: "maths" },

  { key: "src_gemini", label: "Gemini", sub: "watches the footage", pos: [-8.6, 4.0, -1.6], kind: "source" },
  { key: "src_clickhouse", label: "ClickHouse", sub: "7.4M rows of history", pos: [-7.2, -5.0, -1.4], kind: "source" },
  { key: "src_parallel", label: "Parallel", sub: "the world outside", pos: [7.2, 5.0, -2.0], kind: "source" },
  { key: "src_grafana", label: "Grafana", sub: "where the crew looks", pos: [3.0, -6.4, -1.8], kind: "source" },
];

const EDGES: [string, string][] = [
  ["vision", "orchestrator"], ["script", "orchestrator"],
  ["historian", "orchestrator"], ["scout", "orchestrator"],
  ["compliance", "orchestrator"], ["planner", "orchestrator"],
  ["control_room", "orchestrator"], ["simulator", "orchestrator"],
  ["src_gemini", "vision"], ["src_gemini", "script"],
  ["src_clickhouse", "historian"], ["historian", "simulator"],
  ["src_parallel", "scout"], ["scout", "simulator"],
  ["compliance", "simulator"], ["simulator", "planner"],
  ["control_room", "src_grafana"],
];

const COLOUR: Record<NodeSpec["kind"], number> = {
  desk: 0xffffff,
  crew: 0x7aa2ff,
  maths: 0xf5a623,
  source: 0x4ade80,
};

// Agents map onto their source so a tool call lights the source too.
const SOURCE_OF: Record<string, string> = {
  historian: "src_clickhouse",
  scout: "src_parallel",
  control_room: "src_grafana",
  vision: "src_gemini",
  script: "src_gemini",
};

export function Lineage({ runId }: { runId: string | null }) {
  const mount = useRef<HTMLDivElement>(null);
  const activity = useRef<Record<string, number>>({});
  const [log, setLog] = useState<AgentEvent[]>([]);

  // live events drive the glow
  useEffect(() => {
    if (!runId) return;
    setLog([]);
    const source = new EventSource(`/api/runs/${runId}/stream`);
    source.addEventListener("agent", (e) => {
      const event = JSON.parse((e as MessageEvent).data) as AgentEvent;
      setLog((prev) => [...prev.slice(-40), event]);
      activity.current[event.agent] = 1;
      const src = SOURCE_OF[event.agent];
      if (src && (event.phase === "tool_call" || event.phase === "tool_result")) {
        activity.current[src] = 1;
      }
    });
    source.onerror = () => source.close();
    return () => source.close();
  }, [runId]);

  useEffect(() => {
    const el = mount.current;
    if (!el) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      42, el.clientWidth / el.clientHeight, 0.1, 200
    );
    camera.position.set(0, -1.5, 20);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);

    const group = new THREE.Group();
    scene.add(group);

    const byKey = new Map<string, THREE.Mesh>();
    const halos = new Map<string, THREE.Mesh>();

    for (const node of NODES) {
      const radius = node.kind === "desk" ? 0.62 : node.kind === "source" ? 0.5 : 0.4;
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 32, 32),
        new THREE.MeshBasicMaterial({ color: COLOUR[node.kind] })
      );
      mesh.position.set(...node.pos);
      group.add(mesh);
      byKey.set(node.key, mesh);

      const halo = new THREE.Mesh(
        new THREE.SphereGeometry(radius * 2.2, 24, 24),
        new THREE.MeshBasicMaterial({
          color: COLOUR[node.kind], transparent: true, opacity: 0,
        })
      );
      halo.position.set(...node.pos);
      group.add(halo);
      halos.set(node.key, halo);
    }

    const lines: { line: THREE.Line; from: string; to: string }[] = [];
    for (const [from, to] of EDGES) {
      const a = NODES.find((n) => n.key === from)!;
      const b = NODES.find((n) => n.key === to)!;
      const geometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...a.pos),
        new THREE.Vector3(...b.pos),
      ]);
      const line = new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({
          color: 0x2a2f3a, transparent: true, opacity: 0.9,
        })
      );
      group.add(line);
      lines.push({ line, from, to });
    }

    // labels as HTML, projected each frame — sharper than sprites
    const labelLayer = document.createElement("div");
    Object.assign(labelLayer.style, {
      position: "absolute", inset: "0", pointerEvents: "none",
    } as CSSStyleDeclaration);
    el.appendChild(labelLayer);

    const labels = NODES.map((node) => {
      const div = document.createElement("div");
      div.innerHTML =
        `<div style="font-size:13px;font-weight:600">${node.label}</div>` +
        `<div style="font-size:11px;color:#8a90a0">${node.sub}</div>`;
      Object.assign(div.style, {
        position: "absolute", transform: "translate(-50%, 14px)",
        textAlign: "center", whiteSpace: "nowrap", color: "#e8eaed",
      } as CSSStyleDeclaration);
      labelLayer.appendChild(div);
      return { node, div };
    });

    let frame = 0;
    let stop = false;

    function tick() {
      if (stop) return;
      frame += 1;
      group.rotation.y = Math.sin(frame / 400) * 0.28;
      group.rotation.x = Math.sin(frame / 620) * 0.1;

      for (const [key, level] of Object.entries(activity.current)) {
        const halo = halos.get(key);
        if (halo) {
          (halo.material as THREE.MeshBasicMaterial).opacity = level * 0.28;
          halo.scale.setScalar(1 + (1 - level) * 0.5);
        }
        activity.current[key] = Math.max(0, level - 0.012);
      }

      for (const { line, from, to } of lines) {
        const hot = (activity.current[from] ?? 0) + (activity.current[to] ?? 0);
        const material = line.material as THREE.LineBasicMaterial;
        material.color.setHex(hot > 0.15 ? 0x7aa2ff : 0x2a2f3a);
        material.opacity = 0.5 + Math.min(0.5, hot);
      }

      renderer.render(scene, camera);

      const half = new THREE.Vector2(el!.clientWidth / 2, el!.clientHeight / 2);
      for (const { node, div } of labels) {
        const v = new THREE.Vector3(...node.pos).applyMatrix4(group.matrixWorld);
        v.project(camera);
        div.style.left = `${half.x + v.x * half.x}px`;
        div.style.top = `${half.y - v.y * half.y}px`;
        const glow = activity.current[node.key] ?? 0;
        div.style.opacity = String(0.55 + glow * 0.45);
      }

      requestAnimationFrame(tick);
    }
    tick();

    const onResize = () => {
      if (!el) return;
      camera.aspect = el.clientWidth / el.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(el.clientWidth, el.clientHeight);
    };
    window.addEventListener("resize", onResize);

    return () => {
      stop = true;
      window.removeEventListener("resize", onResize);
      renderer.dispose();
      el.innerHTML = "";
    };
  }, []);

  return (
    <>
      <h2 style={{ margin: "0 0 4px", fontSize: 22 }}>Where the call came from</h2>
      <p style={{ color: "var(--muted)", marginTop: 0, fontSize: 14, maxWidth: "62ch" }}>
        Blue is the crew, green is where the facts come from, amber is arithmetic —
        the ten thousand runs are computed, not reasoned. Nodes light as they work.
      </p>

      <div
        ref={mount}
        style={{
          position: "relative",
          height: "min(58vh, 520px)",
          marginTop: 14,
          border: "1px solid var(--line)",
          borderRadius: 12,
          background: "radial-gradient(ellipse at 50% 40%, #14161b 0%, #0c0d10 70%)",
          overflow: "hidden",
        }}
      />

      <div className="card" style={{ marginTop: 14 }}>
        <h3>What just happened</h3>
        {log.length === 0 && <div className="empty" style={{ padding: 12 }}>Run a check to see the crew work.</div>}
        {log.slice(-12).reverse().map((e) => (
          <div
            key={e.seq}
            style={{
              display: "flex", gap: 12, padding: "5px 0", fontSize: 13,
              borderBottom: "1px solid var(--line)",
            }}
          >
            <span style={{ fontFamily: "var(--mono)", color: "var(--faint)", minWidth: 52 }}>
              {e.elapsed_ms != null ? `${(e.elapsed_ms / 1000).toFixed(1)}s` : ""}
            </span>
            <b style={{ minWidth: 110 }}>{e.agent_name}</b>
            <span style={{ color: "var(--muted)" }}>{e.message}</span>
          </div>
        ))}
      </div>
    </>
  );
}
