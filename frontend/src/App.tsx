import { DatabaseZap } from "lucide-react";

export function App() {
  return (
    <main className="shell">
      <header className="brand">
        <span className="mark" aria-hidden="true">
          <DatabaseZap size={22} strokeWidth={1.7} />
        </span>
        <div>
          <strong>DataQuery Agent</strong>
          <span>Local data workbench</span>
        </div>
      </header>
      <section className="empty" aria-labelledby="workspace-title">
        <p>WORKSPACE</p>
        <h1 id="workspace-title">Ask better questions of your data.</h1>
        <span>The query workbench is being assembled.</span>
      </section>
    </main>
  );
}
