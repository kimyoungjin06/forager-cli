import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

const REVIEW_ROUTE = '/forager-cli/review/';
const DASHBOARD_ROUTE = '/forager-cli/dashboard/';
const WORK_ROUTE = '/forager-cli/work/';
const GRAPH_ROUTE = '/forager-cli/graph/';
const SETTINGS_ROUTE = '/forager-cli/settings/';
const DECISIONS_ROUTE = '/forager-cli/decisions/';
const liveSurface = JSON.parse(
  readFileSync(new URL('../fixtures/review-surface-live.json', import.meta.url), 'utf8'),
);
const workstationSurface = JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/ui/workstation_surface/attention.json', import.meta.url), 'utf8'),
);
const acceptedWorkSurface = JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/ui/workstation_surface/closeout_accepted.json', import.meta.url), 'utf8'),
);
const workstationWorkSurface = JSON.parse(JSON.stringify(workstationSurface));
const nanoProject = workstationWorkSurface.projects.find((project) => project.project_key === 'NanoClustering');
if (nanoProject) {
  nanoProject.task_items = [
    {
      kind: 'Recovery task',
      task_id: 'task-nano-recovery',
      request_id: 'request-nano-recovery',
      title: 'Recover stale closeout follow-up',
      status: 'resume_pending',
      capability_id: 'offdesk.closeout_recovery',
      runner_kind: 'local_background',
      summary: 'Task needs recovery review before the harness resumes or abandons it.',
      reference: 'offdesk_tasks.json#task-nano-recovery',
      command: 'forager offdesk resume-task task-nano-recovery',
      updated_at: '2026-06-18T03:08:00Z',
      next_safe_action_kind: 'resume_review_required',
      requires_operator_review: true,
      inspection_items: [
        {
          label: 'Runner',
          value: 'local_background / offdesk.closeout_recovery',
          tone: 'neutral',
        },
        {
          label: 'Gate',
          value: 'blocked',
          tone: 'danger',
        },
        {
          label: 'Error',
          value: 'heartbeat is stale',
          tone: 'danger',
        },
      ],
    },
  ];
}
const harnessProject = workstationWorkSurface.projects.find((project) => project.project_key === 'Harness');
if (harnessProject) {
  harnessProject.task_items = [
    {
      kind: 'Live task store',
      task_id: 'task-harness-live',
      request_id: 'request-harness-live',
      title: 'Run dashboard visual pass',
      status: 'running',
      capability_id: 'web.visual_review',
      runner_kind: 'local_background',
      summary: 'Task is running from offdesk_tasks.json.',
      reference: 'offdesk_tasks.json#task-harness-live',
      command: 'forager offdesk poll ticket-harness-live',
      updated_at: '2026-06-18T03:12:00Z',
      next_safe_action_kind: 'runtime_monitoring',
      requires_operator_review: false,
      inspection_items: [
        {
          label: 'Runner',
          value: 'local_background / web.visual_review',
          tone: 'neutral',
        },
        {
          label: 'Ticket',
          value: 'ticket-harness-live',
          tone: 'neutral',
        },
        {
          label: 'Artifacts',
          value: '1/2 refs; log ready; result missing',
          tone: 'attention',
        },
        {
          label: 'Mode',
          value: 'running / awaiting_runtime_evidence',
          tone: 'neutral',
        },
      ],
    },
  ];
}
const workstationSettingsSurface = JSON.parse(JSON.stringify(workstationSurface));
workstationSettingsSurface.source_label = 'Live workstation settings';
workstationSettingsSurface.workspace_roots = [
  '/workspace/harness',
  '/workspace/science-atlas',
];
workstationSettingsSurface.source_refs = {
  status_json: 'forager status --json',
  task_store: 'offdesk_tasks.json',
  decision_ledger: 'offdesk_decisions.jsonl',
  telegram_loop_status: '~/.cache/forager/remote_operator_telegram_loop.json',
};
const workstationWithRuntimeDispatch = JSON.parse(JSON.stringify(workstationSurface));
workstationWithRuntimeDispatch.runtime_dispatch = {
  schema: 'runtime_dispatch_surface.v1',
  status: 'attention',
  candidate_count: 1,
  visible_count: 1,
  summary: '1 post-closeout runtime handoff is ready for inspection.',
  items: [
    {
      project_key: 'Atlas',
      decision_id: 'decision-runtime-recovery',
      title: 'Queue recovery task',
      stage: 'needs_preflight',
      severity: 'attention',
      closeout_id: 'closeout-runtime-recovery',
      execution_id: 'execution-runtime-recovery',
      receipt_id: 'decision-action-closeout-runtime',
      handoff_id: 'handoff-runtime-recovery',
      latest_preflight: {
        schema: 'runtime_dispatch_preflight.v1',
        preflight_id: 'runtime-preflight-recovery',
        processed_at: '2026-06-18T03:10:00Z',
        result_status: 'ready',
        reason: 'The closeout receipt still matches the latest decision ledger.',
      },
      latest_receipt: null,
      preflight_command: 'forager ondesk runtime-preflight --closeout-id closeout-runtime-recovery --json',
      dispatch_command: 'forager ondesk runtime-dispatch --preflight-id runtime-preflight-recovery --runner qwen --cmd "forager offdesk tick" --json',
      tick_command: 'forager offdesk tick --project-key Atlas --task-id task-runtime-recovery --limit 0 --json',
      boundary: 'Runtime dispatch can be queued only after the receipted decision closeout remains current.',
    },
  ],
};
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
  await makeFixedNavStableForFullPageScreenshot(page);
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
  await expect(page.getByRole('link', { name: 'Open graph' })).toHaveAttribute(
    'href',
    GRAPH_ROUTE,
  );
  await expect(page.getByRole('link', { name: 'Open settings' })).toHaveAttribute(
    'href',
    SETTINGS_ROUTE,
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
  await expect(page.getByText('Live source loaded')).toBeVisible();
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
  await expect(page.getByText('Work Portfolio', { exact: true })).toBeVisible();
  await expect(page.getByText('Project attention summary')).toBeVisible();
  await expect(page.getByText('Operator Priorities', { exact: true })).toBeVisible();
  await expect(page.locator('[data-dashboard-priorities]').getByText('P1 · Decision')).toBeVisible();
  await expect(page.locator('[data-dashboard-priorities]').getByText('Resolve closeout follow-up')).toBeVisible();
  await expect(page.locator('[data-dashboard-priorities]').getByRole('link', { name: /Resolve closeout follow-up/ })).toHaveAttribute('href', DECISIONS_ROUTE);
  await expect(page.getByRole('link', { name: 'Open work' })).toHaveAttribute('href', WORK_ROUTE);
  await expect(page.locator('[data-dashboard-work-summary]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();
  await expect(page.locator('[data-dashboard-work-summary]').getByRole('heading', { name: 'Science Atlas' })).toBeVisible();
  await expect(page.getByText('Decision needed: Resolve closeout follow-up')).toBeVisible();
  await expect(page.getByText('Scoped Graph', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'State briefing' })).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-context]')).toContainText('open decision');
  await expect(page.locator('[data-dashboard-assistant-answer]')).toContainText('execution still requires action envelopes and receipts');
  await expect(page.locator('[data-dashboard-assistant-refs]').getByText('workstation_surface.v1#top_attention')).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-refs]').getByText('decision_inbox_surface.v1')).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-actions]').getByText('Open decision inbox')).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-actions]').getByText('Proposal only')).toBeVisible();
  await expect(page.locator('[data-dashboard-assistant-prompts]').getByRole('button', { name: 'What needs attention?' })).toBeVisible();
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
  await makeFixedNavStableForFullPageScreenshot(page);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`dashboard-${projectName}`, {
    path: screenshotPath,
    contentType: 'image/png',
  });

  await page.getByRole('button', { name: 'Copy dashboard prompt: What needs attention?' }).click();
  await expect(page.locator('[data-dashboard-assistant-copy-status]')).toContainText(/Prompt (copied|ready)/);
});

