import { FormEvent, useState, useSyncExternalStore } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { CheckCircle2, KeyRound, X } from "lucide-react";
import { configureDemoModel, getDemoMode, publicDemoEnabled, subscribeDemoMode } from "./demoRuntime";

export function DemoSettings({ collapsed = false }: { collapsed?: boolean }) {
  const mode = useSyncExternalStore(subscribeDemoMode, getDemoMode);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const secureTransport = window.location.protocol === "https:" || ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  async function configure(value: string | null) {
    if (pending) return;
    setPending(true); setError(""); setNotice("");
    try {
      await configureDemoModel(value);
      setNotice(value ? "配置已保存；Key 有效性将在实际调用时确认。" : "已移除当前页面的模型配置，后续请求使用演示规则。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "配置失败，请重试。"); }
    finally { setKey(""); setPending(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); if (key.trim()) void configure(key.trim()); }
  return <Dialog.Root open={open} onOpenChange={(next) => { if (!pending) { setOpen(next); setKey(""); setError(""); setNotice(""); } }}>
    <Dialog.Trigger className={`sidebar-utility demo-settings-trigger ${publicDemoEnabled && mode === "llm" ? "is-configured" : ""}`} title={collapsed ? "模型配置" : undefined} aria-label="模型配置">
      {publicDemoEnabled && mode === "llm" ? <CheckCircle2 size={17} aria-hidden="true" /> : <KeyRound size={17} aria-hidden="true" />}{!collapsed ? <span>模型配置<small>{publicDemoEnabled ? mode === "llm" ? "DeepSeek · 已配置" : "演示模式 · 无需 Key" : "本地开发模式"}</small></span> : null}
    </Dialog.Trigger>
    <Dialog.Portal><Dialog.Backdrop className="sheet-backdrop" /><Dialog.Popup className="demo-settings-dialog">
      <header><Dialog.Title>模型配置</Dialog.Title><Dialog.Close className="sheet-close" aria-label="关闭模型配置" disabled={pending}><X size={18} /></Dialog.Close></header>
      <Dialog.Description>接入自己的 DeepSeek Key，增强需求理解与方案说明。不配置也能体验规则规划。</Dialog.Description>
      {publicDemoEnabled ? <form onSubmit={submit}>
        <div className={`demo-mode-state ${mode === "llm" ? "is-configured" : ""}`}>
          {mode === "llm" ? <CheckCircle2 size={18} aria-hidden="true" /> : <KeyRound size={18} aria-hidden="true" />}
          <div><strong>{mode === "llm" ? "DeepSeek 已配置" : "当前使用演示规则"}</strong><small>{mode === "llm" ? "是否实际调用，请查看每轮方案的「核验与来源」。" : "无需 Key · 需求理解范围有限"}</small></div>
        </div>
        <label htmlFor="demo-api-key">DeepSeek API Key</label>
        <input id="demo-api-key" type="password" autoComplete="off" spellCheck={false} value={key} maxLength={256} onChange={(event) => setKey(event.target.value)} placeholder="输入你自己的 Key" disabled={pending || !secureTransport} />
        {!secureTransport ? <p role="alert">本站尚未使用 HTTPS，暂不接受 Key，请使用演示模式。</p> : null}
        <p className="demo-privacy demo-key-essential">Key 经本站后端调用 DeepSeek，<strong>不持久保存</strong>；刷新后需重新配置，费用由你的账户承担。</p>
        <details className="demo-privacy-details">
          <summary>隐私与使用说明</summary>
          <p>仅支持 DeepSeek 官方接口，模型为 deepseek-flash。保存配置不会额外调用模型验证 Key；有效性在实际调用时确认。地点与路线的数据模式独立，不会因接入模型自动变为实时数据。</p>
          <p>Key 仅在本次临时体验的后端内存配置中使用，不写入数据库、应用日志或浏览器持久存储。体验最长 2 小时，到期后定时清理；移除或到期前已开始的请求可能继续完成。</p>
          <p>会话仅供当前页面体验，刷新不恢复；后端临时会话按期限清理。请勿输入敏感信息。公网部署须使用 HTTPS。</p>
        </details>
        {error ? <p role="alert" className="demo-settings-error">{error}</p> : null}
        {notice ? <p role="status">{notice}</p> : null}
        <footer><button className="context-secondary" type="button" disabled={pending || mode === "demo"} onClick={() => void configure(null)}>移除 Key，使用演示模式</button><button className="context-primary" type="submit" disabled={pending || !key.trim() || !secureTransport}>{pending ? "正在配置…" : "仅本次使用"}</button></footer>
      </form> : <p className="demo-privacy">当前为本地开发模式，模型使用服务端环境配置。公网体验版本启用后，可在这里临时配置自己的 Key。</p>}
    </Dialog.Popup></Dialog.Portal>
  </Dialog.Root>;
}
