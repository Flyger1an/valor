import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export interface KillSwitchState {
  active: boolean;
  reason: string;
  activatedAt?: string;
  activatedBy?: string;
  resetRequestedAt?: string;
  dashboardResetRequired: boolean;
}

const DEFAULT_STATE: KillSwitchState = {
  active: false,
  reason: "Not active",
  dashboardResetRequired: true,
};

export class FileKillSwitchStore {
  constructor(
    private readonly path =
      process.env.KILL_SWITCH_STATE_PATH ?? ".valor/kill-switch.json",
  ) {}

  read(): KillSwitchState {
    try {
      return JSON.parse(readFileSync(this.path, "utf8")) as KillSwitchState;
    } catch {
      return DEFAULT_STATE;
    }
  }

  activate(input: { reason: string; actor: string; now?: Date }): KillSwitchState {
    const next: KillSwitchState = {
      active: true,
      reason: input.reason,
      activatedAt: (input.now ?? new Date()).toISOString(),
      activatedBy: input.actor,
      dashboardResetRequired: true,
    };
    this.write(next);
    return next;
  }

  requestResume(input: { actor: string; now?: Date }): KillSwitchState {
    const current = this.read();
    const next: KillSwitchState = {
      ...current,
      resetRequestedAt: (input.now ?? new Date()).toISOString(),
      reason: `${current.reason}; resume requested by ${input.actor}`,
    };
    this.write(next);
    return next;
  }

  manualDashboardReset(input: { actor: string; now?: Date }): KillSwitchState {
    const next: KillSwitchState = {
      active: false,
      reason: `Manual dashboard reset by ${input.actor}`,
      activatedAt: undefined,
      activatedBy: undefined,
      resetRequestedAt: undefined,
      dashboardResetRequired: true,
    };
    this.write(next);
    return next;
  }

  private write(state: KillSwitchState) {
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(state, null, 2));
  }
}
