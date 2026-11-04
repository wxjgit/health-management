// typings/global.d.ts — 业务级类型补充
// wx / App / Page / Component / getApp / getCurrentPages / setTimeout 等全局
// 由官方 miniprogram-api-typings（node_modules）提供，此处不重复声明以免冲突。

// app.ts globalData 的强类型形状（供需要时 import 使用；官方 getApp().globalData 为宽松 any）
interface AppGlobalData {
  tabBarData: { tabBar: Array<{ iconName: string; selectedIconName: string; text: string; pagePath: string }> };
  pagesData: Record<string, string>;
  token: string;
  userId: string;
  modelConsent: boolean;
  processingMode: 'model_allowed' | 'local_only';
  privacyVersion: string;
  apiOrigin: string;
}

declare function require(path: string): any;
declare const module: any;
declare function requirePlugin(name: string): any;