test('work route renders project portfolio detail with graph and assistant context', async ({ page }, testInfo) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(workstationWorkSurface),
    });
  });

  await page.goto(WORK_ROUTE);

  await expect(page.getByRole('heading', { name: 'Project work portfolio' })).toBeVisible();
  await expect(page.getByText('Project Portfolio', { exact: true })).toBeVisible();
  await expect(page.getByText('Work queue by project')).toBeVisible();
  await expect(page.getByText('Live source loaded')).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /All 3/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Attention 2/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Blocked 1/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Running 1/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Review 1/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Recovery 1/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Accepted 0/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Stale 1/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Truth gap 2/ })).toBeVisible();
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing all 3 projects from the current workstation surface.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();
  await expect(page.locator('[data-work-graph-title]')).toHaveText('NanoClustering provenance path');
  await expect(page.locator('[data-work-graph]').getByText('NanoClustering accepted truth')).toBeVisible();
  await expect(page.locator('[data-work-assistant-scope]')).toHaveText('Scope: NanoClustering');
  await expect(page.locator('[data-work-assistant-context]')).toContainText('Resolve closeout follow-up');
  await expect(page.locator('[data-work-assistant-answer]')).toContainText('Resolve closeout follow-up');
  await expect(page.locator('[data-work-assistant-actions]').getByText('Review project decision')).toBeVisible();
  await expect(page.locator('[data-work-assistant-prompts]').getByRole('button', { name: /Can this advance/ })).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('Attention path')).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('latest execution blocked')).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('forager offdesk decision show --json decision-closeout-followup')).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('Reject if the decision record status')).toBeVisible();

  await page.getByRole('button', { name: /Science Atlas/ }).click();
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'Science Atlas' })).toBeVisible();
  await expect(page.getByText('Decision needed: Runtime recovery required')).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('Runtime recovery required')).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('Recovery actions must be task-scoped and receipt-backed.')).toBeVisible();
  await expect(page.locator('[data-work-attention-path]').getByText('forager offdesk decisions --json')).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('Active task drawer')).toBeVisible();
  await expect(page.getByText('Scoped Graph', { exact: true })).toBeVisible();
  await expect(page.locator('[data-work-graph-title]')).toHaveText('Science Atlas provenance path');
  await expect(page.locator('[data-work-graph]').getByText('Science Atlas decisions')).toBeVisible();
  await expect(page.getByText('Context helper', { exact: true })).toBeVisible();
  await expect(page.locator('[data-work-assistant-scope]')).toHaveText('Scope: Science Atlas');
  await expect(page.locator('[data-work-assistant-context]')).toContainText('Runtime recovery required');
  await expect(page.locator('[data-work-assistant-refs]').getByText('decision:decision-runtime-recovery')).toBeVisible();
  await expect(page.locator('[data-work-assistant-actions]').getByText('Review project decision')).toBeVisible();
  await expect(page.getByRole('main').getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', DASHBOARD_ROUTE);

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);

  const projectChipBoxes = await page.locator('[data-work-project-state-chips] > div').evaluateAll((items) =>
    items.map((element) => ({
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
      height: element.clientHeight,
      scrollHeight: element.scrollHeight,
    })),
  );
  for (const box of projectChipBoxes) {
    expect(box.width).toBeGreaterThan(120);
    expect(box.scrollWidth).toBeLessThanOrEqual(box.width + 1);
    expect(box.scrollHeight).toBeLessThanOrEqual(box.height + 1);
  }

  const projectName = testInfo.project.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const screenshotPath = testInfo.outputPath(`work-${projectName}.png`);
  await makeFixedNavStableForFullPageScreenshot(page);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`work-${projectName}`, {
    path: screenshotPath,
    contentType: 'image/png',
  });

  await page.locator('[data-work-task-drawer] summary').click();
  await expect(page.locator('[data-work-task-drawer]').getByText('Decision task')).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('decision-runtime-recovery')).toBeVisible();

  await page.getByRole('button', { name: 'Copy prompt: Can this advance?' }).click();
  await expect(page.locator('[data-work-assistant-copy-status]')).toContainText(/Prompt (copied|ready)/);

  await page.locator('[data-work-project-filters]').getByRole('button', { name: /Running 1/ }).click();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Running 1/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing 1 of 3 projects matching running.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'Forager Harness' })).toBeVisible();
  await expect(page.locator('[data-work-projects]').getByRole('button', { name: /Forager Harness/ })).toBeVisible();
  await expect(page.locator('[data-work-projects]').getByRole('button', { name: /Science Atlas/ })).toHaveCount(0);
  await expect(page.locator('[data-work-graph-title]')).toHaveText('Forager Harness provenance path');
  await page.locator('[data-work-task-drawer] summary').click();
  await expect(page.locator('[data-work-task-drawer]').getByText('Live task store')).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('task-harness-live', { exact: true })).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('local_background / web.visual_review')).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('1/2 refs; log ready; result missing')).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('forager offdesk poll ticket-harness-live')).toBeVisible();

  await page.locator('[data-work-project-filters]').getByRole('button', { name: /Recovery 1/ }).click();
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing 1 of 3 projects matching recovery.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();
  await page.locator('[data-work-task-drawer] summary').click();
  await expect(page.locator('[data-work-task-drawer]').getByText('Recovery task')).toBeVisible();
  await expect(page.locator('[data-work-task-drawer]').getByText('heartbeat is stale')).toBeVisible();

  await page.locator('[data-work-project-filters]').getByRole('button', { name: /Stale 1/ }).click();
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing 1 of 3 projects matching stale.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();

  await page.locator('[data-work-project-filters]').getByRole('button', { name: /Review 1/ }).click();
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing 1 of 3 projects matching review.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();

  await page.locator('[data-work-project-filters]').getByRole('button', { name: /Blocked 1/ }).click();
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing 1 of 3 projects matching blocked.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'Science Atlas' })).toBeVisible();
  await expect(page.locator('[data-work-assistant-scope]')).toHaveText('Scope: Science Atlas');
});

