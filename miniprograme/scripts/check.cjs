#!/usr/bin/env node
// scripts/check.cjs — 小程序构建前完整性校验（可复跑）
// 检查：1) app.json 每页四件套齐全 2) usingComponents 路径存在 3) WXML 标签配平
//       4) 递归收集真正被引用的 vendor 组件（用于裁剪未用组件）
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const readJSON = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));
const errors = [];
const warns = [];

// ── 1. app.json 页面四件套 ──
const app = readJSON(path.join(ROOT, 'app.json'));
const usedComponents = new Set(); // 形如 vendor/pyui/pyui-icon/pyui-icon

function resolveUsing(using, fromDir) {
  Object.values(using || {}).forEach((ref) => {
    let abs;
    if (ref.startsWith('/')) abs = path.join(ROOT, ref);
    else abs = path.resolve(fromDir, ref);
    // 组件 .ts（微信工具自动编译为 .js）或 .js 二选一即可
    const ok = fs.existsSync(abs + '.js') || fs.existsSync(abs + '.ts');
    if (!ok) errors.push(`组件源码缺失: ${ref} → ${abs}.(ts|js)`);
    else usedComponents.add(path.relative(ROOT, abs));
  });
}

// 全局 usingComponents
resolveUsing(app.usingComponents, ROOT);

// ── 2. 每个页面 ──
(app.pages || []).forEach((pg) => {
  const dir = path.dirname(path.join(ROOT, pg));
  const base = path.join(ROOT, pg);
  ['.ts', '.js', '.wxml', '.json'].forEach((ext) => {
    // .ts 或编译后 .js 二选一即可
    if (ext === '.js') return;
    if (!fs.existsSync(base + ext)) {
      if (ext === '.ts' && fs.existsSync(base + '.js')) return;
      errors.push(`页面文件缺失: ${pg}${ext}`);
    }
  });
  if (!fs.existsSync(base + '.wxss')) warns.push(`页面无 wxss（可接受）: ${pg}`);
  const pj = base + '.json';
  if (fs.existsSync(pj)) resolveUsing(readJSON(pj).usingComponents, dir);

  // ── 3. WXML 标签配平 ──
  const wxml = base + '.wxml';
  if (fs.existsSync(wxml)) {
    const src = fs.readFileSync(wxml, 'utf8');
    const voidTags = new Set(['input', 'image', 'import', 'include', 'wxs', 'br']);
    const stack = [];
    const re = /<(\/?)([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g;
    let m;
    while ((m = re.exec(src))) {
      const [, close, tag, , selfClose] = m;
      if (selfClose === '/' || voidTags.has(tag)) continue;
      if (close) {
        if (stack.length === 0 || stack[stack.length - 1] !== tag) {
          errors.push(`${pg}.wxml 标签不配平: </${tag}> 栈顶=${stack[stack.length - 1] || '空'}`);
        } else stack.pop();
      } else stack.push(tag);
    }
    if (stack.length) errors.push(`${pg}.wxml 未闭合标签: ${stack.join(',')}`);

    // 3b. wxml 用到的自定义组件必须已注册（全局 usingComponents 或页级 json）
    const usedTags = new Set([...src.matchAll(/<(pyui-[a-z0-9-]+|qj-[a-z0-9-]+)/g)].map((m) => m[1]));
    const pjPath = base + '.json';
    const localReg = fs.existsSync(pjPath) ? new Set(Object.keys(readJSON(pjPath).usingComponents || {})) : new Set();
    const globalReg = new Set(Object.keys(app.usingComponents || {}));
    usedTags.forEach((t) => {
      if (!globalReg.has(t) && !localReg.has(t)) errors.push(`${pg}.wxml 使用未注册组件: <${t}>（需在 app.json 或页 json 的 usingComponents 注册）`);
    });
  }
});

// ── 4. 递归解析 vendor 组件自身依赖 ──
const queue = [...usedComponents];
while (queue.length) {
  const rel = queue.shift();
  const jsonPath = path.join(ROOT, rel + '.json');
  if (!fs.existsSync(jsonPath)) continue;
  let cj;
  try { cj = readJSON(jsonPath); } catch (e) { continue; }
  const dir = path.dirname(path.join(ROOT, rel));
  Object.values(cj.usingComponents || {}).forEach((ref) => {
    let abs;
    if (ref.startsWith('/')) abs = path.join(ROOT, ref);
    else abs = path.resolve(dir, ref);
    const r = path.relative(ROOT, abs);
    if (!usedComponents.has(r)) {
      if (fs.existsSync(abs + '.js')) { usedComponents.add(r); queue.push(r); }
      else errors.push(`vendor 组件依赖缺失: ${ref} (被 ${rel} 引用)`);
    }
  });
}

// qj-feedback 组件也要检查
const qjfb = 'components/qj-feedback/qj-feedback';
if (!usedComponents.has(qjfb)) usedComponents.add(qjfb);

// ── 输出 vendor 组件目录清单（用于裁剪）──
const vendorDirs = new Set();
usedComponents.forEach((rel) => {
  const m = rel.match(/^(vendor\/pyui\/[^/]+)/);
  if (m) vendorDirs.add(m[1]);
});
vendorDirs.add('vendor/pyui/common');

// ── 报告 ──
console.log('════ 小程序完整性校验 ════');
console.log(`页面数: ${(app.pages || []).length}`);
console.log(`被引用的 vendor 组件目录: ${vendorDirs.size}`);
if (warns.length) { console.log(`\n警告 ${warns.length}:`); warns.slice(0, 10).forEach((w) => console.log('  ⚠ ' + w)); }
if (errors.length) {
  console.log(`\n❌ 错误 ${errors.length}:`);
  errors.forEach((e) => console.log('  ' + e));
  process.exit(1);
}
console.log('\n✓ 页面注册、组件路径、WXML 配平全部通过');

// 写出被引用组件清单供裁剪脚本使用
fs.writeFileSync(path.join(__dirname, '.used-vendor.json'), JSON.stringify([...vendorDirs].sort(), null, 2));
console.log(`✓ 已写出 scripts/.used-vendor.json（${vendorDirs.size} 个目录）`);
