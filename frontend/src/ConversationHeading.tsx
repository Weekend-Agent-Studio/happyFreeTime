import { Sparkles } from "lucide-react";

/** Brand copy belongs to the conversation, independently of its scroll container. */
export function ConversationHeading({ compact = false }: { compact?: boolean }) {
  return compact ? (
    <header className="conversation-heading compact"><h1>给周末一点好心情</h1></header>
  ) : (
    <header className="conversation-heading welcome">
      <p>YOUR WEEKEND, YOUR WAY <Sparkles size={14} aria-hidden="true" /></p>
      <h1>给周末一点好心情<span className="welcome-sun" aria-hidden="true">✳</span></h1>
      <span>轻松规划，放心选择。</span>
    </header>
  );
}
