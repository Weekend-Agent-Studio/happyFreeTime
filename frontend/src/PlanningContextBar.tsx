import { FormEvent, useEffect, useMemo, useState } from "react";
import { CalendarDays, CircleDollarSign, MapPin, SlidersHorizontal, Users, X } from "lucide-react";
import type { FieldEdit, PlanningContextPatch, PlanningContextSummary, PreferenceCategory, PreferenceTag } from "./types";

export type ContextSection = "where" | "when" | "who" | "budget" | "preferences";
export type ContextOpenTarget = ContextSection | "all";

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

function whenSummary(context: PlanningContextSummary | null): string {
  if (!context) return "设置";
  const when = context.when;
  const date = when.date.value ? when.date.display_value : "";
  const start = when.start_at.value ? when.start_at.display_value : "";
  const end = when.end_at.value ? when.end_at.display_value : "";
  const startLabel = when.start_kind === "departure" ? "出发" : "开始";
  const endLabel = when.end_kind === "return_deadline" ? "前到家" : "结束";
  const parts = [date];
  if (start && end && when.start_kind === "trip_start" && when.end_kind === "trip_end") {
    parts.push(`${start}–${end}`);
  } else {
    if (start) parts.push(`${startLabel} ${start}`);
    if (end) parts.push(when.end_kind === "return_deadline" ? `${end} 前到家` : `${endLabel} ${end}`);
  }
  return parts.filter(Boolean).join(" · ") || "选择日期";
}

function sourceHint(source?: string) {
  return source === "default" ? "默认" : source === "derived" ? "推导" : "";
}

function preferenceCategoryLabel(category: PreferenceCategory) {
  return {
    preferences: "体验",
    diet_tags: "饮食",
    scene_tags: "场景",
    avoid: "避免",
  }[category];
}

