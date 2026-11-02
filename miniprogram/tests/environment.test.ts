import { expect, it } from "vitest";
import { createRequire } from "node:module";
const { buildEnvironment } = createRequire(import.meta.url)(
  "../scripts/environment.cjs",
);

it("正式构建必须显式提供 HTTPS 域名", () => {
  for (const value of [
    undefined,
    "http://example.com",
    "https://api.example.com:8443",
    "https://[::ffff:127.0.0.1]",
    "https://127.0.0.1",
    "https://localhost",
    "https://api.example.com/path",
    "https://user:secret@api.example.com",
  ]) {
    expect(() => buildEnvironment({ MINIPROGRAM_API_ORIGIN: value })).toThrow();
  }
  expect(
    buildEnvironment({ MINIPROGRAM_API_ORIGIN: "https://api.example.com" })
      .apiOrigin,
  ).toBe("https://api.example.com");
  expect(buildEnvironment({ APP_ENV: "development" }).apiOrigin).toBe(
    "http://127.0.0.1:8000",
  );
});
