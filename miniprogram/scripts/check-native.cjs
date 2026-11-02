const fs = require("node:fs"),
  path = require("node:path"),
  { compileWeChatModule } = require("./wechat-scope.cjs");
const { files } = require("./native-pages.cjs");
function check(root = path.resolve("native")) {
  const source = files(root);
  let count = 0;
  function exists(file) {
    if (!fs.existsSync(file)) throw new Error("缺少原生文件：" + file);
  }
  for (const file of source) {
    const content = fs.readFileSync(file, "utf8");
    if (file.endsWith(".js")) {
      compileWeChatModule(content, file);
      if (/require\(['"](?:@tarojs|react(?:\/|['"]))/u.test(content))
        throw new Error("残留框架依赖：" + file);
      for (const match of content.matchAll(/require\(['"]([^'"]+)['"]\)/g)) {
        const name = match[1];
        if (!name.startsWith("."))
          throw new Error("运行时不允许外部依赖：" + name);
        const target = path.resolve(
          path.dirname(file),
          name.endsWith(".js") ? name : name + ".js",
        );
        if (target !== path.join(root, "native-runtime/environment.js"))
          exists(target);
      }
    }
    if (file.endsWith(".json")) {
      const config = JSON.parse(content);
      for (const target of Object.values(config.usingComponents || {})) {
        const base = target.startsWith("/")
          ? path.join(root, target)
          : path.resolve(path.dirname(file), target);
        for (const ext of [".js", ".json", ".wxml", ".wxss"])
          exists(base + ext);
      }
    }
    if (file.endsWith(".wxml")) {
      for (const match of content.matchAll(
        /<(?:import|include)[^>]*\bsrc=['"]([^'"]+)['"]/g,
      ))
        exists(path.resolve(path.dirname(file), match[1]));
      const js = file.replace(/\.wxml$/, ".js");
      const methods =
        (fs.existsSync(js) ? fs.readFileSync(js, "utf8") : "") +
        fs.readFileSync(path.join(root, "native-runtime/page.js"), "utf8");
      for (const m of content.matchAll(
        /\b(?:bind|catch):?[a-zA-Z]+\s*=\s*['"]([A-Za-z_$][\w$]*)['"]/g,
      )) {
        if (!methods.includes(m[1]))
          throw new Error("事件没有对应方法：" + file + " → " + m[1]);
      }
    }
  }
  const app = JSON.parse(fs.readFileSync(path.join(root, "app.json"), "utf8"));
  for (const page of app.pages) {
    for (const ext of [".js", ".json", ".wxml", ".wxss"])
      exists(path.join(root, page + ext));
    count++;
  }
  return count;
}
if (require.main === module)
  console.log(`原生检查通过：${check()} 个页面，组件引用与事件绑定齐全。`);
module.exports = { check };
