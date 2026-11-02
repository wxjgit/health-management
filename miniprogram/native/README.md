# 微信原生源码

所有页面已迁移为微信原生实现。应用入口是 `app.js`，路由配置为 `app.json`，全局样式为 `app.wxss`。
不存在旧框架运行时或过渡桥接。开发者工具仍读取生成的 `dist/`，维护时编辑这里。

## 页面清单

登录、隐私与模型授权、记录、足迹、复盘占位、我的、今日汇总、长期信息、偏好提醒、站内消息、隐私删除，共 11 个原生页面。
每页均包含 `.js`、`.json`、`.wxml`、`.wxss` 四件套。

## 公共模块

- `native-runtime/api-core.js`：请求协议、认证凭据、账号/服务切换检查与关联错误编号。
- `native-runtime/platform.js`：将 `wx` 回调封装为 Promise。
- `native-runtime/state.js`：全局写入互斥、原请求重试、账号归属、隐私代际与缓存失效。
- `native-runtime/page.js`：原生生命周期、只在前台读取、异步响应失效、页面锁、弹窗和导航同步。
- `native-runtime/privacy.js`：账号归属的删除预览/作业保存、本地导出副本清理。
- `components/`：字段、折叠、弹窗、指标、证据、记录详情和授权组件。
- `custom-tab-bar/`：四项路由适配；实际透镜与拖动手势使用 `vendor/pyui/pyui-tab-bar`。
- `contracts.d.ts`：API 数据契约，供编辑器与维护参考。

`native-runtime/environment.js` 由构建时生成。所有运行时 `require` 都是小程序内相对路径，不需要 npm 组件构建。

## 检查

`npm run typecheck`、`npm test`、`npm run build:weapp:dev`。
构建和结构检查会按微信开发者工具的模块包装参数检查 JavaScript，防止 `confirm`、`history` 等注入名称与顶层常量冲突。
测试使用同样的模块包装和模拟微信 API，覆盖账号隔离、重试去重、计划恢复、删除范围变化、分页、授权、表单和导航手势，不访问真实账号数据库。
使用微信开发者工具实际检查原生页面布局、键盘和安全区，以及真实登录、文件分享与切后台行为。
