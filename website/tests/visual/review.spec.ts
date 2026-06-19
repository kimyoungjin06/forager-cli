import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

const REVIEW_ROUTE = '/forager-cli/review/';
const DASHBOARD_ROUTE = '/forager-cli/dashboard/';
const DECISIONS_ROUTE = '/forager-cli/decisions/';
const liveSurface = JSON.parse(
  readFileSync(new URL('../fixtures/review-surface-live.json', import.meta.url), 'utf8'),
);
const workstationSurface = JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/ui/workstation_surface/attention.json', import.meta.url), 'utf8'),
);
const workstationVariants = [
  ['healthy_idle', 'Fixture healthy idle', 'No blocking attention item'],
  ['agent_outage', 'Fixture agent outage', 'Agent runtime outage'],
  ['active_run', 'Fixture active run', 'Work is active'],
  ['closeout_accepted', 'Fixture closeout accepted', 'Closeout accepted'],
  ['accepted_truth_recovery', 'Fixture accepted truth recovery', 'Accepted truth recovery needed'],
].map(([name, sourceLabel, topTitle]) => ({
  name,
  sourceLabel,
  topTitle,
  surface: JSON.parse(
    readFileSync(new URL(`../../../tests/fixtures/ui/workstation_surface/${name}.json`, import.meta.url), 'utf8'),
  ),
}));

test('review route renders the operator state and captures screenshots', async ({ page }, testInfo) => {
  await page.goto(REVIEW_ROUTE);

  await expect(page.getByRole('heading', { name: 'Morning operator state' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Attention Queue' })).toBeVisible();
  await expect(page.getByText('Next Safe Action')).toBeVisible();
  await expect(page.getByText('Authorization Boundary')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Runbook' })).toHaveAttribute(
    'href',
    '/forager-cli/guides/offdesk-morning-review/',
  );

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);

  const commandOverflow = await page
    .getByText('forager offdesk closeout --dry-run --json')
    .evaluate((element) => ({
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
  expect(commandOverflow.scrollWidth).toBeLessThanOrEqual(commandOverflow.width + 1);

  const projectName = testInfo.project.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const screenshotPath = testInfo.outputPath(`review-${projectName}.png`);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`review-${projectName}`, {
    path: screenshotPath,
    contentType: 'image/png',
  });
});

test('landing page links to the review surface', async ({ page }) => {
  await page.goto('/forager-cli/');

  await expect(
    page.getByRole('heading', {
      name: 'Entrust agent work. Return to evidence, choices, and continuity.',
    }),
  ).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open review surface' })).toHaveAttribute(
    'href',
    '/forager-cli/review/',
  );
  await expect(page.getByRole('link', { name: 'Open dashboard' })).toHaveAttribute(
    'href',
    '/forager-cli/dashboard/',
  );

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    scrollXAfterAttempt: (() => {
      window.scrollTo(1000, 0);
      return window.scrollX;
    })(),
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);
  expect(overflow.scrollXAfterAttempt).toBe(0);
});

test('review route hydrates exported review_surface.v1 when available', async ({ page }) => {
  await page.route('**/forager-cli/review-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(liveSurface),
    });
  });

  await page.goto(REVIEW_ROUTE);

  await expect(page.getByText('Live review_surface.v1')).toBeVisible();
  await expect(page.getByText('night-drive')).toBeVisible();
  await expect(page.locator('[data-review-title]')).toHaveText('Live approvals waiting');
  await expect(page.locator('[data-review-summary]')).toHaveText('승인 요청 2건이 먼저입니다.');
  await expect(page.locator('[data-review-action-command]')).toHaveText('forager offdesk pending --json');
  await expect(page.locator('[data-review-decision-label]')).toHaveText('2 open decisions');
});

test('dashboard route renders the workstation surface and captures screenshots', async ({ page }, testInfo) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(workstationSurface),
    });
  });

  await page.goto(DASHBOARD_ROUTE);

  await expect(page.getByRole('heading', { name: 'Workstation command center' })).toBeVisible();
  await expect(page.locator('#decisions').getByText('Decision Inbox', { exact: true })).toBeVisible();
  await expect(page.getByText('Project Portfolio', { exact: true })).toBeVisible();
  await expect(page.getByText('Work queue by project')).toBeVisible();
  await expect(page.locator('[data-dashboard-project-detail]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();
  await expect(page.getByText('Decision needed: Resolve closeout follow-up')).toBeVisible();
  await expect(page.locator('[data-dashboard-graph-title]')).toHaveText('NanoClustering provenance path');
  await expect(page.locator('[data-dashboard-graph]').getByText('NanoClustering accepted truth')).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-scope]')).toHaveText('Scope: NanoClustering');
  await expect(page.locator('[data-dashboard-assistant-context]')).toContainText('Resolve closeout follow-up');
  await expect(page.locator('[data-dashboard-assistant-prompts]').getByRole('button', { name: 'Can this advance?' })).toBeVisible();
  await page.getByRole('button', { name: /Science Atlas/ }).click();
  await expect(page.locator('[data-dashboard-project-detail]').getByRole('heading', { name: 'Science Atlas' })).toBeVisible();
  await expect(page.getByText('Decision needed: Runtime recovery required')).toBeVisible();
  await expect(page.getByText('Scoped Graph', { exact: true })).toBeVisible();
  await expect(page.locator('[data-dashboard-graph-title]')).toHaveText('Science Atlas provenance path');
  await expect(page.locator('[data-dashboard-graph]').getByText('Science Atlas decisions')).toBeVisible();
  await expect(page.getByText('Context helper', { exact: true })).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-scope]')).toHaveText('Scope: Science Atlas');
  await expect(page.locator('[data-dashboard-assistant-context]')).toContainText('Runtime recovery required');
  await expect(page.locator('[data-dashboard-assistant-refs]').getByText('decision:decision-runtime-recovery')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open review' })).toHaveAttribute(
    'href',
    '/forager-cli/review/',
  );

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);

  const commandOverflow = await page
    .locator('[data-dashboard-next-command]')
    .evaluate((element) => ({
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
  expect(commandOverflow.scrollWidth).toBeLessThanOrEqual(commandOverflow.width + 1);

  const projectName = testInfo.project.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const screenshotPath = testInfo.outputPath(`dashboard-${projectName}.png`);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`dashboard-${projectName}`, {
    path: screenshotPath,
    contentType: 'image/png',
  });
});