test('work route can filter accepted truth projects', async ({ page }) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(acceptedWorkSurface),
    });
  });

  await page.goto(WORK_ROUTE);

  await expect(page.getByText('Live source loaded')).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Accepted 1/ })).toBeVisible();
  await expect(page.locator('[data-work-project-filters]').getByRole('button', { name: /Stale 0/ })).toBeVisible();
  await page.locator('[data-work-project-filters]').getByRole('button', { name: /Accepted 1/ }).click();
  await expect(page.locator('[data-work-filter-summary]')).toHaveText('Showing 1 of 1 projects matching accepted.');
  await expect(page.locator('[data-work-project-detail]').getByRole('heading', { name: 'NanoClustering' })).toBeVisible();
  await expect(page.locator('[data-work-project-state-chips]').getByText('accepted')).toHaveCount(2);
});

test('graph route renders scoped provenance and project blockers', async ({ page }, testInfo) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(workstationSurface),
    });
  });

  await page.goto(GRAPH_ROUTE);

  await expect(page.getByRole('heading', { name: 'Scoped provenance graph' })).toBeVisible();
  await expect(page.getByText('Live source loaded')).toBeVisible();
  await expect(page.getByText('Support Path', { exact: true })).toBeVisible();
  await expect(page.locator('[data-graph-selected-title]')).toHaveText('NanoClustering evidence path');
  await expect(page.locator('[data-graph-canvas]').getByText('approved with followups').first()).toBeVisible();
  await expect(page.locator('[data-graph-blockers]').getByText('Resolve closeout follow-up')).toBeVisible();
  await expect(page.locator('[data-graph-evidence]').getByText('decision:decision-closeout-followup')).toBeVisible();

  await page.getByRole('button', { name: /Science Atlas/ }).click();
  await expect(page.locator('[data-graph-selected-title]')).toHaveText('Science Atlas evidence path');
  await expect(page.locator('[data-graph-canvas]').getByText('needs revision').first()).toBeVisible();
  await expect(page.locator('[data-graph-canvas]').getByText('blocked').first()).toBeVisible();
  await expect(page.locator('[data-graph-canvas]').getByText('missing').first()).toBeVisible();
  await expect(page.locator('[data-graph-blockers]').getByText('Runtime recovery required')).toBeVisible();
  await expect(page.locator('[data-graph-evidence]').getByText('decision:decision-runtime-recovery')).toBeVisible();
  await expect(page.getByRole('main').getByRole('link', { name: 'Work' })).toHaveAttribute('href', WORK_ROUTE);

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);

  const projectName = testInfo.project.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const screenshotPath = testInfo.outputPath(`graph-${projectName}.png`);
  await makeFixedNavStableForFullPageScreenshot(page);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`graph-${projectName}`, {
    path: screenshotPath,
    contentType: 'image/png',
  });
});

