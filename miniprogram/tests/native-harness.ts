import { createRequire } from "node:module";
const { injectedNames } = createRequire(import.meta.url)("../scripts/wechat-scope.cjs");
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { runInNewContext } from "node:vm";
import { vi } from "vitest";
export function createHarness(options: any = {}) {
  const root = resolve("native"),
    storage: Map<string, any> =
      options.storage ||
      new Map([
        [
          "auth-session",
          {
            base: "http://127.0.0.1:8000",
            token: "synthetic-token",
            user: { id: "test-account" },
          },
        ],
      ]);
  let modules = new Map<string, any>(),
    definition: any;
  const pages: any[] = [];
  const fixtures: any = {
    "/policies": {
      version: "policy-v1",
      ready: true,
      operator: "测试运营方",
      contact: "test@example.test",
      privacy: ["隐私"],
      terms: ["协议"],
      model_notice: ["模型说明"],
      model_available: true,
    },
    "/auth/consent": { privacy_accepted: true, model_allowed: false },
    "/health": { today: "2026-11-01", local_only: false, mode: "model" },
    "/profile": {
      age: 30,
      height_cm: 168,
      weight_kg: 60,
      goal_weight_kg: 58,
      goal_date: "2027-01-01",
      sex: "female",
      activity: "light",
      nutrition_context: "general",
    },
    "/auth/me": { user: { id: "test-account" } },
    "/plans": [],
    "/workflows": [],
    "/workflows/expired": [],
    "/messages?limit=20": [],
    "/dashboard": {
      date: "2026-11-01",
      totals: {
        kcal: null,
        protein_g: null,
        carbs_g: null,
        fat_g: null,
        unknown_items: 1,
      },
      weight_kg: null,
      sleep_minutes: null,
      exercise_minutes: 0,
      record_count: 0,
      diet_complete: false,
      events: [],
    },
    "/targets/default": {
      status: "ready",
      targets: {
        kcal: 1800,
        protein_g: 90,
        carbs_g: 225,
        fat_g: 60,
        exercise_minutes: 150,
      },
      fingerprint: "estimate-v1",
      warnings: [],
      sources: [],
    },
    "/notifications": [],
    "/facts?include_inactive=true": [],
    "/events?limit=1000": [],
    "/trends?days=28": [],
    "/monthly-summaries": [],
    "/preferences": {
      weights: { energy: 50, macros: 50 },
      meal_times: { breakfast: "08:00" },
      checkin_time: "21:00",
      summary_time: "22:00",
      weekly_day: 6,
      notifications_enabled: true,
    },
    "/preferences/suggestion": null,
    ...options.fixtures,
  };
  const server =
    options.server ||
    vi.fn(async (o: any) => {
      const path = o.url.replace("http://127.0.0.1:8000/api", "");
      if (o.method === "PUT" && path === "/auth/consent")
        fixtures[path] = {
          privacy_accepted: true,
          model_allowed: o.data.model_consent,
        };
      return {
        statusCode: 200,
        header: {},
        data:
          o.method === "GET"
            ? (fixtures[path] ?? {})
            : path === "/auth/wechat/login"
              ? {
                  access_token: "new-token",
                  user: { id: "test-account" },
                  expires_at: 123,
                }
              : { reply: "已处理" },
      };
    });
  const ok = (value: any = {}) => vi.fn((o: any) => o?.success?.(value));
  const unlink = options.unlink || vi.fn().mockResolvedValue({});
  const wx: any = {
    env: { USER_DATA_PATH: "/app" },
    getStorageSync: (k: string) => storage.get(k),
    setStorageSync: (k: string, v: any) => storage.set(k, v),
    removeStorageSync: (k: string) => storage.delete(k),
    request(o: any) {
      const { success, fail, ...request } = o;
      Promise.resolve()
        .then(() => server(request))
        .then(success, fail);
    },
    login: ok({ code: "synthetic-code" }),
    reLaunch: ok(),
    switchTab: ok(),
    navigateTo: ok(),
    pageScrollTo: ok(),
    setClipboardData: ok(),
    shareFileMessage: ok(),
    showModal: ok({ confirm: true }),
    getFileSystemManager: () => ({
      unlink(o: any) {
        Promise.resolve()
          .then(() => unlink(o))
          .then(o.success, o.fail);
      },
      access(o: any) {
        o.fail();
      },
      writeFile: ok(),
      copyFile: ok(),
    }),
    ...options.wx,
  };
  const h: any = {
    root,
    storage,
    fixtures,
    server,
    wx,
    route: "pages/chat/index",
    resetModules() {
      modules.clear();
    },
    flush: () => new Promise((r) => setImmediate(r)),
  };
  function load(relative: string): any {
    const filename = resolve(
      root,
      relative.endsWith(".js") ? relative : relative + ".js",
    );
    if (!filename.startsWith(root + "/"))
      throw new Error("External runtime dependency: " + relative);
    if (filename.endsWith("/environment.js"))
      return { development: true, apiOrigin: "http://127.0.0.1:8000" };
    if (modules.has(filename)) return modules.get(filename);
    const module = { exports: {} };
    modules.set(filename, module.exports);
    const fn = runInNewContext(
      `(function(require,module,exports,${injectedNames.join(",")}){${readFileSync(filename, "utf8")}\n})`,
      {
        wx,
        Error,
        Date,
        Promise,
        Set,
        Map,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        getApp: () => ({}),
        getCurrentPages: () => [{ route: h.route }],
        Page: (v: any) => {
          definition = v;
        },
        Component: (v: any) => {
          definition = v;
        },
        App: () => {},
      },
    );
    fn(
      (name: string) => load(resolve(dirname(filename), name)),
      module,
      module.exports,
    );
    modules.set(filename, module.exports);
    return module.exports;
  }
  h.load = load;
  h.mount = async (name: string, params: any = {}, read = true) => {
    modules.delete(resolve(root, `pages/${name}/index.js`));
    load(`pages/${name}/index`);
    const bar = { setData: vi.fn(), setActiveRoute: vi.fn() };
    const instance: any = {
      ...definition,
      data: structuredClone(definition.data || {}),
      getTabBar: () => bar,
      bar,
      setData(value: any) {
        for (const [k, v] of Object.entries(value)) {
          const keys = k.replace(/\[(\d+)\]/g, ".$1").split(".");
          let current = this.data;
          for (const key of keys.slice(0, -1)) {
            current[key] ??= {};
            current = current[key];
          }
          current[keys[keys.length - 1]] = v;
        }
      },
    };
    pages.push(instance);
    instance.onLoad?.(params);
    instance._visible = true;
    if (read && instance.reload) await instance.reload();
    return instance;
  };
  h.component = (name: string, props: any = {}) => {
    load(`components/${name}/index`);
    const defaults = Object.fromEntries(
      Object.entries(definition.properties || {}).map(([k, s]: any) => [
        k,
        s.value ?? (s === Boolean ? false : s === String ? "" : null),
      ]),
    );
    const instance: any = {
      ...definition.methods,
      data: {
        ...defaults,
        ...structuredClone(definition.data || {}),
        ...props,
      },
      triggerEvent: vi.fn(),
      setData(value: any) {
        Object.assign(this.data, value);
      },
    };
    definition.lifetimes?.attached?.call(instance);
    return instance;
  };
  h.cleanup = () => pages.forEach((p) => p.onUnload?.());
  return h;
}
