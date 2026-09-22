import { defineConfig } from '@playwright/test';

if (!process.env.E2E_UI_URL) throw new Error('E2E_UI_URL must identify the real deployed UI');

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  timeout: 180_000,
  globalTimeout: 240_000,
  maxFailures: 1,
  expect: { timeout: 20_000 },
  retries: 0,
  workers: 1,
  use: {
    baseURL: process.env.E2E_UI_URL,
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
});