test('settings route renders workstation readiness and source contracts', async ({ page }, testInfo) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(workstationSettingsSurface),
    });
  });

  await page.goto(SETTINGS_ROUTE);

  await expect(page.getByRole('heading', { name: 'Workstation readiness' })).toBeVisible();
  await expect(page.getByText('Live source loaded')).toBeVisible();
  await expect(page.getByText('Live workstation settings')).toBeVisible();
  await expect(page.getByText('Runtime Prerequisites')).toBeVisible();
  await expect(page.getByText('Telegram operator')).toBeVisible();
  await expect(page.getByText('Local LLM')).toBeVisible();
  await expect(page.getByText(/provider deferred/i)).toBeVisible();
  await expect(page.getByText('/workspace/science-atlas')).toBeVisible();
  await expect(page.getByText('telegram loop status')).toBeVisible();
  await expect(page.getByText('remote_operator_telegram_loop.json')).toBeVisible();
  await expect(page.getByRole('main').getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', DASHBOARD_ROUTE);
  await expect(page.getByRole('main').getByRole('link', { name: 'Open decisions' })).toHaveAttribute('href', DECISIONS_ROUTE);

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);

  const projectName = testInfo.project.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const screenshotPath = testInfo.outputPath(`settings-${projectName}.png`);
  await makeFixedNavStableForFullPageScreenshot(page);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`settings-${projectName}`, {
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
  await expect(page.getByText('Live source loaded')).toBeVisible();
  await expect(page.locator('[data-dashboard-top-title]')).toHaveText('Live workstation intervention needed');
  await expect(page.locator('[data-dashboard-next-command]')).toHaveText('forager workstation status --json');
  await expect(page.locator('[data-dashboard-work-summary]').getByText('Live NanoClustering', { exact: true })).toBeVisible();
});

