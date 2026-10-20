import { useLayoutEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

function localDate(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dateValue(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** Local calendar dates stay independent of UTC and request state. */
export function DateCalendar({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const initial = localDate(value) ?? new Date();
  const [month, setMonth] = useState(() => new Date(initial.getFullYear(), initial.getMonth(), 1, 12));
  const [cursor, setCursor] = useState(() => dateValue(initial));
  const focusPending = useRef(false);
  const calendarRef = useRef<HTMLDivElement>(null);
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  const offset = (month.getDay() + 6) % 7;
  const days = new Date(year, monthIndex + 1, 0).getDate();

  useLayoutEffect(() => {
    if (!focusPending.current) return;
    focusPending.current = false;
    calendarRef.current?.querySelector<HTMLButtonElement>(`[data-date="${cursor}"]`)?.focus();
  }, [cursor, month]);

  function moveMonth(delta: number) {
    const next = new Date(year, monthIndex + delta, 1, 12);
    setMonth(next);
    setCursor(dateValue(next));
  }

  return <div className="context-calendar" ref={calendarRef} role="group" aria-label="选择出行日期">
    <div className="context-calendar-heading">
      <button type="button" aria-label="上个月" onClick={() => moveMonth(-1)}><ChevronLeft size={18} /></button>
      <strong aria-live="polite">{year}年{monthIndex + 1}月</strong>
      <button type="button" aria-label="下个月" onClick={() => moveMonth(1)}><ChevronRight size={18} /></button>
    </div>
    <div className="context-calendar-grid">
      {["一", "二", "三", "四", "五", "六", "日"].map((day) => <span className="context-weekday" key={day} aria-hidden="true">{day}</span>)}
      {Array.from({ length: Math.ceil((offset + days) / 7) * 7 }, (_, index) => {
        const day = index - offset + 1;
        if (day < 1 || day > days) return <span key={index} />;
        const date = new Date(year, monthIndex, day, 12);
        const key = dateValue(date);
        return <button type="button" key={index} data-date={key} aria-label={`${year}年${monthIndex + 1}月${day}日`}
          aria-pressed={value === key} tabIndex={cursor === key ? 0 : -1}
          onFocus={() => setCursor(key)} onClick={() => onChange(key)}
          onKeyDown={(event) => {
            const shifts: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -(index % 7), End: 6 - index % 7 };
            const shift = shifts[event.key];
            if (shift === undefined && event.key !== "PageUp" && event.key !== "PageDown") return;
            event.preventDefault();
            const next = new Date(date);
            if (shift !== undefined) next.setDate(day + shift);
            else {
              next.setDate(1);
              next.setMonth(monthIndex + (event.key === "PageUp" ? -1 : 1));
              next.setDate(Math.min(day, new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate()));
            }
            focusPending.current = true;
            setCursor(dateValue(next));
            setMonth(new Date(next.getFullYear(), next.getMonth(), 1, 12));
          }}>{day}</button>;
      })}
    </div>
  </div>;
}
