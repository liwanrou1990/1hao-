// 真实页面测试：反复加载 Dashboard、切换全部页签、检查异常/关键元素/数据延迟标识；用于验收与长期稳定性抽检。
// 用法：node scripts/spot90_ui_test.js [url] [rounds]
const { chromium } = require('playwright');
const url = process.argv[2] || 'http://127.0.0.1:8501/'; const rounds = parseInt(process.argv[3] || '3');
(async () => {
  const browser = await chromium.launch(); const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const required = ['趋势状态', '风险等级', '距离主要均线', '14 天限价单成交概率', '持仓成本', '各风险等级的历史 90 天回撤分布', '最新完整日线', '参考价'];
  let failures = 0; const t0 = Date.now();
  for (let r = 0; r < rounds; r++) {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 120000 }); await page.waitForTimeout(6000);
    for (const name of ['BTC', 'ETH', '方案历史比较', '数据来源与验证']) {
      await page.getByRole('tab', { name }).first().click(); await page.waitForTimeout(3500);
      const exc = await page.locator('[data-testid="stException"]').count();
      const body = await page.locator('body').innerText();
      const miss = (name === 'BTC' || name === 'ETH') ? required.filter(k => !body.includes(k)) : [];
      const plots = await page.locator('.js-plotly-plot').count();
      const forbidden = ['AI 最佳买入区间', '必涨'].filter(k => body.includes(k) && !body.includes('不显示任何'));
      const ok = exc === 0 && miss.length === 0 && forbidden.length === 0 && (!(name === 'BTC' || name === 'ETH') || plots >= 2);
      if (!ok) failures++;
      console.log(`round ${r + 1} tab ${name}: exceptions=${exc} plots=${plots} missing=[${miss}] forbidden=[${forbidden}] ${ok ? 'OK' : 'FAIL'}`);
    }
  }
  console.log(`done rounds=${rounds} failures=${failures} elapsed_s=${((Date.now() - t0) / 1000).toFixed(0)}`);
  await browser.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
