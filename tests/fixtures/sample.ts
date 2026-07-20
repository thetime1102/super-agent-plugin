/**
 * Sample TypeScript file for testing mapFile
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import axios from 'axios';

// Configuration interface
interface AppConfig {
  apiUrl: string;
  timeout: number;
  retries?: number;
}

// Type alias
type LogLevel = 'debug' | 'info' | 'warn' | 'error';

// Class with methods
class Logger {
  private level: LogLevel = 'info';

  constructor(level?: LogLevel) {
    if (level) this.level = level;
  }

  log(message: string): void {
    console.log(`[${this.level}] ${message}`);
  }

  error(message: string): void {
    console.error(`[ERROR] ${message}`);
  }
}

// Async function
export async function fetchData(config: AppConfig): Promise<string> {
  const response = await axios.get(config.apiUrl, { timeout: config.timeout });
  return response.data;
}

// Arrow constant
export const VERSION = '1.0.0';

// Generator function
export function* idGenerator(): Generator<number> {
  let id = 0;
  while (true) {
    yield id++;
  }
}