function samePreference(left: PreferenceTag, right: PreferenceTag) {
  return left.category === right.category && left.value === right.value;
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
  onReplan,
}: {
  context: PlanningContextSummary | null;
  busy: boolean;
  externalOpen: ContextOpenTarget | null;
  onExternalOpenHandled: () => void;
  onSave: (patch: PlanningContextPatch) => Promise<void>;
  onReplan: () => Promise<void>;
}) {
  const [section, setSection] = useState<ContextSection | null>(null);
  const [sectionChooserOpen, setSectionChooserOpen] = useState(false);
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
  const [preferences, setPreferences] = useState<PreferenceTag[]>([]);
  const [preferenceInput, setPreferenceInput] = useState("");
  const [preferenceCategory, setPreferenceCategory] = useState<PreferenceCategory>("preferences");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [replanning, setReplanning] = useState(false);
  const [replanError, setReplanError] = useState("");

  const values = useMemo(() => {
    const party = asRecord(currentValue(context?.who));
    const budget = asRecord(currentValue(context?.budget));
    const preferenceGroups = asRecord(currentValue(context?.preferences));
    const savedPreferences = Object.entries(preferenceGroups).flatMap(([category, value]) =>
      Array.isArray(value)
        ? value.map((item) => ({ category: category as PreferenceCategory, value: String(item) }))
        : [],
    );
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
      preferences: savedPreferences.filter((item, index, values) =>
        values.findIndex((candidate) => samePreference(candidate, item)) === index,
      ),
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
    setPreferenceCategory("preferences");
  }

  useEffect(() => {
    if (externalOpen) {
      if (externalOpen === "all") setSectionChooserOpen(true);
      else open(externalOpen);
      onExternalOpenHandled();
    }
  // External clarification navigation is an event, not a controlled dialog state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalOpen]);

  function close() {
    if (!saving) setSection(null);
    if (!saving) setSectionChooserOpen(false);
  }

  async function replan() {
    if (busy || replanning) return;
    setReplanning(true);
    setReplanError("");
    try {
      await onReplan();
    } catch (error) {
      setReplanError(error instanceof Error ? error.message : "重新规划失败，请重试");
    } finally {
      setReplanning(false);
    }
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
      // Budget used to be submitted unconditionally, so opening the section and
      // saving without an edit still bumped the request revision and marked an
      // otherwise valid plan as stale. Diff it like every other section.
      const nextMode = budgetMode === "per_person" ? "per_person" as const : "unlimited" as const;
      const nextAmount = nextMode === "per_person" ? Number(budgetAmount) : null;
      const nextStrict = nextMode === "per_person" ? budgetStrict : false;
      const amountChanged = nextMode === "per_person" && String(nextAmount) !== values.budgetAmount;
      if (nextMode !== values.budgetMode || amountChanged || nextStrict !== values.budgetStrict) {
        patch.budget = nextMode === "per_person" && nextAmount !== null
          ? { mode: nextMode, amount: nextAmount, strict: nextStrict }
          : { mode: "unlimited" };
      }
    } else if (section === "preferences") {
      const added = preferences.filter((item) => !values.preferences.some((saved) => samePreference(saved, item)));
      const removed = values.preferences.filter((item) => !preferences.some((saved) => samePreference(saved, item)));
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
    if (next.length) setPreferences((current) => {
      const additions = next.map((value) => ({ category: preferenceCategory, value }));
      return [...current, ...additions.filter((item) => !current.some((saved) => samePreference(saved, item)))];
    });
    setPreferenceInput("");
  }

  return (
    <div className="planning-context-wrap">
      <nav className="planning-context-bar" aria-label="规划条件">
        {SECTIONS.map(({ id, label, icon: Icon }) => {
          const field = id === "when" ? undefined : context?.[id];
          const timeFields = id === "when" && context
            ? [context.when.date, context.when.start_at, context.when.end_at].filter((item) => item.value != null)
            : [];
          const assumed = id === "when"
            ? timeFields.length > 0 && timeFields.every((item) => item.source === "default")
            : field?.source === "default";
          const displayValue = id === "when"
            ? whenSummary(context)
            : field?.display_value ?? "设置";
          const pending = clarificationSection(context?.pending_field) === id;
          return <button
            type="button"
            key={id}
            className={`planning-context-trigger ${pending ? "pending" : ""} ${assumed ? "assumed" : ""}`}
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
      {sectionChooserOpen ? (
        <div className="planning-context-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
          <section className="planning-context-dialog recovery-editor-chooser" role="dialog" aria-modal="true" aria-labelledby="recovery-editor-title">
            <header>
              <div><small>规划恢复</small><h2 id="recovery-editor-title">手动调整条件</h2></div>
              <button type="button" aria-label="关闭" onClick={close}><X size={18} /></button>
            </header>
            <p>选择要修改的条件。保存后可按新条件重新规划。</p>
            <div className="recovery-editor-sections">
              {SECTIONS.map(({ id, label, icon: Icon }) => (
                <button type="button" key={id} onClick={() => { setSectionChooserOpen(false); open(id); }}>
                  <Icon size={17} aria-hidden="true" />
                  <span>{label}</span>
                  <small>{id === "when" ? whenSummary(context) : context?.[id]?.display_value ?? "设置"}</small>
                </button>
              ))}
            </div>
          </section>
        </div>
      ) : null}
      {context && (context.plan_stale || (context.ready_for_planning && !context.has_active_plan)) ? (
        <div className="planning-context-notice" role="status">
          <span>{context.plan_stale ? "条件已更新，当前方案基于旧条件。" : "规划条件已就绪。"}</span>
          {context.ready_for_planning ? <button type="button" onClick={() => void replan()} disabled={busy || replanning}>
            {replanning ? "正在规划…" : context.has_active_plan ? "按新条件重新规划" : "开始规划"}
          </button> : <small>请先补齐待确认条件</small>}
          {replanError ? <small role="alert">{replanError}</small> : null}
        </div>
      ) : null}
      {section ? (
        <div className="planning-context-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
          <section className="planning-context-dialog" role="dialog" aria-modal="true" aria-labelledby="planning-context-title">
            <header>
              <div>
                <small>规划条件{section === "when" ? "" : ` · ${sourceLabel(context?.[section]?.source)}`}</small>
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
                <label><span>日期 <small>{sourceHint(context?.when.date.source)}</small></span><input className={context?.when.date.source === "default" ? "context-assumed" : ""} aria-label="日期" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
                <label><span>{context?.when.start_kind === "departure" || context?.pending_field === "departure_at" ? "出发时间" : "开始时间"} <small>{sourceHint(context?.when.start_at.source)}</small></span><input className={context?.when.start_at.source === "default" ? "context-assumed" : ""} aria-label={context?.when.start_kind === "departure" || context?.pending_field === "departure_at" ? "出发时间" : "开始时间"} type="time" value={startAt} onChange={(event) => setStartAt(event.target.value)} /></label>
                <label><span>{context?.when.end_kind === "return_deadline" || context?.pending_field === "return_by" ? "最晚到家" : "结束时间"} <small>{sourceHint(context?.when.end_at.source)}</small></span><input className={context?.when.end_at.source === "default" ? "context-assumed" : ""} aria-label={context?.when.end_kind === "return_deadline" || context?.pending_field === "return_by" ? "最晚到家" : "结束时间"} type="time" value={endAt} onChange={(event) => setEndAt(event.target.value)} /></label>
                <small>默认值会以弱化样式显示；清空已设置的必需时间后，系统会询问你补充。</small>
              </div> : null}
              {section === "who" ? <div className="context-form-grid">
                <label>成人<input type="number" min="0" max="20" value={adults} onChange={(event) => setAdults(Number(event.target.value))} /></label>
                <label>儿童<input type="number" min="0" max="20" value={children} onChange={(event) => setChildren(Number(event.target.value))} /></label>
                {children > 0 ? <label>儿童年龄（可选）<input type="number" min="0" max="17" value={childAge} onChange={(event) => setChildAge(event.target.value)} /></label> : null}
                <label className="context-form-wide">同行关系（可选）<input value={members} onChange={(event) => setMembers(event.target.value)} placeholder="例如：朋友、家人" /></label>
              </div> : null}
              {section === "budget" ? <div className="context-budget-options" role="radiogroup" aria-label="预算方式">
                <label className={`context-budget-choice${budgetMode === "unlimited" ? " selected" : ""}`}>
                  <input aria-label="不限预算" type="radio" name="budget-mode" checked={budgetMode === "unlimited"} onChange={() => setBudgetMode("unlimited")} />
                  <span className="context-budget-choice-copy"><strong>不限预算</strong><small>不添加预算条件</small></span>
                </label>
                <label className={`context-budget-choice${budgetMode === "per_person" ? " selected" : ""}`}>
                  <input aria-label="设置人均预算" type="radio" name="budget-mode" checked={budgetMode === "per_person"} onChange={() => setBudgetMode("per_person")} />
                  <span className="context-budget-choice-copy"><strong>设置人均预算</strong><small>可作为预算偏好，或设为严格上限</small></span>
                </label>
                {budgetMode === "per_person" ? <div className="context-budget-details">
                  <label className="context-budget-amount">
                    <span>人均金额（元）<small>{budgetAmount === values.budgetAmount ? sourceHint(context?.budget.source) : "待保存"}</small></span>
                    <input aria-label="人均金额（元）" type="number" min="1" value={budgetAmount} onChange={(event) => setBudgetAmount(event.target.value)} required />
                  </label>
                  <label className="context-budget-strict">
                    <input type="checkbox" checked={budgetStrict} onChange={(event) => setBudgetStrict(event.target.checked)} />
                    <span><strong>严格控制，不超预算</strong><small>不勾选时作为预算偏好；勾选后按硬上限筛选</small></span>
                  </label>
                </div> : null}
              </div> : null}
              {section === "preferences" ? <div className="context-preference-editor">
                <div className="context-preference-chips">{preferences.map((item) => <button type="button" key={`${item.category}:${item.value}`} onClick={() => setPreferences((current) => current.filter((value) => !samePreference(value, item)))}><small>{preferenceCategoryLabel(item.category)}</small>{item.value}<X size={13} /></button>)}</div>
                <label>偏好类别<select aria-label="偏好类别" value={preferenceCategory} onChange={(event) => setPreferenceCategory(event.target.value as PreferenceCategory)}>
                  <option value="preferences">想要的体验</option>
                  <option value="diet_tags">饮食要求</option>
                  <option value="scene_tags">场景类型</option>
                  <option value="avoid">希望避免</option>
                </select></label>
                <label>添加偏好<input value={preferenceInput} onChange={(event) => setPreferenceInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addPreference(); } }} placeholder="例如：清淡、适合聊天" /></label>
                <button type="button" className="context-secondary" onClick={addPreference}>添加</button>
              </div> : null}
              {saveError ? <p className="context-error" role="alert">{saveError}</p> : null}
              <footer>
                <button type="button" className="context-secondary" onClick={close} disabled={saving}>取消</button>
                <button type="submit" className="context-primary" disabled={busy || saving}>{saving ? "正在保存…" : context?.pending_field && clarificationSection(context.pending_field) === section ? "确认并继续" : "保存条件"}</button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
