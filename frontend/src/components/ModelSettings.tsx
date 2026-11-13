import { AlertCircle, Check, CheckCircle2, Loader2, RefreshCw, Zap } from "lucide-react";
import { useState } from "react";
import { api, type AvailableModel, type ModelSelection, type ModelTest } from "../api";

interface Props {
  current: ModelSelection;
  onSave: (selection: ModelSelection) => Promise<void>;
}

/** Settings > Model: the Bedrock model or inference profile for the next message. */
export function ModelSettings({ current, onSave }: Props) {
  const [modelId, setModelId] = useState(current.model_id);
  const [region, setRegion] = useState(current.region);
  const [models, setModels] = useState<AvailableModel[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setError(""); setLoading(true); setWarnings([]);
    try {
      const result = await api.discoverModels(region.trim());
      setModels(result.models);
      setWarnings(result.warnings);
    } catch (e) {
      setModels([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally { setLoading(false); }
  };
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState<ModelTest | "running" | null>(null);
  const runTest = async (id = modelId) => {
    if (!id.trim() || !region.trim()) return;
    setTest("running");
    try { setTest(await api.testModel({ model_id: id.trim(), region: region.trim() })); }
    catch (e) { setTest({ ok: false, code: "Error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const save = async () => {
    setError(""); setSaving(true); setSaved(false);
    try { await onSave({ model_id: modelId.trim(), region: region.trim() }); setSaved(true); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };

  const india = models.filter((model) => model.id.startsWith("in."));
  const other = models.filter((model) => !model.id.startsWith("in."));
  const selected = models.some((model) => model.id === modelId) ? modelId : "";
  const option = (model: AvailableModel) => (
    <option key={`${model.kind}:${model.id}`} value={model.id}>{model.name} · {model.id}{model.status !== "ACTIVE" ? ` (${model.status})` : ""}</option>
  );

  return (
    <div className="model-settings">
        <p className="current-model">In use: <code>{current.model_id}</code> · {current.region}</p>
        <p className="muted small">Choose a model for your next message. Availability and access depend on your AWS account.</p>
        <label>Bedrock region
          <input value={region} disabled={loading} onChange={(event) => { setRegion(event.target.value); setModels([]); setWarnings([]); }} placeholder="ap-south-1" />
        </label>
        <div className="model-discover"><span>Discover models and inference profiles in this region.</span><button type="button" onClick={() => void load()} disabled={!region.trim() || loading}><RefreshCw size={14} className={loading ? "spin" : ""} /> {loading ? "Loading…" : "Load models"}</button></div>
        {warnings.map((warning) => <p className="notice" key={warning}>{warning} You can still enter a model ID.</p>)}
        {models.length > 0 && <label>Available models and profiles
          <select value={selected} onChange={(event) => { setModelId(event.target.value); if (event.target.value) void runTest(event.target.value); }}>
            <option value="">Select a model or enter an ID below</option>
            {india.length > 0 && <optgroup label="India inference profiles">{india.map(option)}</optgroup>}
            {other.length > 0 && <optgroup label="Other models and profiles">{other.map(option)}</optgroup>}
          </select>
        </label>}
        {models.length === 0 && !loading && <p className="muted small">Load the list, or enter an ID directly.</p>}
        <label>Model or inference profile ID
          <input value={modelId} onChange={(event) => { setModelId(event.target.value); setTest(null); }} placeholder="in.provider.model-id" autoComplete="off" />
        </label>
        <p className="muted small">India geographic profiles use the <code>in.</code> prefix. Global profiles may route requests outside India. Some listed profiles may not support chat or streaming.</p>
        {error && <p className="error-text" role="alert">{error}</p>}
        <ModelTestResult test={test} />
        <div className="pane-actions">
          {saved && <span className="muted small"><Check size={13} /> Saved</span>}
          <button onClick={() => void runTest()} disabled={!modelId.trim() || !region.trim() || test === "running"}>
            {test === "running" ? <Loader2 size={14} className="spin" /> : <Zap size={14} />} Test model
          </button>
          <button className="primary" onClick={() => void save()} disabled={!modelId.trim() || !region.trim() || saving}>{saving ? "Saving…" : "Use model"}</button>
        </div>
    </div>
  );
}

function ModelTestResult({ test }: { test: ModelTest | "running" | null }) {
  if (!test) return null;
  if (test === "running") {
    return <p className="model-test running" role="status"><Loader2 size={14} className="spin" /> Asking the model for a few tokens…</p>;
  }
  if (test.ok) {
    return (
      <p className="model-test ok" role="status">
        <CheckCircle2 size={14} /> Available. First response in {test.first_token_s}s
        {test.reply ? <> · replied <code>{test.reply}</code></> : null}.
      </p>
    );
  }
  return (
    <p className="model-test bad" role="alert">
      <AlertCircle size={14} /> <span><strong>{test.code}</strong>{test.message ? `: ${test.message}` : ""}</span>
    </p>
  );
}
