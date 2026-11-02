const fs = require("node:fs");
const path = require("node:path");
const { compileWeChatModule } = require("./wechat-scope.cjs");
function files(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((item) => {
    const file = path.join(root, item.name);
    return item.isDirectory() ? files(file) : [file];
  });
}
function emitNativePages({
  root,
  environment,
  output = path.join(root, "dist"),
}) {
  root = path.resolve(root);
  output = path.resolve(output);
  const source = path.join(root, "native");
  if (
    output === root ||
    source === output ||
    source.startsWith(output + path.sep)
  )
    throw new Error("拒绝覆盖源码目录");
  for (const file of files(source)) {
    if (file.endsWith(".js"))
      compileWeChatModule(fs.readFileSync(file, "utf8"), file);
    if (file.endsWith(".json")) JSON.parse(fs.readFileSync(file, "utf8"));
  }
  fs.rmSync(output, { recursive: true, force: true });
  fs.mkdirSync(output, { recursive: true });
  fs.cpSync(source, output, {
    recursive: true,
    filter: (filename) => !filename.endsWith(".md"),
  });
  fs.writeFileSync(
    path.join(output, "native-runtime/environment.js"),
    `module.exports = ${JSON.stringify(environment)};\n`,
  );
  const project = path.join(root, "project.config.json");
  if (fs.existsSync(project)) {
    const config = JSON.parse(fs.readFileSync(project, "utf8"));
    config.miniprogramRoot = "./";
    fs.writeFileSync(
      path.join(output, "project.config.json"),
      JSON.stringify(config, null, 2) + "\n",
    );
  }
}
module.exports = { emitNativePages, files };
