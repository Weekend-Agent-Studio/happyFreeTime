/** Brand copy belongs to the conversation, independently of its scroll container. */
export function ConversationHeading({ compact = false }: { compact?: boolean }) {
  return compact ? (
    <header className="conversation-heading compact"><h1>给周末一点好心情</h1></header>
  ) : (
    <header className="conversation-heading welcome">
      <p>YOUR WEEKEND, YOUR WAY</p>
      <h1>给周末一点好心情</h1>
      <span>轻松规划，放心选择。</span>
    </header>
  );
}
