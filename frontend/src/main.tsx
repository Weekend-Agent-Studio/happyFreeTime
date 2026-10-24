import { StrictMode, useEffect, useState } from "react";
import { initializePublicDemo } from "./demoRuntime";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./tokens.css";
import "./styles.css";
import "./shell.css";

function Bootstrap() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { let active = true; setError(""); initializePublicDemo().then(() => { if (active) setReady(true); }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "连接失败，请重试。"); }); return () => { active = false; }; }, [attempt]);
  if (ready) return <App />;
  return <main className="demo-connect"><h1>HappyFreeTime</h1><p role={error ? "alert" : "status"}>{error || "正在建立本次临时体验…"}</p>{error ? <button type="button" className="context-primary" onClick={() => setAttempt((value) => value + 1)}>重新连接</button> : null}</main>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Bootstrap />
  </StrictMode>,
);
