/**
 * 聚天然 token 续期一键脚本
 * 使用前提：先运行 login.mjs 完成短信登录（auth_state.json 已刷新）
 * 本脚本自动：① 从 auth_state.json 提取新 token → ② 加密更新到 GitHub Secret JTR_TOKEN
 * 用法：node renew_token.mjs
 */
import sodium from 'libsodium-wrappers';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

const BASE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STATE_FILE = path.join(BASE_DIR, 'data', 'auth_state.json');
const REPO = 'wingchen0418/nangmo-eco-monitor';

// 从 git 远端配置里取 GitHub PAT
const remoteUrl = execSync('git remote get-url origin', { cwd: path.dirname(fileURLToPath(import.meta.url)), encoding: 'utf-8' }).trim();
const m = remoteUrl.match(/x-access-token:([^@]+)@/);
if (!m) throw new Error('git 远端配置中未找到 GitHub token，请联系 WorkBuddy');
const GH_TOKEN = m[1];
process.env.HTTPS_PROXY = process.env.HTTPS_PROXY || 'http://127.0.0.1:17890';

// 1. 提取新 token
const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
let jtrToken = null, expire = 0;
for (const origin of state.origins || []) {
  for (const item of origin.localStorage || []) {
    try {
      const v = JSON.parse(item.value);
      if (v?.loginInfo?.token) { jtrToken = v.loginInfo.token; expire = v.loginInfo.tokenExpireTime; }
    } catch {}
  }
}
if (!jtrToken) throw new Error('auth_state.json 中没有 token——请先运行 login.mjs 完成登录');

const expireDate = new Date(expire * 1000).toLocaleString('zh-CN', { hour12: false });
const daysLeft = ((expire - Date.now() / 1000) / 86400).toFixed(1);
console.log(`提取到新 token: ${jtrToken.slice(0, 8)}... | 有效期至 ${expireDate}（剩 ${daysLeft} 天）`);
if (Number(daysLeft) < 1) { console.log('⚠️ token 即将过期，请确认已重新登录'); process.exit(1); }

// 2. 更新 GitHub Secret
await sodium.ready;
const headers = { Authorization: `Bearer ${GH_TOKEN}`, Accept: 'application/vnd.github+json' };
const keyResp = await fetch(`https://api.github.com/repos/${REPO}/actions/secrets/public-key`, { headers });
if (!keyResp.ok) throw new Error(`获取公钥失败: HTTP ${keyResp.status}`);
const { key, key_id } = await keyResp.json();

const messageBytes = Buffer.from(jtrToken, 'utf-8');
const keyBytes = Buffer.from(key, 'base64');
const encrypted = sodium.crypto_box_seal(messageBytes, keyBytes);

const putResp = await fetch(`https://api.github.com/repos/${REPO}/actions/secrets/JTR_TOKEN`, {
  method: 'PUT',
  headers: { ...headers, 'Content-Type': 'application/json' },
  body: JSON.stringify({ encrypted_value: Buffer.from(encrypted).toString('base64'), key_id }),
});
if (!putResp.ok && putResp.status !== 204) throw new Error(`更新Secret失败: HTTP ${putResp.status}`);

console.log('✅ GitHub Secret JTR_TOKEN 已更新，云端采集器即刻生效（下次运行自动使用新 token）');
