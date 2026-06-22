import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { runMigrations } from "@/db/migrate";
import * as schema from "@/db/schema";

let sqlite: Database.Database | null = null;
let db: ReturnType<typeof drizzle<typeof schema>> | null = null;

export function resolveDatabasePath() {
  const url = process.env.DATABASE_URL ?? "file:./.valor/valor.sqlite";
  if (url.startsWith("file:")) {
    return url.slice("file:".length);
  }
  return url;
}

export function getSqlite() {
  if (!sqlite) {
    const path = resolveDatabasePath();
    mkdirSync(dirname(path), { recursive: true });
    sqlite = new Database(path);
    sqlite.pragma("journal_mode = WAL");
    runMigrations(sqlite);
  }
  return sqlite;
}

export function getDb() {
  if (!db) {
    db = drizzle(getSqlite(), { schema });
  }
  return db;
}

export function closeDb() {
  sqlite?.close();
  sqlite = null;
  db = null;
}