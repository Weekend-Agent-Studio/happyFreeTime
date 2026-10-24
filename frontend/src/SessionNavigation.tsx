import { DemoShowcases, type DemoExampleNavigation } from "./DemoShowcases";
import { publicDemoEnabled } from "./demoRuntime";
import { DemoSettings } from "./DemoSettings";
import { useRef, useState } from "react";
import { Check, Compass, History, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pencil, Plus, ReceiptText, Trash2, X } from "lucide-react";
import type { SessionSummary } from "./types";

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    active: "等待开始",
    running: "规划中",
    needs_input: "待补充信息",
    needs_recovery: "等待选择恢复操作",
    completed: "已生成方案",
    failed: "上次失败",
  };
  return labels[status] ?? status;
}

export function SessionNavigation({ recentSessions, sessionId, onNew, onOpen, onRename, onDelete, compact = false, collapsed = false, onToggle, onOpenHistory, exampleNavigation, busy = false }: {
  recentSessions: SessionSummary[];
  sessionId: string | null;
  onNew: () => void;
  onOpen: (sessionId: string) => void;
  onRename: (sessionId: string, title: string) => Promise<void>;
  onDelete: (sessionId: string) => Promise<void>;
  compact?: boolean;
  collapsed?: boolean;
  onToggle?: () => void;
  onOpenHistory?: () => void;
  exampleNavigation?: DemoExampleNavigation;
  busy?: boolean;
}) {
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const [actionError, setActionError] = useState("");
  const [managingSessions, setManagingSessions] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const visibleSessions = collapsed
    ? recentSessions.filter((session) => session.session_id === sessionId)
    : recentSessions;

  function toggleSidebar() {
    setRenamingSessionId(null);
    setMenuSessionId(null);
    onToggle?.();
    requestAnimationFrame(() => toggleRef.current?.focus());
  }

  async function saveTitle(sessionId: string) {
    const title = titleDraft.trim();
    if (!title || !recentSessions.some((item) => item.session_id === sessionId)) return;
    try {
      await onRename(sessionId, title);
      setRenamingSessionId(null);
      setActionError("");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "重命名失败");
    }
  }

  async function removeSession(session: SessionSummary) {
    if (!window.confirm(`删除“${session.title}”？会话消息和方案也会一起删除，且无法恢复。`)) return;
    try {
      await onDelete(session.session_id);
      setMenuSessionId(null);
      setActionError("");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "删除会话失败");
    }
  }

  return (
    <div className={`session-navigation ${compact ? "compact" : ""} ${collapsed ? "collapsed" : ""}`}>
      {!compact ? <div className="sidebar-brand">
        <div className="brand-logo-zone">
          <span className="brand-mark" aria-hidden="true"><Compass size={20} /></span>
          {collapsed ? <button ref={toggleRef} className="brand-expand" type="button" aria-label="展开侧边栏" title="展开侧边栏" aria-expanded={false} aria-controls="desktop-session-navigation" onClick={toggleSidebar}><PanelLeftOpen size={19} /></button> : null}
        </div>
        {!collapsed ? <><div className="brand-copy"><strong>HappyFreeTime</strong><small>周末小管家</small></div><button ref={toggleRef} className="sidebar-collapse" type="button" aria-label="收起侧边栏" title="收起侧边栏" aria-expanded={true} aria-controls="desktop-session-navigation" onClick={toggleSidebar}><PanelLeftClose size={17} /></button></> : null}
      </div> : null}
      <button className="new-session-button" type="button" aria-label="新建规划" title={collapsed ? "新建规划" : undefined} onClick={onNew}><Plus size={18} aria-hidden="true" /><span className={collapsed ? "sr-only" : undefined}>新建规划</span></button>
      {exampleNavigation ? <DemoShowcases navigation={exampleNavigation} collapsed={collapsed} busy={busy} /> : null}
      {!collapsed ? <div className="sidebar-section-label">{publicDemoEnabled ? "本次体验" : "最近会话"}</div> : null}
      <div className="session-history-list" aria-label="最近会话">
        {visibleSessions.map((session) => <div className={`session-row-shell ${managingSessions ? "managing" : ""}`} key={session.session_id} onContextMenu={(event) => { event.preventDefault(); setMenuSessionId(session.session_id); setActionError(""); }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setMenuSessionId(null); }}>
          {renamingSessionId === session.session_id ? <form className="session-rename-form" onSubmit={(event) => { event.preventDefault(); void saveTitle(session.session_id); }}>
            <input autoFocus aria-label="规划名称" maxLength={120} value={titleDraft} onChange={(event) => setTitleDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setRenamingSessionId(null); }} />
            <button type="submit" aria-label="保存名称" disabled={!titleDraft.trim()}><Check size={15} /></button>
            <button type="button" aria-label="取消重命名" onClick={() => setRenamingSessionId(null)}><X size={15} /></button>
          </form> : <button className={`session-row ${session.session_id === sessionId ? "active" : ""}`} type="button" aria-label={session.title} title={collapsed ? session.title : undefined} aria-current={session.session_id === sessionId ? "page" : undefined} onClick={() => onOpen(session.session_id)}>
            <History className="session-glyph" size={17} aria-hidden="true" /><span className="session-copy"><strong>{session.title}</strong><small>{session.last_message_preview || statusLabel(session.status)}</small></span>
            <time>{new Date(session.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</time>
          </button>}
          {renamingSessionId !== session.session_id ? <button className="session-more" type="button" aria-label="更多会话操作" aria-haspopup="menu" aria-expanded={menuSessionId === session.session_id} onClick={() => { setActionError(""); setMenuSessionId((current) => current === session.session_id ? null : session.session_id); }}><MoreHorizontal size={17} /></button> : null}
          {menuSessionId === session.session_id ? <div className="session-actions-menu" role="menu">
            <button type="button" role="menuitem" onClick={() => { setTitleDraft(session.title); setRenamingSessionId(session.session_id); setMenuSessionId(null); }}><Pencil size={14} />重命名</button>
            <button type="button" role="menuitem" className="danger" onClick={() => void removeSession(session)}><Trash2 size={14} />删除规划</button>
          </div> : null}
        </div>)}
        {!collapsed && !recentSessions.length ? <p className="session-history-empty">{publicDemoEnabled ? "会话仅在当前页面内可继续，刷新后不恢复。" : "完成一次规划后，会话和方案会保留在这里。"}</p> : null}
      </div>
      {actionError ? <p className="session-action-error" role="alert">{actionError}</p> : null}
      <div className="sidebar-spacer" />
      <DemoSettings collapsed={collapsed} />
      {collapsed ? <button className="sidebar-utility" type="button" aria-label="打开历史会话" title="历史会话与管理" onClick={onOpenHistory}><History size={18} /></button> : <button className={`sidebar-utility ${managingSessions ? "active" : ""}`} type="button" aria-pressed={managingSessions} onClick={() => setManagingSessions((current) => !current)}><ReceiptText size={17} />{managingSessions ? "完成管理" : "管理会话"}</button>}
    </div>
  );
}
