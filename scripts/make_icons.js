// 아이콘 PNG 생성: node scripts/make_icons.js  (Playwright + Chromium 필요, 결과물은 저장소에 포함되어 있음)
// 원본: app/static/icons/icon.svg (afterwork 로고 모노그램). 글꼴은 저장소의 Cormorant Garamond 파일을 쓴다.
// - icon-180.png : iOS apple-touch-icon (iOS가 모서리를 알아서 둥글게 깎음)
// - icon-192.png / icon-512.png : manifest 일반 아이콘
// - icon-maskable-512.png : 안드로이드 maskable (그림을 안전 영역 80% 안으로 축소)
// - badge-72.png : 안드로이드 알림 상태바용 단색 배지
// - favicon-32.png
// - logo-1024.png : 로고 이미지 (SNS 프로필·문서용 고해상도)
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..', 'app', 'static', 'icons');
const svg = fs.readFileSync(path.join(dir, 'icon.svg'), 'utf8');
const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');

const fontUrl = 'file://' + path.join(__dirname, '..', 'app', 'static', 'fonts', 'cormorant', 'cormorant-garamond-latin-600-normal.woff2');
const fontCss = `<style>@font-face{font-family:"Cormorant Garamond";font-weight:600;src:url("${fontUrl}") format("woff2");}</style>`;

const variants = [
  ['icon-180.png', 180, svg],
  ['icon-192.png', 192, svg],
  ['icon-512.png', 512, svg],
  ['favicon-32.png', 32, svg],
  ['logo-1024.png', 1024, svg],  // 로고 원본 이미지 (SNS·문서용)
  ['icon-maskable-512.png', 512,
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="#1E232A"/>` +
    `<g transform="translate(51.2 51.2) scale(0.8)">${inner.replace('<rect width="512" height="512" fill="#1E232A"/>', '')}</g></svg>`],
  ['badge-72.png', 72,
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><text x="33" y="52" text-anchor="middle" font-family="Cormorant Garamond" font-weight="600" font-size="62" fill="#fff">a</text><circle cx="52" cy="49" r="5" fill="#fff"/></svg>`],
];

(async () => {
  const b = await chromium.launch();
  for (const [name, size, src] of variants) {
    const p = await b.newPage({ viewport: { width: size, height: size } });
    await p.setContent(`<html><head>${fontCss}</head><body style="margin:0;background:transparent">${src.replace('<svg ', `<svg width="${size}" height="${size}" `)}</body></html>`);
    await p.evaluate(() => document.fonts.ready);
    await p.waitForTimeout(200);
    await p.screenshot({ path: path.join(dir, name), omitBackground: true });
    await p.close();
    console.log(name, size);
  }
  await b.close();
})();
