import { BookOpen } from "lucide-react";
import type { AgentResponse } from "./types";

export type DemoExample = {
  id: string; title: string; description: string; prompt: string;
  steps: Array<{ label: string; user?: string; reply?: string; response?: AgentResponse; selected_plan_id?: string }>;
};

export type DemoExampleNavigation = {
  examples: DemoExample[]; activeId: string | null; loading: boolean; error: string;
  onOpen: (example: DemoExample) => void; onRetry: () => void;
};

export function DemoShowcases({ navigation, collapsed, busy }: {
  navigation: DemoExampleNavigation; collapsed: boolean; busy: boolean;
}) {
  return <section className="demo-example-list" aria-label="示例会话">
    {!collapsed ? <div className="sidebar-section-label">示例会话 <span>只读</span></div> : null}
    {navigation.loading ? <p className="session-history-empty" role="status">正在加载示例…</p> : null}
    {navigation.error ? <div className="session-action-error" role="alert"><p>{navigation.error}</p><button type="button" className="sidebar-utility" onClick={navigation.onRetry}>重试加载示例</button></div> : null}
    {navigation.examples.map((example) => <button key={example.id} type="button"
      className={`session-row ${navigation.activeId === example.id ? "active" : ""}`}
      aria-label={`查看示例：${example.title}`} aria-current={navigation.activeId === example.id ? "page" : undefined}
      title={collapsed ? example.title : undefined} disabled={busy} onClick={() => navigation.onOpen(example)}>
      <BookOpen className="example-glyph" size={17} aria-hidden="true" />
      {!collapsed ? <span className="session-copy"><strong>{example.title}</strong><small>{example.description}</small></span> : <span className="sr-only">{example.title}</span>}
    </button>)}
  </section>;
}
