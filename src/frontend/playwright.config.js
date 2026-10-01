import { defineConfig } from "@playwright/test";

const deployedUrl = process.env.FRONTEND_TEST_URL;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  workers: 2,
  use: {
    baseURL: deployedUrl || "http://127.0.0.1:4173",
    channel: "msedge",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: deployedUrl ? undefined : {
    command: "python3 -m http.server 4173 --bind 127.0.0.1",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
  },
});
