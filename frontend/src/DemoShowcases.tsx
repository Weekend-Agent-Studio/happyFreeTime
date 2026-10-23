import { useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { BookOpen, X } from "lucide-react";
import { publicDemoEnabled, requestIdentity } from "./demoRuntime";

type Example = { id: string; title: string; description: string; prompt: string; steps: Array<{
  label: string; user?: string; reply?: string; stale?: boolean; question?: string; recovery?: string[];
  plans: Array<{ title: string; minutes: number; price: number; stops: Array<{ name: string; start: string; end: string }> }>;
  diffs?: Array<{ replacements: Array<{ stop_index: number; before_name: string; after_name: string }>; distance_delta: number | null }>;
}> };

export function DemoShowcases({ collapsed = false, busy = false, onStart }: { collapsed?: boolean; busy?: boolean; onStart?: (prompt: string) => void }) {
  const [open, setOpen] = useState(false);
  const [examples, setExamples] = useState<Example[]>([]);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load() {
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/demo/showcases", { headers: requestIdentity(), cache: "no-store" });
      if (!response.ok) throw new Error("展示案例暂时无法加载，请重试；体验过期时请刷新页面。");
      setExamples((await response.json()).data.examples);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "加载失败，请重试。"); }
    finally { setLoading(false); }
  }
  if (!publicDemoEnabled) return null;
  const example = examples[selected];
  return <Dialog.Root open={open} onOpenChange={(next) => { setOpen(next); if (next && !examples.length && !loading) void load(); }}>
    <Dialog.Trigger className="sidebar-utility" aria-label="展示案例" title={collapsed ? "展示案例" : undefined}><BookOpen size={17} aria-hidden="true" />{!collapsed ? "展示案例" : null}</Dialog.Trigger>
    <Dialog.Portal><Dialog.Backdrop className="sheet-backdrop" /><Dialog.Popup className="demo-settings-dialog demo-showcases-dialog">
      <header><Dialog.Title>看看规划如何完成</Dialog.Title><Dialog.Close className="sheet-close" aria-label="关闭展示案例"><X size={18} /></Dialog.Close></header>
      <Dialog.Description>预置案例来自真实离线规划链路，模拟出行日期为 2026-08-15，使用演示数据。这里仅供回看，不代表实时执行。</Dialog.Description>
      {loading ? <p role="status">正在加载案例…</p> : null}
      {error ? <div role="alert"><p>{error}</p><button className="context-secondary" type="button" onClick={() => void load()}>重试加载案例</button></div> : null}
      <nav className="demo-example-nav" aria-label="选择展示案例">{examples.map((item, index) => <button type="button" key={item.id} aria-pressed={selected === index} onClick={() => setSelected(index)}>{item.title}</button>)}</nav>
      {example ? <section aria-label={example.title}>
        <h3>{example.title}</h3><p>{example.description}</p>
        <ol className="demo-example-steps">{example.steps.map((step, index) => <li key={`${example.id}-${index}`}>
          <details open={index === example.steps.length - 1}><summary>{step.label}</summary>
            {step.user ? <p><strong>用户：</strong>{step.user}</p> : null}
            {step.reply ? <p>{step.reply}</p> : null}
            {step.stale ? <p>记录状态：条件已变更，旧方案需重新核验。</p> : null}
            {step.question ? <p>反问：{step.question}</p> : null}
            {step.recovery?.length ? <p>服务端提供的恢复选项：{step.recovery.join("；")}</p> : null}
            {step.plans.map((plan, planIndex) => <div className="demo-example-plan" key={planIndex}><strong>{plan.title}</strong><p>{plan.minutes} 分钟 · 全员地点费用 ¥{plan.price}</p><ul>{plan.stops.map((stop, stopIndex) => <li key={stopIndex}>{stop.start}–{stop.end} · {stop.name}</li>)}</ul></div>)}
            {step.diffs?.map((diff, diffIndex) => <p key={diffIndex}>{diff.replacements.map((change) => `第 ${change.stop_index + 1} 站：${change.before_name} → ${change.after_name}`).join("；")}</p>)}
          </details>
        </li>)}</ol>
        <footer><button type="button" className="context-primary" disabled={busy || !onStart} onClick={() => { onStart?.(example.prompt); setOpen(false); }}>从这个需求开始体验</button></footer>
        <p className="demo-privacy">只将需求填入新的个人体验，点击发送后才规划；结果随当前日期、条件和模式变化。</p>
      </section> : null}
    </Dialog.Popup></Dialog.Portal>
  </Dialog.Root>;
}
