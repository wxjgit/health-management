// runtime/environment.ts — 后端地址与构建期开关
// apiOrigin：正式/真机统一走反代 HTTPS 域名（已在 mp 后台配置 request 合法域名）。
// 本机联调临时改回 http://127.0.0.1:8001（改完记得还原，勿提交）。
//
// auditMode（审核马甲开关）：提交审核前改为 true 并重新编译上传；
// 开启后客户端强制本地规则模式、隐藏全部模型相关 UI（模式标识/授权/额度/处理过程），
// 审核包与用户包代码一致、行为一致（合规）；过审取得类目后改回 false 重新提审。
export const ENV = {
  // 不含 /api 后缀；request.ts 自行拼接路径
  apiOrigin: 'https://qingji.pengyg.top',
  development: false,
  auditMode: false,
};