test('decisions route renders read-only action center from workstation surface', async ({ page }, testInfo) => {
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
  await expect(page.getByRole('button', { name: /Resolve closeout follow-up/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('heading', { name: 'Decision readout' })).toBeVisible();
  await expect(page.locator('[data-decision-assistant-answer]')).toContainText('Resolve closeout follow-up');
  await expect(page.locator('[data-decision-assistant-actions]').getByText('Refresh decision envelope')).toBeVisible();
  await expect(page.locator('[data-decision-assistant-refs]').getByText('decision:decision-closeout-followup')).toBeVisible();
  await expect(page.locator('[data-decision-assistant-prompts]').getByRole('button', { name: 'Can this advance?' })).toBeVisible();
  await expect(page.getByText('Action envelope preview')).toBeVisible();
  await expect(page.getByText('action_envelope.v1')).toBeVisible();
  await expect(page.getByText('action_envelope_receipt.v1')).toBeVisible();
  await expect(page.getByText('Authorization boundary')).toBeVisible();
  await page.getByText('Envelope boundary').click();
  await expect(page.getByText('forager offdesk decision show --json decision-closeout-followup')).toBeVisible();
  await page.getByText('CLI fallback').click();
  await expect(page.getByText('forager offdesk decisions --json')).toBeVisible();
  await page.getByRole('button', { name: 'Copy decision prompt: Can this advance?' }).click();
  await expect(page.locator('[data-decision-assistant-copy-status]')).toContainText(/Prompt (copied|ready)/);
  await page.getByRole('button', { name: /Runtime recovery required/ }).click();
  await expect(page.locator('[data-decision-detail]').getByRole('heading', { name: 'Runtime recovery required' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Runtime recovery required/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-decision-assistant-answer]')).toContainText('Runtime recovery required');

  const overflow = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);

  const projectName = testInfo.project.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const screenshotPath = testInfo.outputPath(`decisions-${projectName}.png`);
  await makeFixedNavStableForFullPageScreenshot(page);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: true });
  expect(screenshot.length).toBeGreaterThan(10_000);

  await testInfo.attach(`decisions-${projectName}`, {
    path: screenshotPath,
    contentType: 'image/png',
  });
});

test('decisions route can inspect runtime handoffs while decisions remain open', async ({ page }) => {
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(workstationWithRuntimeDispatch),
    });
  });

  await page.goto(DECISIONS_ROUTE);

  await expect(page.locator('[data-decision-detail]').getByRole('heading', { name: 'Resolve closeout follow-up' })).toBeVisible();
  await page.getByRole('button', { name: /Queue recovery task/ }).click();
  await expect(page.locator('[data-decision-detail]').getByRole('heading', { name: 'Runtime handoff' })).toBeVisible();
  await expect(page.getByText('Runtime dispatch can be queued only after the receipted decision closeout remains current.')).toBeVisible();
  await expect(page.getByText('runtime-preflight-recovery', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Queue recovery task/ })).toHaveAttribute('aria-pressed', 'true');
});

