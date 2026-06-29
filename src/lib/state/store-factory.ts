import { LocalStateStore, type StateStore } from "@/lib/state/local-store";
import {
  sqlitePathFromDatabaseUrl,
  SqliteStateStore,
} from "@/lib/state/sqlite-store";

export function getStateStore(env: NodeJS.ProcessEnv = process.env): StateStore {
  if (env.VALOR_STATE_BACKEND === "json") {
    return new LocalStateStore(env.VALOR_STATE_PATH);
  }

  const sqlitePath = sqlitePathFromDatabaseUrl(env.DATABASE_URL);
  if (sqlitePath) return new SqliteStateStore(sqlitePath);

  return new LocalStateStore(env.VALOR_STATE_PATH);
}
