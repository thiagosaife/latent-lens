// One-command dev: runs the mock agent server and the Vite dev server together,
// prefixing each line and tearing both down on exit. No dependencies.

import { spawn } from 'node:child_process'

const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const reset = '\x1b[0m'

const targets = [
  { name: 'mock', args: ['run', 'mock'], color: '\x1b[36m' },
  { name: 'vite', args: ['run', 'dev'], color: '\x1b[35m' },
]

const children = targets.map(({ name, args, color }) => {
  const child = spawn(npm, args, { stdio: ['ignore', 'pipe', 'pipe'] })
  const tag = `${color}[${name}]${reset} `
  const pipe = (src, dst) =>
    src.on('data', (buf) => {
      for (const line of buf.toString().split('\n')) {
        if (line.length) dst.write(tag + line + '\n')
      }
    })
  pipe(child.stdout, process.stdout)
  pipe(child.stderr, process.stderr)
  child.on('exit', (code) => {
    console.log(`${tag}exited (code ${code ?? 0})`)
    shutdown()
  })
  return child
})

let down = false
function shutdown() {
  if (down) return
  down = true
  for (const child of children) {
    try {
      child.kill('SIGTERM')
    } catch {
      // already gone
    }
  }
  setTimeout(() => process.exit(0), 100)
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
