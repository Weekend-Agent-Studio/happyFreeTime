import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { CalendarDays, ChevronRight, CircleDollarSign, Info, MapPin, Pencil, SlidersHorizontal, Users, X } from "lucide-react";
import type { FieldEdit, PlanningContextPatch, PlanningContextSummary, PreferenceCategory, PreferenceTag } from "./types";
import { DateCalendar } from "./DateCalendar";

export type ContextSection = "where" | "when" | "who" | "budget" | "preferences";
export type ContextEditorSection = ContextSection | "advanced";
export type ContextOpenTarget = ContextEditorSection | "all";

const SECTIONS: Array<{ id: ContextSection; label: string; icon: typeof MapPin }> = [
  { id: "when", label: "日期时间", icon: CalendarDays },
  { id: "where", label: "出发区域", icon: MapPin },
  { id: "who", label: "同行人数", icon: Users },
  { id: "budget", label: "人均预算", icon: CircleDollarSign },
  { id: "preferences", label: "出行偏好", icon: SlidersHorizontal },
];

const TIME_RANGE_MIN = 6 * 60;
const TIME_RANGE_MAX = 23 * 60;
const BUDGET_SLIDER_MIN = 50;
const BUDGET_SLIDER_MAX = 1000;

type EditorValues = {
  where: string;
  date: string;
  startAt: string;
  endAt: string;
  adults: number;
  children: number;
  childAge: string;
  members: string;
  budgetMode: "unlimited" | "per_person";
  budgetAmount: string;
  budgetStrict: boolean;
  preferences: PreferenceTag[];
  exactStopCount: string;
  maxDistanceKm: string;
  totalDistanceKm: string;
};

const PET_PREFERENCE: PreferenceTag = { category: "preferences", value: "携带宠物" };

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

function preferenceDiff(previous: PreferenceTag[], next: PreferenceTag[]) {
  const add = next.filter((item) => !previous.some((saved) => samePreference(saved, item)));
  const remove = previous.filter((item) => !next.some((saved) => samePreference(saved, item)));
  return add.length || remove.length ? { add, remove } : undefined;
}

function minutesFromTime(value: string): number | null {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  return hours <= 23 && minutes <= 59 ? hours * 60 + minutes : null;
}

function timeFromMinutes(value: number): string {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function isWithinTimeSlider(value: string) {
  const minutes = minutesFromTime(value);
  return minutes !== null && minutes >= TIME_RANGE_MIN && minutes <= TIME_RANGE_MAX;
}

function getValues(context: PlanningContextSummary | null): EditorValues {
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
    budgetMode: budget.mode === "per_person" ? "per_person" : "unlimited",
    budgetAmount: budget.amount == null ? "" : String(budget.amount),
    budgetStrict: Boolean(budget.strict),
    preferences: savedPreferences.filter((item, index, values) =>
      values.findIndex((candidate) => samePreference(candidate, item)) === index,
    ),
    exactStopCount: context?.exact_stop_count.value == null ? "" : String(context.exact_stop_count.value),
    maxDistanceKm: context?.max_distance_km.value == null ? "" : String(context.max_distance_km.value),
    totalDistanceKm: context?.total_distance_km.value == null ? "" : String(context.total_distance_km.value),
  };
}

export function clarificationSection(field: string | null | undefined): ContextEditorSection | null {
  if (field === "location") return "where";
  if (["date", "time_window", "departure_at", "return_by"].includes(field ?? "")) return "when";
  if (["party", "child_age"].includes(field ?? "")) return "who";
  if (field === "budget_per_person") return "budget";
  if (["preferences", "diet_tags", "scene_tags", "avoid"].includes(field ?? "")) return "preferences";
  if (["exact_stop_count", "max_distance_km", "total_distance_km"].includes(field ?? "")) return "advanced";
  return null;
}

