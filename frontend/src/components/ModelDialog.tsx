import { RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type AvailableModel, type ModelSelection } from "../api";

interface Props {
  current: ModelSelection;
  onSave: (selection: ModelSelection) => Promise<void>;
  onClose: () => void;
}

export function ModelDialog({ current, onSave, onClose }: Props) {
  const [modelId, setModelId] = useState(current.model_id);
  const [region, setRegion] = useState(current.region);
  const [models, setModels] = useState<AvailableModel[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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
  const save = async () => {
    setError(""); setSaving(true);
    try { await onSave({ model_id: modelId.trim(), region: region.trim() }); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };

  const india = models.filter((model) => model.id.startsWith("in."));
  const other = models.filter((model) => !model.id.startsWith("in."));
  const selected = models.some((model) => model.id === modelId) ? modelId : "";
  const option = (model: AvailableModel) => (
    <option key={`${model.kind}:${model.id}`} value={model.id}>{model.name} — {model.id}{model.status !== "ACTIVE" ? ` (${model.status})` : ""}</option>
  );

  return (
    <div className="overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="dialog model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-title">
        <header><h2 id="model-title">Bedrock model</h2><button className="ghost icon-only" aria-label="Close" onClick={onClose}><X size={18} /></button></header>
        <p className="muted small">Choose a model for your next message. Availability and access depend on your AWS account.</p>
        <label>Bedrock region
          <input value={region} disabled={loading} onChange={(event) => { setRegion(event.target.value); setModels([]); setWarnings([]); }} placeholder="ap-south-1" />
        </label>
        <div className="model-discover"><span>Discover models and inference profiles in this region.</span><button type="button" onClick={() => void load()} disabled={!region.trim() || loading}><RefreshCw size={14} className={loading ? "spin" : ""} /> {loading ? "Loading…" : "Load models"}</button></div>
        {warnings.map((warning) => <p className="notice" key={warning}>{warning} You can still enter a model ID.</p>)}
        {models.length > 0 && <label>Available models and profiles
          <select value={selected} onChange={(event) => setModelId(event.target.value)}>
            <option value="">Select a model or enter an ID below</option>
            {india.length > 0 && <optgroup label="India inference profiles">{india.map(option)}</optgroup>}
            {other.length > 0 && <optgroup label="Other models and profiles">{other.map(option)}</optgroup>}
          </select>
        </label>}
        {models.length === 0 && !loading && <p className="muted small">Load the list, or enter an ID directly.</p>}
        <label>Model or inference profile ID
          <input value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder="in.provider.model-id" autoComplete="off" />
        </label>
        <p className="muted small">India geographic profiles use the <code>in.</code> prefix. Global profiles may route requests outside India. Some listed profiles may not support chat or streaming.</p>
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="dialog-actions"><button onClick={onClose}>Cancel</button><button className="primary" onClick={() => void save()} disabled={!modelId.trim() || !region.trim() || saving}>{saving ? "Saving…" : "Use model"}</button></div>
      </div>
    </div>
  );
}
