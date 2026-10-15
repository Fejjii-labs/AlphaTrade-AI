import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Only the named disposable local fixture may be initialized by this test runner.
const fixture = "postgresql+psycopg://reviewer:local-fixture-only@127.0.0.1:55432/reviewer";
if (process.env.DATABASE_URL !== fixture) throw new Error("Expected disposable PostgreSQL fixture");
const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../backend");
const initialized = spawnSync("uv", ["run", "python", "-c", [
  "from sqlalchemy import create_engine",
  "from app.db import models",
  "from app.db.base import Base",
  "import os",
  "engine = create_engine(os.environ['DATABASE_URL'])",
  "Base.metadata.create_all(engine)",
  "engine.dispose()",
].join("\n")], { cwd: backend, env: process.env, stdio: "inherit" });
if (initialized.status !== 0) process.exit(initialized.status ?? 1);
const server = spawn("uv", ["run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], {
  cwd: backend, env: process.env, stdio: "inherit",
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.kill(signal));
server.on("exit", code => process.exit(code ?? 0));