export function PlanningContextBar({
  context,
  sessionId,
  busy,
  externalOpen,
  onExternalOpenHandled,
  onSave,
}: {
  context: PlanningContextSummary | null;
  sessionId: string | null;
  busy: boolean;
  externalOpen: ContextOpenTarget | null;
  onExternalOpenHandled: () => void;
  onSave: (patch: PlanningContextPatch) => Promise<void>;
}) {
  const [section, setSection] = useState<ContextEditorSection | null>(null);
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
  const [exactStopCount, setExactStopCount] = useState("");
  const [maxDistanceKm, setMaxDistanceKm] = useState("");
  const [totalDistanceKm, setTotalDistanceKm] = useState("");
  const [preferenceInput, setPreferenceInput] = useState("");
  const [preferenceCategory, setPreferenceCategory] = useState<PreferenceCategory>("preferences");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const focusReturnRef = useRef<HTMLElement | null>(null);
  const baseRef = useRef<{ sessionId: string | null; revision: number; view: "committed" | "draft"; values: EditorValues } | null>(null);

  const values = useMemo(() => getValues(context), [context]);

  function captureFocusReturn() {
    const activeElement = document.activeElement;
    if (activeElement instanceof HTMLElement && !activeElement.closest(".planning-context-dialog")) {
      focusReturnRef.current = activeElement;
    }
  }

  function restoreFocus() {
    const target = focusReturnRef.current;
    if (!target) return;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (target.isConnected) target.focus();
    }));
  }

  function open(next: ContextEditorSection) {
    if (!sectionChooserOpen) captureFocusReturn();
    const baseValues = getValues(context);
    baseRef.current = {
      sessionId,
      revision: context?.request_revision ?? 0,
      view: context?.request_view ?? "committed",
      values: baseValues,
    };
    setSection(next);
    setSectionChooserOpen(false);
    setSaveError("");
    setWhere(baseValues.where);
    setWhereClear(false);
    setDate(baseValues.date);
    setStartAt(baseValues.startAt);
    setEndAt(baseValues.endAt);
    setAdults(baseValues.adults);
    setChildren(baseValues.children);
    setChildAge(baseValues.childAge);
    setMembers(baseValues.members);
    setBudgetMode(baseValues.budgetMode);
    setBudgetAmount(baseValues.budgetAmount);
    setBudgetStrict(baseValues.budgetStrict);
    setPreferences(baseValues.preferences);
    setExactStopCount(baseValues.exactStopCount);
    setMaxDistanceKm(baseValues.maxDistanceKm);
    setTotalDistanceKm(baseValues.totalDistanceKm);
    setPreferenceInput("");
    setPreferenceCategory("preferences");
  }

  useEffect(() => {
    if (externalOpen) {
      if (externalOpen === "all") {
        captureFocusReturn();
        setSectionChooserOpen(true);
      }
      else open(externalOpen);
      onExternalOpenHandled();
    }
  // External clarification navigation is an event, not a controlled dialog state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalOpen]);

  useEffect(() => {
    if (!section || !baseRef.current) return;
    const base = baseRef.current;
    if (base.sessionId !== sessionId
      || base.revision !== (context?.request_revision ?? 0)
      || base.view !== (context?.request_view ?? "committed")) {
      baseRef.current = null;
      setSection(null);
      setSaveError("规划条件已更新，请重新打开编辑器后再修改。");
      restoreFocus();
    }
  }, [context?.request_revision, context?.request_view, section, sessionId]);

  function close() {
    if (savingRef.current) return;
    setSection(null);
    setSectionChooserOpen(false);
    baseRef.current = null;
    restoreFocus();
  }

  function fieldEdit<T>(next: string, previous: string): FieldEdit<T> | undefined {
    if (next === previous) return undefined;
    return (next ? { operation: "set", value: next } : { operation: "clear" }) as FieldEdit<T>;
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!section || savingRef.current) return;
    const base = baseRef.current;
    if (!base || base.sessionId !== sessionId
      || base.revision !== (context?.request_revision ?? 0)
      || base.view !== (context?.request_view ?? "committed")) {
      setSaveError("规划条件已更新，请关闭后重新打开编辑器。");
      return;
    }
    const original = base.values;
    const patch: PlanningContextPatch = { base_revision: base.revision };
    if (section === "where") {
      if (whereClear) patch.where = { operation: "clear" };
      else if (where.trim() !== original.where) patch.where = { operation: "set", value: where.trim() };
    } else if (section === "when") {
      const when: NonNullable<PlanningContextPatch["when"]> = {};
      const dateEdit = fieldEdit<string>(date, original.date);
      const startEdit = fieldEdit<string>(startAt, original.startAt);
      const endEdit = fieldEdit<string>(endAt, original.endAt);
      if (dateEdit) when.date = dateEdit;
      if (startEdit) when.start_at = startEdit;
      if (endEdit) when.end_at = endEdit;
      if (startAt && minutesFromTime(startAt) === null) {
        setSaveError("开始时间格式无效，请输入有效的 24 小时时间。");
        return;
      }
      if (endAt && minutesFromTime(endAt) === null) {
        setSaveError("结束时间格式无效，请输入有效的 24 小时时间。");
        return;
      }
      const startMinutes = minutesFromTime(startAt);
      const endMinutes = minutesFromTime(endAt);
      if (startMinutes !== null && endMinutes !== null && startMinutes >= endMinutes) {
        setSaveError("结束时间需要晚于开始时间。当前编辑器不自动跨日。");
        return;
      }
      if (Object.keys(when).length) patch.when = when;
    } else if (section === "who") {
      if (!Number.isSafeInteger(adults) || adults < 0 || !Number.isSafeInteger(children) || children < 0) {
        setSaveError("成人和儿童人数必须是大于或等于 0 的整数。");
        return;
      }
      if (childAge && (!Number.isInteger(Number(childAge)) || Number(childAge) < 0 || Number(childAge) > 17)) {
        setSaveError("儿童年龄需为 0 到 17 岁的整数。");
        return;
      }
      const who: NonNullable<PlanningContextPatch["who"]> = {};
      if (adults !== original.adults) who.adults = adults;
      if (children !== original.children) who.children = children;
      if (childAge !== original.childAge) {
        who.child_age = childAge ? { operation: "set", value: Number(childAge) } : { operation: "clear" };
      }
      if (members !== original.members) {
        who.members = members.trim()
          ? { operation: "set", value: members.split(/[、,，]/).map((item) => item.trim()).filter(Boolean) }
          : { operation: "clear" };
      }
      if (Object.keys(who).length) patch.who = who;
      const preferencesEdit = preferenceDiff(original.preferences, preferences);
      if (preferencesEdit) patch.preferences = preferencesEdit;
    } else if (section === "budget") {
      const nextMode = budgetMode === "per_person" ? "per_person" as const : "unlimited" as const;
      if (nextMode === "per_person" && (!budgetAmount.trim() || !Number.isFinite(Number(budgetAmount))
        || !Number.isInteger(Number(budgetAmount)) || Number(budgetAmount) <= 0)) {
        setSaveError("人均预算需为大于 0 的整数金额。");
        return;
      }
      const nextAmount = nextMode === "per_person" ? Number(budgetAmount) : null;
      const nextStrict = nextMode === "per_person" ? budgetStrict : false;
      const amountChanged = nextMode === "per_person" && String(nextAmount) !== original.budgetAmount;
      if (nextMode !== original.budgetMode || amountChanged || nextStrict !== original.budgetStrict) {
        patch.budget = nextMode === "per_person" && nextAmount !== null
          ? { mode: nextMode, amount: nextAmount, strict: nextStrict }
          : { mode: "unlimited" };
      }
    } else if (section === "preferences") {
      const preferencesEdit = preferenceDiff(original.preferences, preferences);
      if (preferencesEdit) patch.preferences = preferencesEdit;
    } else if (section === "advanced") {
      const advanced: NonNullable<PlanningContextPatch["advanced"]> = {};
      if (exactStopCount !== original.exactStopCount) {
        if (exactStopCount.trim()) {
          const count = Number(exactStopCount);
          if (!Number.isInteger(count) || count < 1 || count > 4) {
            setSaveError("精确站数需为 1 到 4 的整数。");
            return;
          }
          advanced.exact_stop_count = { operation: "set", value: count };
        } else if (original.exactStopCount) {
          advanced.exact_stop_count = { operation: "clear" };
        }
      }
      for (const [field, next, previous] of [
        ["max_distance_km", maxDistanceKm, original.maxDistanceKm],
        ["total_distance_km", totalDistanceKm, original.totalDistanceKm],
      ] as const) {
        if (next === previous) continue;
        if (!next.trim()) {
          if (previous) advanced[field] = { operation: "clear" };
          continue;
        }
        const distance = Number(next);
        if (!Number.isFinite(distance) || distance <= 0) {
          setSaveError("距离上限需为有限且大于 0 的公里数。");
          return;
        }
        if (String(distance) !== previous) advanced[field] = { operation: "set", value: distance };
      }
      if (Object.keys(advanced).length) patch.advanced = advanced;
    }
    if (Object.keys(patch).length === 1) {
      setSection(null);
      baseRef.current = null;
      restoreFocus();
      return;
    }
    savingRef.current = true;
    setSaving(true);
    setSaveError("");
    try {
      await onSave(patch);
      setSection(null);
      baseRef.current = null;
      restoreFocus();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "更新失败，请重试");
    } finally {
      savingRef.current = false;
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

  function setTimePreset(start: string, end: string) {
    setStartAt(start);
    setEndAt(end);
    setSaveError("");
  }

  function setDuration(hours: number) {
    const start = minutesFromTime(startAt);
    if (start === null) {
      setSaveError("请先设置开始时间，再使用时长快捷选项。");
      return;
    }
    const end = start + hours * 60;
    if (end > 23 * 60 + 59) {
      setSaveError("此时长会跨到次日；请直接设置结束时间，避免隐式跨日。");
      return;
    }
    setEndAt(timeFromMinutes(end));
    setSaveError("");
  }

  const advancedConditionsSet = Boolean(
    context?.exact_stop_count.value != null
    || context?.max_distance_km.value != null
    || context?.total_distance_km.value != null
  );

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
          const displayValue = !context ? label : id === "when"
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
            <span className="sr-only">{label}</span>
            <small>{id === "when" && displayValue === "未设置" ? "选择日期" : displayValue}</small>
            <Pencil className="context-edit-icon" size={12} aria-hidden="true" />
          </button>;
        })}
      </nav>
      <button
        type="button"
        className="planning-context-more"
        aria-label={advancedConditionsSet ? "更多条件，已设置" : "更多条件"}
        aria-haspopup="dialog"
        title={advancedConditionsSet ? "更多条件 · 已设置" : "更多条件"}
        onClick={() => open("advanced")}
        disabled={busy}
      >
        <SlidersHorizontal size={15} aria-hidden="true" />
        <span>更多条件</span>
        {advancedConditionsSet ? <span className="planning-context-more-indicator" aria-hidden="true" /> : null}
      </button>

      <Dialog.Root open={sectionChooserOpen} onOpenChange={(open) => { if (!open) { setSectionChooserOpen(false); restoreFocus(); } }}>
        <Dialog.Portal>
          <Dialog.Backdrop className="planning-context-backdrop" />
          <Dialog.Popup className="planning-context-dialog recovery-editor-chooser" aria-label="手动调整条件">
            <header>
              <div><small>规划恢复</small><Dialog.Title>手动调整条件</Dialog.Title></div>
              <button type="button" aria-label="关闭" onClick={() => { setSectionChooserOpen(false); restoreFocus(); }}><X size={18} /></button>
            </header>
            <p>选择要修改的条件。保存后可按新条件重新规划。</p>
            <div className="recovery-editor-sections">
              {SECTIONS.map(({ id, label, icon: Icon }) => (
                <button type="button" key={id} onClick={() => open(id)}>
                  <Icon size={17} aria-hidden="true" />
                  <span>{label}</span>
                  <small>{id === "when" ? whenSummary(context) : context?.[id]?.display_value ?? "设置"}</small>
                </button>
              ))}
              <button type="button" onClick={() => open("advanced")}>
                <SlidersHorizontal size={17} aria-hidden="true" />
                <span>更多条件</span>
                <small>站数与距离</small>
              </button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={Boolean(section)} onOpenChange={(open) => { if (!open && !savingRef.current) close(); }}>
        <Dialog.Portal>
          <Dialog.Backdrop className="planning-context-backdrop" />
          <Dialog.Popup className={`planning-context-dialog ${section === "when" ? "context-date-dialog" : ""}`} aria-label="规划条件编辑器">
            <header>
              <div>
                <small>规划条件{section && section !== "when" && section !== "advanced" ? ` · ${sourceLabel(context?.[section]?.source)}` : ""}</small>
                <Dialog.Title>{section === "advanced" ? "更多条件" : SECTIONS.find((item) => item.id === section)?.label ?? "规划条件"}</Dialog.Title>
              </div>
              <button type="button" aria-label="关闭" onClick={close} disabled={saving}><X size={18} /></button>
            </header>
            <form onSubmit={(event) => void save(event)}>
              <div className="context-form-content">
                {section === "where" ? <div className="context-form-field">
                  <label htmlFor="context-where">出发地点</label>
                  <input id="context-where" value={where} onChange={(event) => { setWhere(event.target.value); setWhereClear(false); setSaveError(""); }} placeholder="小区、地铁站或附近地标" />
                  <small>这是出发点和检索依据；由系统解析出发位置，不在此处模拟定位或地点建议。</small>
                  <button type="button" className="context-secondary" onClick={() => { setWhere(""); setWhereClear(true); }}>清除地点</button>
                </div> : null}
                {section === "when" ? <div className="context-date-layout">
                  <DateCalendar key={date.slice(0, 7)} value={date} onChange={(value) => { setDate(value); setSaveError(""); }} />
                  <div className="context-form-grid context-time-column">
                  <label className="context-form-wide"><span>日期 <small>{sourceHint(context?.when.date.source)}</small></span><input className={context?.when.date.source === "default" ? "context-assumed" : ""} aria-label="日期" type="date" value={date} onChange={(event) => { setDate(event.target.value); setSaveError(""); }} /></label>
                  <label><span>{context?.when.start_kind === "departure" || context?.pending_field === "departure_at" ? "出发时间" : "开始时间"} <small>{sourceHint(context?.when.start_at.source)}</small></span><input className={context?.when.start_at.source === "default" ? "context-assumed" : ""} aria-label={context?.when.start_kind === "departure" || context?.pending_field === "departure_at" ? "出发时间" : "开始时间"} type="time" value={startAt} onChange={(event) => { setStartAt(event.target.value); setSaveError(""); }} /></label>
                  <label><span>{context?.when.end_kind === "return_deadline" || context?.pending_field === "return_by" ? "最晚到家" : "结束时间"} <small>{sourceHint(context?.when.end_at.source)}</small></span><input className={context?.when.end_at.source === "default" ? "context-assumed" : ""} aria-label={context?.when.end_kind === "return_deadline" || context?.pending_field === "return_by" ? "最晚到家" : "结束时间"} type="time" value={endAt} onChange={(event) => { setEndAt(event.target.value); setSaveError(""); }} /></label>
                  <div className="context-time-tools context-form-wide">
                    <span className="context-tool-label">时段快捷设置</span>
                    <div className="context-preset-row" aria-label="时间预设">
                      <button type="button" className="context-secondary" aria-pressed={startAt === "09:00" && endAt === "12:00"} onClick={() => setTimePreset("09:00", "12:00")}>上午 09:00–12:00</button>
                      <button type="button" className="context-secondary" aria-pressed={startAt === "12:00" && endAt === "18:00"} onClick={() => setTimePreset("12:00", "18:00")}>下午 12:00–18:00</button>
                      <button type="button" className="context-secondary" aria-pressed={startAt === "18:00" && endAt === "22:00"} onClick={() => setTimePreset("18:00", "22:00")}>晚上 18:00–22:00</button>
                    </div>
                    <div className="context-preset-row" aria-label="行程时长">
                      {[2, 3, 4, 6].map((hours) => <button type="button" className="context-secondary" aria-pressed={minutesFromTime(endAt) !== null && minutesFromTime(startAt) !== null && minutesFromTime(endAt)! - minutesFromTime(startAt)! === hours * 60} key={hours} onClick={() => setDuration(hours)}>{hours} 小时</button>)}
                    </div>
                    {isWithinTimeSlider(startAt) && isWithinTimeSlider(endAt) && minutesFromTime(startAt)! < minutesFromTime(endAt)! ? <div className="context-time-range-wrap">
                      <div className="context-time-range-track" aria-hidden="true">
                        <span style={{ left: `${((minutesFromTime(startAt)! - TIME_RANGE_MIN) / (TIME_RANGE_MAX - TIME_RANGE_MIN)) * 100}%`, width: `${((minutesFromTime(endAt)! - minutesFromTime(startAt)!) / (TIME_RANGE_MAX - TIME_RANGE_MIN)) * 100}%` }} />
                      </div>
                      <input className="context-time-range-thumb context-time-range-start" type="range" min={TIME_RANGE_MIN} max={TIME_RANGE_MAX} step="1" value={minutesFromTime(startAt)!} aria-label="时段开始时间快捷滑块" aria-valuetext={startAt} onChange={(event) => { setStartAt(timeFromMinutes(Math.min(Number(event.target.value), minutesFromTime(endAt)! - 1))); setSaveError(""); }} />
                      <input className="context-time-range-thumb context-time-range-end" type="range" min={TIME_RANGE_MIN} max={TIME_RANGE_MAX} step="1" value={minutesFromTime(endAt)!} aria-label="时段结束时间快捷滑块" aria-valuetext={endAt} onChange={(event) => { setEndAt(timeFromMinutes(Math.max(Number(event.target.value), minutesFromTime(startAt)! + 1))); setSaveError(""); }} />
                      <small>06:00–23:00 · 拖动两端调整时段，也可直接输入精确时间。</small>
                    </div> : <small>设置有效时段后可拖动调整；06:00–23:00 以外请直接输入。</small>}
                  </div>
                  <small>保存后再按新条件规划；时间均为当天，不自动跨日。</small>
                </div></div> : null}
                {section === "who" ? <div className="context-form-grid">
                  <div className="context-count-field"><label htmlFor="context-adults">成人</label>
                    <span className="context-count-control"><button type="button" aria-label="减少成人" onClick={() => setAdults((value) => Math.max(0, value - 1))}>−</button><input id="context-adults" type="number" min="0" step="1" value={adults} onChange={(event) => setAdults(Number(event.target.value))} /><button type="button" aria-label="增加成人" onClick={() => setAdults((value) => value + 1)}>＋</button></span>
                  </div>
                  <div className="context-count-field"><label htmlFor="context-children">儿童</label>
                    <span className="context-count-control"><button type="button" aria-label="减少儿童" onClick={() => setChildren((value) => Math.max(0, value - 1))}>−</button><input id="context-children" type="number" min="0" step="1" value={children} onChange={(event) => setChildren(Number(event.target.value))} /><button type="button" aria-label="增加儿童" onClick={() => setChildren((value) => value + 1)}>＋</button></span>
                  </div>
                  {children > 0 ? <label>儿童年龄（可选）<input type="number" min="0" max="17" step="1" value={childAge} onChange={(event) => { setChildAge(event.target.value); setSaveError(""); }} /></label> : null}
                  <label className="context-form-wide">同行关系（可选）<input value={members} onChange={(event) => setMembers(event.target.value)} placeholder="例如：朋友、家人" /></label>
                  <label className="context-pet-choice context-form-wide">
                    <input type="checkbox" checked={preferences.some((item) => samePreference(item, PET_PREFERENCE))} onChange={(event) => setPreferences((current) => event.target.checked
                      ? current.some((item) => samePreference(item, PET_PREFERENCE)) ? current : [...current, PET_PREFERENCE]
                      : current.filter((item) => !samePreference(item, PET_PREFERENCE)))} />
                    <span><strong>携带宠物</strong><small>作为出行偏好，地点是否允许携宠尚未核实。</small></span>
                  </label>
                </div> : null}
                {section === "budget" ? <div className="context-budget-options" role="radiogroup" aria-label="预算方式">
                  <label className={`context-budget-choice${budgetMode === "unlimited" ? " selected" : ""}`}>
                    <input aria-label="不限预算" type="radio" name="budget-mode" checked={budgetMode === "unlimited"} onChange={() => { setBudgetMode("unlimited"); setSaveError(""); }} />
                    <span className="context-budget-choice-copy"><strong>不限预算</strong><small>不添加预算条件</small></span>
                  </label>
                  <label className={`context-budget-choice${budgetMode === "per_person" ? " selected" : ""}`}>
                    <input aria-label="设置人均预算" type="radio" name="budget-mode" checked={budgetMode === "per_person"} onChange={() => { setBudgetMode("per_person"); setSaveError(""); }} />
                    <span className="context-budget-choice-copy"><strong>设置人均预算</strong><small>可作为预算偏好，或设为严格上限</small></span>
                  </label>
                  {budgetMode === "per_person" ? <div className="context-budget-details">
                    <label className="context-budget-amount">
                      <span>人均金额（元）<small>{budgetAmount === values.budgetAmount ? sourceHint(context?.budget.source) : "待保存"}</small></span>
                      <input aria-label="人均金额（元）" type="number" min="1" step="1" value={budgetAmount} onChange={(event) => { setBudgetAmount(event.target.value); setSaveError(""); }} />
                    </label>
                    {Number.isInteger(Number(budgetAmount)) && Number(budgetAmount) >= BUDGET_SLIDER_MIN && Number(budgetAmount) <= BUDGET_SLIDER_MAX ? <label className="context-budget-slider">
                      <span>快捷调整人均金额</span><input aria-label="人均金额快捷滑块" type="range" min={BUDGET_SLIDER_MIN} max={BUDGET_SLIDER_MAX} step="50" value={Number(budgetAmount)} onChange={(event) => { setBudgetAmount(event.target.value); setSaveError(""); }} />
                    </label> : <small>便捷滑块范围为 ¥50–¥1000；超出范围可继续精确输入，金额不会被截断。</small>}
                    <div className="context-preset-row" aria-label="预算预设">{[100, 200, 300, 500].map((amount) => <button type="button" className="context-secondary" key={amount} onClick={() => { setBudgetAmount(String(amount)); setSaveError(""); }}>¥{amount}</button>)}</div>
                    <label className="context-budget-strict">
                      <input type="checkbox" checked={budgetStrict} onChange={(event) => { setBudgetStrict(event.target.checked); setSaveError(""); }} />
                      <span><strong>严格控制，不超预算</strong><small>不勾选时作为预算偏好；勾选后按硬上限筛选，不含实时价格或交通费用保证。</small></span>
                    </label>
                  </div> : null}
                </div> : null}
                {section === "preferences" ? <div className="context-preference-editor">
                  <p>体验作为偏好表达；饮食要求与希望避免的内容会保留各自类别。标签不代表地点事实已经核实。</p>
                  <div className="context-preference-chips">{preferences.map((item) => <button type="button" key={`${item.category}:${item.value}`} aria-label={`移除${preferenceCategoryLabel(item.category)}偏好 ${item.value}`} onClick={() => setPreferences((current) => current.filter((value) => !samePreference(value, item)))}><small>{preferenceCategoryLabel(item.category)}</small>{item.value}<X size={13} /></button>)}</div>
                  <label>偏好类别<select aria-label="偏好类别" value={preferenceCategory} onChange={(event) => setPreferenceCategory(event.target.value as PreferenceCategory)}>
                    <option value="preferences">想要的体验</option>
                    <option value="diet_tags">饮食要求</option>
                    <option value="scene_tags">场景类型</option>
                    <option value="avoid">希望避免</option>
                  </select></label>
                  <label>添加偏好<input value={preferenceInput} onChange={(event) => setPreferenceInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addPreference(); } }} placeholder="例如：清淡、适合聊天" /></label>
                  <button type="button" className="context-secondary" onClick={addPreference}>添加</button>
                </div> : null}
                {section === "advanced" ? <div className="context-advanced-editor">
                  <div className="context-advanced-field">
                    <label htmlFor="context-exact-stop-count">精确站数 <small>{context?.exact_stop_count.source === "default" ? "默认" : context?.exact_stop_count.source === "derived" ? "推导" : context?.exact_stop_count.source === "user" ? "已确认" : "未设置"}</small></label>
                    <div className="context-advanced-input"><input id="context-exact-stop-count" aria-label="精确站数" type="number" min="1" max="4" step="1" value={exactStopCount} onChange={(event) => { setExactStopCount(event.target.value); setSaveError(""); }} /><button type="button" className="context-secondary" onClick={() => setExactStopCount("")}>取消限制</button></div>
                    <small>指定行程包含的站点数量，范围为 1–4；已有必需站点角色仍会保留。</small>
                  </div>
                  <div className="context-advanced-field">
                    <label htmlFor="context-max-distance-km">检索及单段距离上限（km） <small>{context?.max_distance_km.source === "user" ? "已确认" : context?.max_distance_km.source === "derived" ? "推导" : context?.max_distance_km.source === "default" ? "默认" : "未设置"}</small></label>
                    <div className="context-advanced-input"><input id="context-max-distance-km" aria-label="检索及单段距离上限（km）" type="number" min="0" step="any" value={maxDistanceKm} onChange={(event) => { setMaxDistanceKm(event.target.value); setSaveError(""); }} /><button type="button" className="context-secondary" onClick={() => setMaxDistanceKm("")}>取消限制</button></div>
                    <small>同时影响候选检索范围和每段路线；全程总路程另设上限。</small>
                  </div>
                  <div className="context-advanced-field">
                    <label htmlFor="context-total-distance-km">全程累计距离上限（km） <small>{context?.total_distance_km.source === "user" ? "已确认" : context?.total_distance_km.source === "derived" ? "推导" : context?.total_distance_km.source === "default" ? "默认" : "未设置"}</small></label>
                    <div className="context-advanced-input"><input id="context-total-distance-km" aria-label="全程累计距离上限（km）" type="number" min="0" step="any" value={totalDistanceKm} onChange={(event) => { setTotalDistanceKm(event.target.value); setSaveError(""); }} /><button type="button" className="context-secondary" onClick={() => setTotalDistanceKm("")}>取消限制</button></div>
                    <small>限制所有行程路段累计距离；不自动推导它与单段上限的大小关系。</small>
                  </div>
                </div> : null}
              </div>
              {saveError ? <p className="context-error" role="alert">{saveError}</p> : null}
              <footer>
                <button type="button" className="context-secondary" onClick={close} disabled={saving}>取消</button>
                <button type="submit" className="context-primary" disabled={busy || saving}>{saving ? "正在保存…" : context?.pending_field && clarificationSection(context.pending_field) === section ? "确认并继续" : "保存条件"}</button>
              </footer>
            </form>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

