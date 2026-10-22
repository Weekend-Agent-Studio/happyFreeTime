import { Sparkles } from "lucide-react";

/** Empty-state brand copy stays outside the conversation scroll container. */
export function ConversationHeading() {
  return <header className="conversation-heading welcome">
    <p>YOUR WEEKEND, YOUR WAY <Sparkles size={14} aria-hidden="true" /></p>
    <h1>给周末一点好心情<span className="welcome-sun" aria-hidden="true">✳</span></h1>
    <span>轻松规划，放心选择。</span>
  </header>;
}
