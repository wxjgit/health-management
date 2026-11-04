// runtime/environment.ts — 后端地址配置
// 本机联调用 127.0.0.1:8001（8000 被本机 haui-serv 占用，见项目备忘）。
// 真机预览需改为手机可访问的局域网地址；正式发布改为 HTTPS 合法域名。
export const ENV = {
  // 不含 /api 后缀；request.ts 自行拼接路径
  apiOrigin: 'http://127.0.0.1:8001',
  development: true,
};
