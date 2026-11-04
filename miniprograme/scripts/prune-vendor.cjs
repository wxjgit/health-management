#!/usr/bin/env node
// scripts/prune-vendor.cjs — 裁剪未引用的 PyUI 组件，缩小主包体积
// 依据 scripts/.used-vendor.json（由 check.cjs 生成，已含递归依赖）。
// 安全措施：裁剪前把被移除目录备份到 scripts/.vendor-backup/，可一键恢复。
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const VENDOR = path.join(ROOT, 'vendor', 'pyui');
const BACKUP = path.join(__dirname, '.vendor-backup');
const usedFile = path.join(__dirname, '.used-vendor.json');

if (!fs.existsSync(usedFile)) {
  console.error('缺少 .used-vendor.json，请先运行 node scripts/check.cjs');
  process.exit(1);
}
const used = new Set(JSON.parse(fs.readFileSync(usedFile, 'utf8')));

// vendor/pyui 下的组件目录（common 单独保留）
const allDirs = fs.readdirSync(VENDOR, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => 'vendor/pyui/' + d.name);

const toRemove = allDirs.filter((d) => !used.has(d));
const kept = allDirs.filter((d) => used.has(d));

console.log(`vendor 组件目录总数: ${allDirs.length}`);
console.log(`保留: ${kept.length}  移除: ${toRemove.length}`);

if (process.argv.includes('--restore')) {
  if (!fs.existsSync(BACKUP)) { console.log('无备份可恢复'); process.exit(0); }
  fs.readdirSync(BACKUP).forEach((name) => {
    fs.cpSync(path.join(BACKUP, name), path.join(VENDOR, name), { recursive: true });
  });
  console.log('✓ 已从备份恢复全部组件');
  process.exit(0);
}

// 备份后移除
fs.mkdirSync(BACKUP, { recursive: true });
toRemove.forEach((rel) => {
  const name = path.basename(rel);
  fs.cpSync(path.join(ROOT, rel), path.join(BACKUP, name), { recursive: true });
  fs.rmSync(path.join(ROOT, rel), { recursive: true, force: true });
});
console.log(`✓ 已移除 ${toRemove.length} 个未用组件，备份在 scripts/.vendor-backup/`);
console.log('  恢复命令: node scripts/prune-vendor.cjs --restore');
