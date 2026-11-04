# 轻记小程序（miniprograme）

微信原生框架 + TypeScript + PyUI 组件库实现，对应后端 FastAPI 服务（见仓库根 `backend/`）。
设计基准：PyUI iOS 26 液态玻璃设计系统（统一圆角 `--r-card:20px`、品牌深蓝 `#015293`、玻璃卡片），
叠加轻记健康语义色与全局布局类（`app.wxss`），保证所有页面宽度/圆角/间距一眼一致。

## 目录结构

```text
miniprograme/
  app.json / app.ts / app.wxss   应用入口、页面注册、全局设计 Token 与布局类
  project.config.json            工程配置（appid=wx86cbe85947e2f8a9，TS 编译插件，打包排除项）
  runtime/                       运行时基础层（页面只调这里，不直接拼接口）
    environment.ts               后端地址（本机联调 127.0.0.1:8001）
    request.ts                   统一请求：token、request_id 幂等、离线检测、401 跳登录
    auth.ts                      微信登录、授权/撤回、注销、当前用户与用量
    api.ts                       业务接口服务层（records/chat/plans/insights/profile/deletions/system）
    viewmodel.ts                 后端原始数据 → 视图模型归一化（events/dashboard）
    format.ts                    日期/数字格式化、五类记录语义色
    feedback.ts                  统一反馈：toast/confirm（走 qj-feedback，不用微信原生）
    routes.ts / tabbar.ts        Tab 槽位、路由标题、自定义悬浮 tabbar 行为
  components/qj-feedback/        全局反馈组件（封装 pyui-message + pyui-dialog）
  pages/                         15 个页面（登录 + 5 Root Tab + 9 Task 页）
  vendor/pyui/                   PyUI 组件（已裁剪至 25 个被引用目录）
  scripts/
    check.cjs                    完整性校验：页面注册/组件路径/WXML 配平/vendor 依赖收集
    prune-vendor.cjs             裁剪未引用 vendor 组件（带备份，可 --restore 恢复）
    verify_contract.py           用真实后端响应核对前端字段映射
```

## 运行

1. 启动后端（仓库根）：`.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001`
2. 微信开发者工具「导入项目」→ 选择本目录 `miniprograme/`，AppID 填 `wx86cbe85947e2f8a9`。
3. 工具设置需开启 **TypeScript 编译**（project.config.json 已配 `useCompilerPlugins:["typescript"]`）。
4. 真机预览：把 `runtime/environment.ts` 的 `apiOrigin` 改为手机可访问的局域网地址或 HTTPS 合法域名。

## 校验命令（在 miniprograme/ 目录）

```sh
node scripts/check.cjs            # 页面/组件/WXML 完整性
node scripts/prune-vendor.cjs     # 裁剪未用 PyUI 组件（首次已执行，备份在 scripts/.vendor-backup）
npm run typecheck                 # TS 类型检查（tsc 5.6.3）
```

## 关键设计约定

- **禁用微信原生 UI**：不用 `wx.showToast/showModal/showLoading/showActionSheet`、不用系统 `tabBar`。
  反馈一律走 `qj-feedback`（封装 PyUI message/dialog），导航用自定义悬浮 `pyui-tab-bar`。
- **页面层级**：Root 页（记录/历史/计划/发现/我的）显示悬浮 tabbar、`reLaunch` 切换；
  Task 页隐藏 tabbar、显示返回与底部操作栏。顶部让位统一用动态 `navH`（胶囊按钮实测）。
- **风格统一**：颜色/玻璃/圆角复用 PyUI token；页面宽度边距、卡片、列表、统计、表单、chips
  全部走 `app.wxss` 的 `qj-*` 全局类，页面内不硬编码颜色与圆角。
- **幂等**：所有写入携带 `request_id`，网络失败按原编号重试，不重复记录。

## 契约核对结论（scripts/verify_contract.py，对真实后端）

已核对并与前端映射一致：`policies`、`profile`、`preferences`（5 维权重 energy/macros/satiety/adherence/snacking）、
`dashboard`、`trends`、`targets/default`（Mifflin–St Jeor + fingerprint）、`events` 行结构
`{id,op_key,kind,occurred_at,day,raw_text,data,version,created_at}`、`usage`
`{day,timezone,model_attempts,daily_limit,remaining}`、`revisions` 的 `changes:[{field,before,after}]`。

## 真机联调前需处理

- **PATCH 方法**：微信 `wx.request` 官方类型不含 PATCH（真机可能不支持）。`request.ts` 已对 PATCH
  附加 `X-HTTP-Method-Override` 头，但 FastAPI 默认不识别——真机联调前需后端加一层 override 中间件，
  或将 3 个 PATCH 端点（insights 反馈、notifications 已读、facts 决策）改为 POST。
- **HTTPS 合法域名**：正式发布需在微信后台配置 request 合法域名并启用 HTTPS。