test('operator routes mark failed live surface loads as degraded fallback', async ({ page }) => {
  await page.route('**/forager-cli/review-surface.json', async (route) => {
    await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"offline"}' });
  });
  await page.route('**/forager-cli/workstation-surface.json', async (route) => {
    await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"offline"}' });
  });

  await page.goto(REVIEW_ROUTE);
  await expect(page.getByText('Live source failed')).toBeVisible();
  await expect(page.getByText('npm run export:review-surface')).toBeVisible();

  await page.goto(DASHBOARD_ROUTE);
  await expect(page.getByText('Live source failed')).toBeVisible();
  await expect(page.getByText('npm run export:workstation-surface')).toBeVisible();

  await page.goto(WORK_ROUTE);
  await expect(page.getByText('Live source failed')).toBeVisible();
  await expect(page.getByText('npm run export:workstation-surface')).toBeVisible();

  await page.goto(GRAPH_ROUTE);
  await expect(page.getByText('Live source failed')).toBeVisible();
  await expect(page.getByText('npm run export:workstation-surface')).toBeVisible();

  await page.goto(SETTINGS_ROUTE);
  await expect(page.getByText('Live source failed')).toBeVisible();
  await expect(page.getByText('npm run export:workstation-surface')).toBeVisible();

  await page.goto(DECISIONS_ROUTE);
  await expect(page.getByText('Live source failed')).toBeVisible();
  await expect(page.getByText('npm run export:workstation-surface')).toBeVisible();
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
    await expect(page.getByText('Work Portfolio', { exact: true })).toBeVisible();
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
      await expect(page.getByRole('complementary').getByText('Resolve archive review before accepting truth.', { exact: true })).toBeVisible();
      await expect(page.locator('[data-dashboard-top-title]')).toHaveText('Accepted truth recovery needed');
    }

    const overflow = await page.evaluate(() => ({
      width: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.width + 1);
  }
});

async function makeFixedNavStableForFullPageScreenshot(page) {
  await page.addStyleTag({
    content: 'nav.fixed { position: absolute !important; }',
  });
}
