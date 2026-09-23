/**
 * 打包清单生成：读 references/packing-rules.json + 命令行条件，输出 Markdown 勾选清单。
 *
 * 沙箱内 cwd = 技能包根目录，故附件用相对路径读取；只用 node 标准库。
 * 用法：
 *   node scripts/packing_list.js --city 东京 --season winter --days 5
 * 退出码恒为 0；未收录的城市 / 季节只在 stderr 提示，通用清单照常输出。
 */

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const RULES_FILE = path.join('references', 'packing-rules.json');

function parseArgs(argv) {
  const args = { city: '东京', season: 'any', days: 3 };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === '--city') {
      args.city = value || args.city;
      i += 1;
    } else if (flag === '--season') {
      args.season = value || args.season;
      i += 1;
    } else if (flag === '--days') {
      const days = Number.parseInt(value, 10);
      if (Number.isFinite(days) && days > 0) {
        args.days = days;
      }
      i += 1;
    }
  }
  return args;
}

function collect(rules, args) {
  const items = [...(rules.base || [])];

  const seasonal = (rules.season || {})[args.season];
  if (seasonal) {
    items.push(...seasonal);
  } else if (args.season && args.season !== 'any') {
    const known = Object.keys(rules.season || {}).join('、');
    console.error(`未收录季节「${args.season}」，仅输出通用项；可选：${known}`);
  }

  const cityItems = (rules.city || {})[args.city];
  if (cityItems) {
    items.push(...cityItems);
  } else {
    const known = Object.keys(rules.city || {}).join('、');
    console.error(`未收录城市「${args.city}」，仅输出通用项；可选：${known}`);
  }

  const longTrip = rules.long_trip || {};
  const threshold = longTrip.threshold_days || Number.POSITIVE_INFINITY;
  if (args.days >= threshold) {
    items.push(...(longTrip.items || []));
  }
  return items;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const rules = JSON.parse(fs.readFileSync(RULES_FILE, 'utf8'));
  const items = collect(rules, args);

  console.log(`# 打包清单（${args.city} / ${args.season} / ${args.days} 天）`);
  console.log('');
  for (const item of items) {
    console.log(`- [ ] ${item}`);
  }
  console.log('');
  console.log(`共 ${items.length} 项；证件与贵重物品请随身携带，勿托运。`);
}

main();
