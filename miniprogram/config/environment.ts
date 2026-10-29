export function buildEnvironment(env: Record<string, string | undefined>) {
  const development = env.APP_ENV === "development";
  const apiOrigin =
    env.MINIPROGRAM_API_ORIGIN || (development ? "http://127.0.0.1:8000" : "");
  if (!development) {
    if (!apiOrigin)
      throw new Error("正式构建必须填写 MINIPROGRAM_API_ORIGIN。");
    const url = new URL(apiOrigin);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash ||
      url.port ||
      url.hostname.includes(":") ||
      url.hostname === "localhost" ||
      /^\d+(\.\d+){3}$/.test(url.hostname) ||
      !url.hostname.includes(".")
    ) {
      throw new Error(
        "正式构建必须配置 MINIPROGRAM_API_ORIGIN 为不带路径和端口的 HTTPS 域名。",
      );
    }
  }
  return { development, apiOrigin: apiOrigin.replace(/\/$/, "") };
}
