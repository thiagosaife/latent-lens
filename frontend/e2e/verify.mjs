// Browser verification of the LatentLens HITL flow. Drives the running dev app
// (npm run dev:all) with a headed Chrome so WebGL renders for real.
//
//   node e2e/verify.mjs            # headed (default)
//   HEADLESS=1 node e2e/verify.mjs # headless
//
// Screenshots → e2e/shots/*.png ; JSON summary → stdout.

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, 'shots')
mkdirSync(SHOTS, { recursive: true })
const URL = process.env.APP_URL || 'http://localhost:5173'

const out = { steps: {}, logs: [] }
const shot = (page, name) => page.screenshot({ path: join(SHOTS, name) })

const browser = await chromium.launch({ channel: 'chrome', headless: process.env.HEADLESS === '1' })
const page = await browser.newPage({ viewport: { width: 1280, height: 920 }, deviceScaleFactor: 2 })
page.on('console', (m) => out.logs.push(`[${m.type()}] ${m.text()}`))
page.on('pageerror', (e) => out.logs.push(`[pageerror] ${e.message}`))

try {
  await page.goto(URL, { waitUntil: 'domcontentloaded' })

  // 1) Editable plan renders with 5 steps
  await page.waitForSelector('.plan-item', { timeout: 10000 })
  out.steps.planStepCount = await page.locator('.plan-item').count()
  out.steps.planTitles = await page.locator('.plan-item .title').evaluateAll((els) => els.map((e) => e.value))
  await shot(page, '1-plan.png')

  // PROBE: delete the LAST step via the UI, then verify it never executes.
  // (Keep the delegated clean/cluster steps so the multi-agent traces render.)
  const lastItem = page.locator('.plan-item').last()
  out.steps.deletedTitle = await lastItem.locator('.title').inputValue()
  await lastItem.locator('.del').click()
  out.steps.planCountAfterDelete = await page.locator('.plan-item').count()

  // 2) Approve plan → profiling cards stream in
  await page.getByRole('button', { name: /Approve & run/ }).click()
  await page.waitForSelector('.stat-tile', { timeout: 12000 })
  await page.waitForTimeout(2600)
  out.steps.statTiles = await page.locator('.stat-tile').count()
  await shot(page, '2-profiling.png')

  // 2b) Multi-agent: the clean step is delegated to cleaning-agent. Wait for it
  // to return, then capture the attributed tool-call trace.
  await page.locator('.delegation .returned').first().waitFor({ timeout: 15000 })
  out.steps.delegationAgent = await page.locator('.delegation .badge').first().innerText()
  out.steps.cleaningTrace = (await page.locator('.delegation').first().innerText()).replace(/\s+/g, ' ').trim()
  await page.locator('.delegation').first().screenshot({ path: join(SHOTS, '6-delegation.png') })

  // 3) Approval gate with cost/time estimate
  await page.waitForSelector('.gate', { timeout: 15000 })
  out.steps.gateText = (await page.locator('.gate').innerText()).replace(/\s+/g, ' ').trim()
  await shot(page, '3-gate.png')

  // 4) Approve gate → WebGL embedding renders
  await page.getByRole('button', { name: /Approve & continue/ }).click()
  await page.waitForSelector('.embed canvas', { timeout: 15000 })
  await page.waitForTimeout(3500)
  // backing-store size (non-zero = sized & drawable); visual proof is the screenshot
  out.steps.canvas = await page.evaluate(() => {
    const c = document.querySelector('.embed canvas')
    return c ? `${c.width}x${c.height}` : 'no-canvas'
  })
  out.steps.embedRef = await page.locator('.embed .ref').innerText().catch(() => null)
  await shot(page, '4-embedding.png')

  // 5) Lasso: toggle lasso mode, then drag a loop → selection bar appears.
  // Scroll the embedding into view first so drag coords land inside the canvas.
  await page.locator('.embed').scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await page.locator('.lasso-btn').click()
  const box = await page.locator('.embed canvas').boundingBox()
  if (box) {
    const pts = [
      [0.32, 0.3],
      [0.72, 0.32],
      [0.72, 0.72],
      [0.3, 0.7],
      [0.32, 0.31],
    ]
    await page.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1])
    await page.mouse.down()
    for (const [fx, fy] of pts.slice(1)) {
      await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 })
    }
    await page.mouse.up()
  }
  await page.waitForTimeout(1800)
  out.steps.selBarVisible = await page.locator('.sel-bar').isVisible().catch(() => false)
  out.steps.selBarText = await page.locator('.sel-bar').innerText().catch(() => null)
  await shot(page, '5-lasso.png')

  // 6) Observability: open the trace console, inspect a tool call's I/O
  await page.locator('.trace .bar').click()
  await page.waitForSelector('.trace.open .row', { timeout: 5000 })
  out.steps.traceId = await page.locator('.trace .tid').innerText()
  out.steps.traceMeta = await page.locator('.trace .meta').innerText()
  out.steps.traceDurations = await page.locator('.trace .dur').allInnerTexts()
  const toolRow = page.locator('.trace .row.tool.hasDetail').first()
  await toolRow.click()
  out.steps.traceDetailVisible = await page.locator('.trace .detail').first().isVisible().catch(() => false)
  out.steps.traceDetail = await page.locator('.trace .detail').first().innerText().catch(() => null)
  await page.locator('.trace').screenshot({ path: join(SHOTS, '7-trace.png') })

  // Which steps actually executed (the deleted last step should be absent)
  out.steps.executedSteps = await page.locator('.step .step-title').allInnerTexts()
  out.steps.delegatedAgents = await page.locator('.agent-tag').allInnerTexts()
  out.ok = true
} catch (err) {
  out.ok = false
  out.error = err instanceof Error ? err.message : String(err)
  await shot(page, 'error.png').catch(() => {})
} finally {
  console.log(JSON.stringify(out, null, 2))
  await browser.close()
}