test('dashboard route hydrates exported workstation_surface.v1 when available', async ({ page }) => {
  const liveWorkstationSurface = JSON.parse(JSON.stringify(workstationSurface));
  liveWorkstationSurface.source_label = 'Live workstation_surface.v1';
  liveWorkstationSurface.top_attention.title = 'Live workstation intervention needed';
  liveWorkstationSurface.next_safe_actions[0].command = 'forager workstation status --json';
  liveWorkstationSurface.projects[0].display_name = 'Live NanoClustering';

  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(liveWorkstationSurface),
    });
  });

  await page.goto(DASHBOARD_ROUTE);

  await expect(page.getByText('Live workstation_surface.v1')).toBeVisible();
  await expect(page.locator('[data-dashboard-top-title]')).toHaveText('Live workstation intervention needed');
  await expect(page.locator('[data-dashboard-next-command]')).toHaveText('forager workstation status --json');
  await expect(page.locator('[data-dashboard-project-detail]').getByRole('heading', { name: 'Live NanoClustering', exact: true })).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-scope]')).toHaveText('Scope: Live NanoClustering');
});

test('decisions route renders read-only action center from workstation surface', async ({ page }) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(workstationSurface),
    });
  });

  await page.goto(DECISIONS_ROUTE);

  await expect(page.getByRole('heading', { name: 'Decision action center' })).toBeVisible();
  await expect(page.getByText('Queue', { exact: true })).toBeVisible();
  await expect(page.locator('[data-decision-detail]').getByRole('heading', { name: 'Resolve closeout follow-up' })).toBeVisible();
  await expect(page.getByText('Action envelope preview')).toBeVisible();
  await expect(page.getByText('action_envelope.v1')).toBeVisible();
  await expect(page.getByText('action_envelope_receipt.v1')).toBeVisible();
  await expect(page.getByText('Authorization boundary')).toBeVisible();
  await page.getByText('Envelope boundary').click();
  await expect(page.getByText('forager offdesk decision show --json decision-closeout-followup')).toBeVisible();
  await page.getByText('CLI fallback').click();
  await expect(page.getByText('forager offdesk decisions --json')).toBeVisible();

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);
});

test('dashboard route renders workstation fixture variants without empty-state gaps', async ({ page }) => {
  let currentSurface = workstationVariants[0].surface;
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(currentSurface),
    });
  });

  for (const variant of workstationVariants) {
    currentSurface = variant.surface;
    await page.goto(`${DASHBOARD_ROUTE}?fixture=${variant.name}`);

    await expect(page.getByText(variant.sourceLabel)).toBeVisible();
    await expect(page.locator('[data-dashboard-top-title]')).toHaveText(variant.topTitle);
    await expect(page.getByText('Project Portfolio', { exact: true })).toBeVisible();
    await expect(page.getByText('Decision Inbox', { exact: true })).toBeVisible();
    await expect(page.getByRole('complementary').getByText('Truth Recovery', { exact: true })).toBeVisible();

    const hasDecisions = Array.isArray(variant.surface.decisions) && variant.surface.decisions.length > 0;
    const hasProjects = Array.isArray(variant.surface.projects) && variant.surface.projects.length > 0;
    if (!hasDecisions) {
      await expect(page.getByText('No open decisions')).toBeVisible();
    }
    if (!hasProjects) {
      await expect(page.getByText('No project activity is currently visible.')).toBeVisible();
    }
    if (variant.name === 'accepted_truth_recovery') {
      await expect(page.getByText('Resolve archive review before accepting truth.', { exact: true })).toBeVisible();
      await expect(page.getByText('accepted_truth_recovery_action_envelope.v1')).toBeVisible();
      await expect(page.getByText('accepted_truth_recovery_action_receipt.v1')).toBeVisible();
      await expect(page.getByText('forager offdesk closeout-decision --closeout-id closeout-nano-followup').first()).toBeVisible();
    }

    const overflow = await page.evaluate(() => ({
      width: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);
  }
});
