# PyUI 原生组件

来源：用户提供的 `pyui-main.zip`，仓库 https://github.com/p2003722/pyui 。
版本为 `0.1.0`；提交号、压缩包 SHA-256、许可证声明和所选文件见 `SOURCE.json`。
上游包声明 MIT，提供的压缩包没有独立 LICENSE 文件；保留原始 `package.json` 和源码注释。

当前按需保留 `pyui-button`、`pyui-empty`、`pyui-tab-bar`、`pyui-icon` 与 `common/tokens.wxss`。
TabBar 做了兼容修改：四项导航的窄屏自适应宽度、进入页面时对齐当前路由、路由校正时终止残留手势动画；详见 `SOURCE.json` 的 `local_changes`，其余组件保持原始代码。
未引入上游示例应用的路由、全局状态、网络服务或业务代码。

接入方式：

- `app.json` 和组件 JSON 注册原生组件；Node.js 构建脚本原样复制本目录。
- `app.wxss` 先引入上游令牌，再设置项目样式及主色。
- 原生 WXML 直接使用 `pyui-button`、`pyui-empty` 等标签。
- 原生按钮派发 `click` 事件，以 `bind:click` 绑定。页面操作锁同时阻止重复提交。
- 公共弹窗按钮与复盘占位使用 PyUI；`custom-tab-bar` 直接装配 PyUI 液态玻璃导航，保留四项路由与 `wx.switchTab`，点击和拖动都通过 `change` 事件切换页面。
- 弹窗打开时移除导航子组件，关闭时恢复，避免遮挡；页面在 onShow/onReady 主动同步所属路由；切换失败或手势被拒绝时恢复正确高亮并停止旧动画。

升级时从新压缩包替换选中的完整组件目录及令牌，更新来源信息，并运行类型检查、测试和开发构建。
新增组件时同时复制其 `usingComponents` 引用的依赖，再注册到应用配置。
