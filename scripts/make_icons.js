// 아이콘 PNG 생성: node scripts/make_icons.js  (Playwright + Chromium 필요, 결과물은 저장소에 포함되어 있음)
// - icon-180.png : iOS apple-touch-icon (iOS가 모서리를 알아서 둥글게 깎음)
// - icon-192.png / icon-512.png : manifest 일반 아이콘
// - icon-maskable-512.png : 안드로이드 maskable (그림을 안전 영역 80% 안으로 축소)
// - badge-72.png : 안드로이드 알림 상태바용 단색 배지
// - favicon-32.png
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..', 'app', 'static', 'icons');
const svg = fs.readFileSync(path.join(dir, 'icon.svg'), 'utf8');
const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');

const variants = [
  ['icon-180.png', 180, svg],
  ['icon-192.png', 192, svg],
  ['icon-512.png', 512, svg],
  ['favicon-32.png', 32, svg],
  ['icon-maskable-512.png', 512,
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="#1f2a44"/>` +
    `<g transform="translate(51.2 51.2) scale(0.8)">${inner.replace('<rect width="512" height="512" fill="#1f2a44"/>', '')}</g></svg>`],
  ['badge-72.png', 72,
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><path d="M18 37 L31 50 L55 24" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>`],
];

(async () => {
  const b = await chromium.launch();
  for (const [name, size, src] of variants) {
    const p = await b.newPage({ viewport: { width: size, height: size } });
    await p.setContent(`<html><body style="margin:0;background:transparent">${src.replace('<svg ', `<svg width="${size}" height="${size}" `)}</body></html>`);
    await p.screenshot({ path: path.join(dir, name), omitBackground: true });
    await p.close();
    console.log(name, size);
  }
  await b.close();
})();
