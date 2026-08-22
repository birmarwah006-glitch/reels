import { chromium } from 'playwright'
const URL_TO_ADD = process.argv[2]
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1280, height: 900 } })
const errs = []
p.on('pageerror', e => errs.push('pageerror: ' + e.message))
p.on('console', m => { if (m.type()==='error' && !/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()) })

await p.goto('http://localhost:5173/add', { waitUntil: 'networkidle' })
await p.fill('#yt', URL_TO_ADD)
const btn = p.getByRole('button', { name: /Turn this into Meals/ })
console.log('button present:', await btn.isVisible(), '| enabled:', await btn.isEnabled())
await btn.click()
await p.waitForURL(/\/generating\//, { timeout: 30000 })
console.log('navigated to:', new URL(p.url()).pathname)
await p.waitForTimeout(6000)
const body = await p.locator('body').innerText()
console.log('--- page ---')
console.log(body.split('\n').filter(Boolean).slice(2, 20).join('\n'))
console.log('--- errors:', errs.length, errs.slice(0,3).join(' | '))
await p.screenshot({ path: '.smoke/generating.png' })
await b.close()
