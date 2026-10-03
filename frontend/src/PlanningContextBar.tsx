import { FormEvent, useEffect, useMemo, useState } from "react";
import { CalendarDays, CircleDollarSign, MapPin, SlidersHorizontal, Users, X } from "lucide-react";
import type { FieldEdit, PlanningContextPatch, PlanningContextSummary } from "./types";

export type ContextSection = "where" | "when" | "who" | "budget" | "preferences";

const SECTIONS: Array<{ id: ContextSection; label: string; icon: typeof MapPin }> = [
  { id: "where", label: "Where", icon: MapPin },
  { id: "when", label: "When", icon: CalendarDays },
  { id: "who", label: "Who", icon: Users },
  { id: "budget", label: "Budget", icon: CircleDollarSign },
  { id: "preferences", label: "Preferences", icon: SlidersHorizontal },
];

function currentValue(field: PlanningContextSummary["where"] | undefined): unknown {
  return field?.value;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function sourceLabel(source?: string) {
  return source === "default" ? "系统默认" : source === "derived" ? "推导" : source === "user" ? "已确认" : "未设置";
}

export function clarificationSection(field: string | null | undefined): ContextSection | null {
  if (field === "location") return "where";
  if (["date", "time_window", "departure_at", "return_by"].includes(field ?? "")) return "when";
  if (["party", "child_age"].includes(field ?? "")) return "who";
  if (field === "budget_per_person") return "budget";
  if (["preferences", "diet_tags", "scene_tags", "avoid"].includes(field ?? "")) return "preferences";
  return null;
}

export function PlanningContextBar({
  context,
  busy,
  externalOpen,
  onExternalOpenHandled,
  onSave,
}: {
  context: PlanningContextSummary | null;
  busy: boolean;
  externalOpen: ContextSection | null;
  onExternalOpenHandled: () => void;
  onSave: (patch: PlanningContextPatch) => Promise<void>;
}) {
  const [section, setSection] = useState<ContextSection | null>(null);
  const [where, setWhere] = useState("");
  const [whereClear, setWhereClear] = useState(false);
  const [date, setDate] = useState("");
  const [startAt, setStartAt] = useState("");
  const [endAt, setEndAt] = useState("");
  const [adults, setAdults] = useState(1);
  const [children, setChildren] = useState(0);
  const [childAge, setChildAge] = useState("");
  const [members, setMembers] = useState("");
  const [budgetMode, setBudgetMode] = useState<"unlimited" | "per_person">("unlimited");
  const [budgetAmount, setBudgetAmount] = useState("");
  const [budgetStrict, setBudgetStrict] = useState(false);
  const [preferences, setPreferences] = useState<string[]>([]);
  const [preferenceInput, setPreferenceInput] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);

  const values = useMemo(() => {
    const party = asRecord(currentValue(context?.who));
    const budget = asRecord(currentValue(context?.budget));
    const preferenceGroups = asRecord(currentValue(context?.preferences));
    const savedPreferences = Object.values(preferenceGroups).flatMap((value) => Array.isArray(value) ? value : []);
    return {
      where: typeof currentValue(context?.where) === "string"
        ? String(currentValue(context?.where))
        : String(asRecord(currentValue(context?.where)).address ?? ""),
      date: String(currentValue(context?.when.date) ?? ""),
      startAt: String(currentValue(context?.when.start_at) ?? ""),
      endAt: String(currentValue(context?.when.end_at) ?? ""),
      adults: Number(party.adults ?? 1),
      children: Number(party.children ?? 0),
      childAge: party.child_age == null ? "" : String(party.child_age),
      members: Array.isArray(party.members) ? party.members.map(String).join("、") : "",
      budgetMode: budget.mode === "per_person" ? "per_person" as const : "unlimited" as const,
      budgetAmount: budget.amount == null ? "" : String(budget.amount),
      budgetStrict: Boolean(budget.strict),
      preferences: [...new Set(savedPreferences.map(String))],
    };
  }, [context]);

  function open(next: ContextSection) {
    setSection(next);
    setSaveError("");
    setWhere(values.where);
    setWhereClear(false);
    setDate(values.date);
    setStartAt(values.startAt);
    setEndAt(values.endAt);
    setAdults(values.adults);
    setChildren(values.children);
    setChildAge(values.childAge);
    setMembers(values.members);
    setBudgetMode(values.budgetMode);
    setBudgetAmount(values.budgetAmount);
    setBudgetStrict(values.budgetStrict);
    setPreferences(values.preferences);
    setPreferenceInput("");
  }

  useEffect(() => {
    if (externalOpen) {
      open(externalOpen);
      onExternalOpenHandled();
    }
  // External clarification navigation is an event, not a controlled dialog state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalOpen]);

  function close() {
    if (!saving) setSection(null);
  }

  function fieldEdit<T>(next: string, previous: string): FieldEdit<T> | undefined {
    if (next === previous) return undefined;
    return (next ? { operation: "set", value: next } : { operation: "clear" }) as FieldEdit<T>;
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!section || saving) return;
    const patch: PlanningContextPatch = { base_revision: context?.request_revision ?? 0 };
    if (section === "where") {
      if (whereClear) patch.where = { operation: "clear" };
      else if (where.trim() !== values.where) patch.where = { operation: "set", value: where.trim() };
    } else if (section === "when") {
      const when: NonNullable<PlanningContextPatch["when"]> = {};
      const dateEdit = fieldEdit<string>(date, values.date);
      const startEdit = fieldEdit<string>(startAt, values.startAt);
      const endEdit = fieldEdit<string>(endAt, values.endAt);
      if (dateEdit) when.date = dateEdit;
      if (startEdit) when.start_at = startEdit;
      if (endEdit) when.end_at = endEdit;
      if (Object.keys(when).length) patch.when = when;
    } else if (section === "who") {
      const who: NonNullable<PlanningContextPatch["who"]> = {};
      if (adults !== values.adults) who.adults = adults;
      if (children !== values.children) who.children = children;
      if (childAge !== values.childAge) {
        who.child_age = childAge ? { operation: "set", value: Number(childAge) } : { operation: "clear" };
      }
      if (members !== values.members) {
        who.members = members.trim()
          ? { operation: "set", value: members.split(/[、,，]/).map((item) => item.trim()).filter(Boolean) }
          : { operation: "clear" };
      }
      if (Object.keys(who).length) patch.who = who;
    } else if (section === "budget") {
      patch.budget = budgetMode === "per_person"
        ? { mode: "per_person", amount: Number(budgetAmount), strict: budgetStrict }
        : { mode: "unlimited" };
    } else if (section === "preferences") {
      const added = preferences.filter((item) => !values.preferences.includes(item));
      const removed = values.preferences.filter((item) => !preferences.includes(item));
      if (added.length || removed.length) patch.preferences = { add: added, remove: removed };
    }
    if (Object.keys(patch).length === 1) {
      setSection(null);
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      await onSave(patch);
      setSection(null);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "更新失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  function addPreference() {
    const next = preferenceInput.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
    if (next.length) setPreferences((current) => [...new Set([...current, ...next])]);
    setPreferenceInput("");
  }

  return (
    <div className="planning-context-wrap">
      <nav className="planning-context-bar" aria-label="规划条件">
        {SECTIONS.map(({ id, label, icon: Icon }) => {
          const field = id === "when" ? context?.when.start_at : context?.[id];
          const displayValue = id === "when"
            ? context?.when.date.display_value ?? "选择日期"
            : field?.display_value ?? "设置";
          const pending = clarificationSection(context?.pending_field) === id;
          return <button
            type="button"
            key={id}
            className={`planning-context-trigger ${pending ? "pending" : ""} ${field?.source === "default" ? "assumed" : ""}`}
            aria-label={`修改${label}，当前${displayValue}`}
            aria-haspopup="dialog"
            onClick={() => open(id)}
            disabled={busy}
          >
            <Icon size={15} aria-hidden="true" />
            <span>{label}</span>
            <small>{id === "when" && displayValue === "未设置" ? "选择日期" : displayValue}</small>
          </button>;
        })}
      </nav>
      {section ? (
        <div className="planning-context-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
          <section className="planning-context-dialog" role="dialog" aria-modal="true" aria-labelledby="planning-context-title">
            <header>
              <div>
                <small>规划条件 · {sourceLabel(section === "when" ? context?.when.start_at.source : context?.[section]?.source)}</small>
                <h2 id="planning-context-title">{SECTIONS.find((item) => item.id === section)?.label}</h2>
              </div>
              <button type="button" aria-label="关闭" onClick={close} disabled={saving}><X size={18} /></button>
            </header>
            <form onSubmit={(event) => void save(event)}>
              {section === "where" ? <div className="context-form-field">
                <label htmlFor="context-where">出发地点</label>
                <input id="context-where" value={where} onChange={(event) => { setWhere(event.target.value); setWhereClear(false); }} placeholder="小区、地铁站或附近地标" />
                <button type="button" className="context-secondary" onClick={() => { setWhere(""); setWhereClear(true); }}>清除地点</button>
              </div> : null}
              {section === "when" ? <div className="context-form-grid">
                <label>日期<input aria-label="日期" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
                <label>开始时间<input aria-label="开始时间" type="time" value={startAt} onChange={(event) => setStartAt(event.target.value)} /></label>
                <label>结束时间<input aria-label="结束时间" type="time" value={endAt} onChange={(event) => setEndAt(event.target.value)} /></label>
                <small>默认值会以弱化样式显示；清空已设置的必需时间后，系统会询问你补充。</small>
              </div> : null}
              {section === "who" ? <div className="context-form-grid">
                <label>成人<input type="number" min="0" max="20" value={adults} onChange={(event) => setAdults(Number(event.target.value))} /></label>
                <label>儿童<input type="number" min="0" max="20" value={children} onChange={(event) => setChildren(Number(event.target.value))} /></label>
                {children > 0 ? <label>儿童年龄（可选）<input type="number" min="0" max="17" value={childAge} onChange={(event) => setChildAge(event.target.value)} /></label> : null}
                <label className="context-form-wide">同行关系（可选）<input value={members} onChange={(event) => setMembers(event.target.value)} placeholder="例如：朋友、家人" /></label>
              </div> : null}
              {section === "budget" ? <div className="context-budget-options">
                <label><input type="radio" name="budget-mode" checked={budgetMode === "unlimited"} onChange={() => setBudgetMode("unlimited")} />不限预算</label>
                <label><input type="radio" name="budget-mode" checked={budgetMode === "per_person"} onChange={() => setBudgetMode("per_person")} />设置人均预算</label>
                {budgetMode === "per_person" ? <>
                  <label>人均金额（元）<input aria-label="人均金额" type="number" min="1" value={budgetAmount} onChange={(event) => setBudgetAmount(event.target.value)} required /></label>
                  <label className="context-checkbox"><input type="checkbox" checked={budgetStrict} onChange={(event) => setBudgetStrict(event.target.checked)} />严格控制，不超预算</label>
                </> : null}
              </div> : null}
              {section === "preferences" ? <div className="context-preference-editor">
                <div className="context-preference-chips">{preferences.map((item) => <button type="button" key={item} onClick={() => setPreferences((current) => current.filter((value) => value !== item))}>{item}<X size={13} /></button>)}</div>
                <label>添加偏好<input value={preferenceInput} onChange={(event) => setPreferenceInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addPreference(); } }} placeholder="例如：清淡、适合聊天" /></label>
                <button type="button" className="context-secondary" onClick={addPreference}>添加</button>
              </div> : null}
              {saveError ? <p className="context-error" role="alert">{saveError}</p> : null}
              <footer>
                <button type="button" className="context-secondary" onClick={close} disabled={saving}>取消</button>
                <button type="submit" className="context-primary" disabled={busy || saving}>{saving ? "正在更新…" : "保存并重新规划"}</button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
