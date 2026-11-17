import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('https://fonts.googleapis.com/**', route => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.route('https://fonts.gstatic.com/**', route => route.abort());
});

test('public navigation reaches the workspace and returns keyboard focus', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/forager-cli/');
  await expect(page.getByRole('link', { name: 'Install Forager', exact: true })).toHaveAttribute('href', '/forager-cli/docs/installation.html');
  const menu = page.locator('nav details').filter({ visible: true });
  const summary = menu.locator('summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(menu).toHaveAttribute('open', '');
  const dashboard = menu.getByRole('link', { name: /^Dashboard\b/ });
  await dashboard.focus();
  await page.keyboard.press('Escape');
  await expect(menu).not.toHaveAttribute('open');
  await expect(summary).toBeFocused();
  await summary.click();
  await dashboard.click();
  await expect(page).toHaveURL(/\/forager-cli\/dashboard\/$/);
  expect(errors).toEqual([]);
  const width = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(width.scroll).toBeLessThanOrEqual(width.client + 1);
  await page.goto('/forager-cli/');
  await page.screenshot({ path: testInfo.outputPath('public-home.png'), fullPage: true });
  await page.screenshot({ path: testInfo.outputPath('public-hero.png') });
  await page.locator('nav details').filter({ visible: true }).locator('summary').click();
  await page.screenshot({ path: testInfo.outputPath('public-navigation.png') });
});

test('installation copies the canonical command without an unrelated GitHub request', async ({ page }) => {
  const errors: string[] = [];
  let githubRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().startsWith('https://api.github.com/')) githubRequests += 1; });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (text: string) => {
      (window as unknown as { copiedInstall: string }).copiedInstall = text;
    } } });
  });
  await page.goto('/forager-cli/');
  await page.getByRole('button', { name: 'Copy install command' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Install command copied.' })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { copiedInstall: string }).copiedInstall))
    .toBe('curl -fsSL https://raw.githubusercontent.com/kimyoungjin06/forager-cli/main/scripts/install.sh | bash');
  expect(githubRequests).toBe(0);
  expect(errors).toEqual([]);
});

test('clipboard denial leaves a usable command and clear feedback', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('Fixture clipboard denied'); } } });
  });
  await page.goto('/forager-cli/');
  await page.getByRole('button', { name: 'Copy install command' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Copy is unavailable here. Select the command above.' })).toBeVisible();
  await expect(page.locator('[data-install-command]')).toContainText('https://raw.githubusercontent.com/kimyoungjin06/forager-cli/main/scripts/install.sh');
  expect(errors).toEqual([]);
});
