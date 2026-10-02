import { useState, useEffect, useCallback, useRef } from "react";
import "./App.css";

const BASE_URL   = "http://localhost:8000";
const MACHINE_ID = "EX-04";
const POLL_MS    = 2000;

async function apiGet(path) {
  const r = await fetch(`${BASE_URL}${path}`);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

const ALERT_META = {
  proximity: { icon: "📡", label: "Proximity" },
  tilt:      { icon: "📐", label: "Tilt"      },
  engine:    { icon: "🌡️", label: "Engine"    },
  fatigue:   { icon: "😴", label: "Fatigue"   },
};

const SCENARIOS = [
  { id: "multi_alert",       label: "⚡ Multi-Alert",       cls: "btn-danger",  hint: "4 concurrent risks" },
  { id: "tilt_critical",     label: "📐 Tilt Critical",     cls: "btn-danger",  hint: "28° tilt"           },
  { id: "proximity_warning", label: "📡 Proximity Warning", cls: "btn-warning", hint: "2.3 m obstacle"     },
  { id: "engine_overload",   label: "🌡️ Engine Overload",  cls: "btn-orange",  hint: "112°C / 96% load"   },
  { id: "fatigue_high",      label: "😴 Fatigue High",      cls: "btn-purple",  hint: "Score 85/100"       },
  { id: "all_clear",         label: "✅ Reset / All Clear", cls: "btn-neutral", hint: "Nominal readings"   },
];

function sevClass(score) {
  if (score == null) return "sev-clear";
  if (score >= 80)   return "sev-critical";
  if (score >= 50)   return "sev-high";
  if (score >= 25)   return "sev-medium";
  return "sev-low";
}

function sevLabel(score) {
  if (score == null) return "—";
  if (score >= 80)   return "CRITICAL";
  if (score >= 50)   return "HIGH";
  if (score >= 25)   return "MEDIUM";
  return "LOW";
}

function barColor(score) {
  if (score >= 80) return "#ef4444";
  if (score >= 50) return "#f59e0b";
  if (score >= 25) return "#fb923c";
  return "#475569";
}

function Header({ online }) {
  return (
    <header className="cp-header">
      <div className="cp-header-left">
        <span className="cp-header-icon">🏗️</span>
        <div>
          <div className="cp-title">Alert Arbitration Co-Pilot</div>
          <div className="cp-subtitle">Excavator {MACHINE_ID} · Heavy Machinery Safety</div>
        </div>
      </div>
      <div className={`cp-status ${online ? "online" : "offline"}`}>
        <span className="cp-dot" />
        <span>{online ? "Edge Online" : "Connecting…"}</span>
      </div>
    </header>
  );
}

function OfflineBanner() {
  return (
    <div className="cp-offline-banner">
      <span className="cp-banner-icon">⚠️</span>
      <div>
        <strong>Backend not connected</strong>
        <p>Start the FastAPI server: <code>uvicorn main:app --port 8000</code></p>
      </div>
    </div>
  );
}

function InstructionCard({ instruction }) {
  const score    = instruction?.severity ?? null;
  const hasAlert = instruction?.alert_type && score != null && score > 5;
  const cls      = hasAlert ? sevClass(score) : "sev-clear";
  const meta     = hasAlert ? (ALERT_META[instruction.alert_type] || { icon: "⚠️", label: instruction.alert_type }) : null;
  const isLLM    = instruction?.source === "llm";
  const text     = instruction?.instruction_text || "All clear — no active alerts";

  return (
    <div className={`cp-instruction-card ${cls}`}>
      <div className="cp-instr-header">
        <span className="cp-instr-type">
          {meta ? `${meta.icon} ${meta.label.toUpperCase()} ALERT` : "🟢 SYSTEM STATUS"}
        </span>
        <div className="cp-instr-badges">
          {hasAlert && (
            <span className="cp-badge cp-badge-sev">
              {sevLabel(score)} · {Math.round(score)}/100
            </span>
          )}
          {instruction?.source && (
            <span className={`cp-badge ${isLLM ? "cp-badge-llm" : "cp-badge-fallback"}`}>
              {isLLM ? "🤖 Groq LLM" : "📋 Fallback Template"}
            </span>
          )}
        </div>
      </div>
      <div className="cp-instr-text">{text}</div>
      {hasAlert && (
        <div className="cp-instr-footer">
          Machine {MACHINE_ID} · Severity: {Math.round(score)}/100
        </div>
      )}
    </div>
  );
}

function SensorRow({ reading }) {
  const sensors = [
    {
      icon: "📡", label: "Proximity",
      value: reading?.proximity != null ? `${reading.proximity.toFixed(1)}` : "—",
      unit: "m", sub: "Distance to obstacle",
    },
    {
      icon: "🌡️", label: "Engine",
      value: reading?.engine_temp != null ? `${Math.round(reading.engine_temp)}°C` : "—",
      unit: "", sub: reading?.engine_load != null ? `Load: ${Math.round(reading.engine_load)}%` : "Temp / Load",
    },
    {
      icon: "😴", label: "Fatigue",
      value: reading?.fatigue != null ? `${Math.round(reading.fatigue)}` : "—",
      unit: "/100", sub: "Operator fatigue score",
    },
  ];

  return (
    <div className="cp-sensor-row">
      {sensors.map((s) => (
        <div key={s.label} className="cp-sensor-card">
          <div className="cp-sensor-label">{s.icon} {s.label}</div>
          <div className="cp-sensor-value">
            {s.value}
            {s.value !== "—" && s.unit && <span className="cp-sensor-unit">{s.unit}</span>}
          </div>
          <div className="cp-sensor-sub">{s.sub}</div>
        </div>
      ))}
    </div>
  );
}

function SuppressedQueue({ queue }) {
  const suppressed = queue?.suppressed_alerts ?? [];
  return (
    <div className="cp-queue-card">
      <div className="cp-queue-header">
        <span className="cp-queue-title">🗂 Suppressed Alert Queue</span>
        <span className="cp-queue-count">{suppressed.length} held</span>
        <span className="cp-queue-hint">Re-evaluated on every sensor update</span>
      </div>
      {suppressed.length === 0 ? (
        <div className="cp-queue-empty">No suppressed alerts — queue is empty</div>
      ) : (
        <ul className="cp-queue-list">
          {suppressed.map((a, i) => {
            const m = ALERT_META[a.alert_type] || { icon: "⚠️", label: a.alert_type };
            const s = a.adjusted_score ?? 0;
            return (
              <li key={`${a.alert_type}-${i}`} className="cp-queue-item">
                <div className="cp-qi-left">
                  <span className="cp-qi-icon">{m.icon}</span>
                  <div>
                    <div className="cp-qi-name">{m.label}</div>
                    <div className="cp-qi-sub">Score {Math.round(s)}/100 · Suppressed by higher-priority alert</div>
                  </div>
                </div>
                <div className="cp-qi-right">
                  <div className="cp-bar-wrap">
                    <div className="cp-bar-fill" style={{ width: `${s}%`, background: barColor(s) }} />
                  </div>
                  <span className="cp-qi-score">{Math.round(s)}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function FleetFooter({ summary, lastUpdated }) {
  const n = (v) => (v ?? 0).toLocaleString();
  return (
    <div className="cp-footer">
      <div className="cp-footer-stats">
        <div className="cp-fstat">
          <span className="cp-fstat-val">{n(summary?.total_alerts_processed)}</span>
          <span className="cp-fstat-lbl">Total Processed</span>
        </div>
        <div className="cp-fdivider" />
        <div className="cp-fstat">
          <span className="cp-fstat-val">{n(summary?.alerts_shown)}</span>
          <span className="cp-fstat-lbl">Shown to Operator</span>
        </div>
        <div className="cp-fdivider" />
        <div className="cp-fstat">
          <span className="cp-fstat-val">{n(summary?.alerts_suppressed)}</span>
          <span className="cp-fstat-lbl">Suppressed</span>
        </div>
        {summary?.by_alert_type &&
          Object.entries(summary.by_alert_type).map(([type, cnt]) => (
            <div key={type} style={{ display: "contents" }}>
              <div className="cp-fdivider" />
              <div className="cp-fstat">
                <span className="cp-fstat-val">{n(cnt)}</span>
                <span className="cp-fstat-lbl">{type}</span>
              </div>
            </div>
          ))}
      </div>
      <div className="cp-footer-time">
        {lastUpdated ? `Last updated: ${lastUpdated}` : "Waiting for data…"}
      </div>
    </div>
  );
}

function ControlPanel({ onTrigger, loading }) {
  return (
    <div className="cp-control">
      <div className="cp-control-header">
        <span>🎛️</span>
        <span className="cp-control-title">Demo Control Panel</span>
        <span className="cp-control-machine">Machine: {MACHINE_ID}</span>
      </div>
      <div className="cp-control-body">
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            className={`cp-btn ${s.cls}`}
            onClick={() => onTrigger(s.id)}
            disabled={!!loading}
            title={s.hint}
          >
            {loading === s.id && <span className="cp-spinner" />}
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [online,      setOnline]      = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const [instruction, setInstruction] = useState(null);
  const [queue,       setQueue]       = useState(null);
  const [summary,     setSummary]     = useState(null);
  const [lastReading, setLastReading] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [loading,     setLoading]     = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      await apiGet("/health");
      setOnline(true);
      setBackendDown(false);
    } catch {
      setOnline(false);
      setBackendDown(true);
      return;
    }
    const [q, instr, sum] = await Promise.allSettled([
      apiGet(`/alerts/${MACHINE_ID}/queue`),
      apiGet(`/alerts/${MACHINE_ID}/instruction`),
      apiGet("/fleet/summary"),
    ]);
    if (q.status     === "fulfilled") setQueue(q.value);
    if (instr.status === "fulfilled") setInstruction(instr.value);
    if (sum.status   === "fulfilled") setSummary(sum.value);
    setLastUpdated(new Date().toLocaleTimeString());
  }, []);

  const pollRef = useRef(null);
  useEffect(() => {
    fetchAll();
    pollRef.current = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [fetchAll]);

  const handleTrigger = useCallback(async (scenarioId) => {
    setLoading(scenarioId);
    try {
      const result = await apiPost("/sensors/simulate", {
        machine_id: MACHINE_ID,
        scenario: scenarioId,
      });
      if (result.synthetic_reading) setLastReading(result.synthetic_reading);
      const [q, instr, sum] = await Promise.allSettled([
        apiGet(`/alerts/${MACHINE_ID}/queue`),
        apiGet(`/alerts/${MACHINE_ID}/instruction`),
        apiGet("/fleet/summary"),
      ]);
      if (q.status     === "fulfilled") setQueue(q.value);
      if (instr.status === "fulfilled") setInstruction(instr.value);
      if (sum.status   === "fulfilled") setSummary(sum.value);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      console.error("Trigger failed:", err);
    } finally {
      setLoading(null);
    }
  }, []);

  return (
    <div className="cp-app">
      <Header online={online} />
      <main className="cp-main">
        {backendDown && <OfflineBanner />}
        <p className="cp-section-label">🔴 Active Operator Instruction</p>
        <InstructionCard instruction={instruction} />
        <p className="cp-section-label">📊 Latest Sensor Readings</p>
        <SensorRow reading={lastReading} />
        <SuppressedQueue queue={queue} />
        <FleetFooter summary={summary} lastUpdated={lastUpdated} />
        <p className="cp-section-label">🎛 Demo Control Panel</p>
        <ControlPanel onTrigger={handleTrigger} loading={loading} />
      </main>
    </div>
  );
}