/** Request status is in normal workspace flow, so it cannot cover the fixed chat title. */
export function PlanningContextNotice({ context, busy, onReplan }: {
  context: PlanningContextSummary | null;
  busy: boolean;
  onReplan: () => Promise<void>;
}) {
  const [replanning, setReplanning] = useState(false);
  const [replanError, setReplanError] = useState("");
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

  if (!context) return null;
  return (context.request_view === "draft" || context.plan_stale || (context.ready_for_planning && !context.has_active_plan)) ? (
    <div className={`planning-context-notice ${context.request_view === "draft" ? "draft" : context.plan_stale ? "stale" : "ready"}`} role="status">
      <div className="planning-context-notice-content">
        <Info size={16} aria-hidden="true" />
        <span>{context.request_view === "draft"
          ? `有未提交的条件更改；方案仍基于第 ${context.committed_request_revision ?? "—"} 版条件。`
          : context.plan_stale
            ? "规划条件已更新，旧方案尚未重新验证。"
            : "规划条件已就绪。"}</span>
        {context.ready_for_planning ? <button type="button" onClick={() => void replan()} disabled={busy || replanning}>
          {replanning ? "正在规划…" : context.has_active_plan ? "按新条件重新规划" : "开始规划"}
          <ChevronRight size={15} aria-hidden="true" />
        </button> : <small>请先补齐待确认条件</small>}
      </div>
      {replanError ? <small role="alert">{replanError}</small> : null}
    </div>
  ) : null;
}
