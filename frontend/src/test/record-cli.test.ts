// @vitest-environment node
import { execFile } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { expect, it } from 'vitest'
import { appendClarification } from '../lib/questions'
import { recordingSchema } from '../lib/contracts'
import { clarification, response } from './fixtures'

it('captures real HTTP responses through the CLI, persists follow-ups, and refuses to overwrite', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'groundwork-recording-test-'))
  const questionFile = join(temporary, 'question.txt')
  const answersFile = join(temporary, 'answers.json')
  const output = join(temporary, 'capture.json')
  const calls: { path: string; body: unknown }[] = []
  const returned = [clarification(), { ...response(), captured_extension: 'preserve me' }]
  const server = createServer(async (request, reply) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    calls.push({
      path: request.url || '',
      body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
    })
    reply.setHeader('content-type', 'application/json')
    reply.end(JSON.stringify(returned[calls.length - 1]))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('test server address')
  const root = fileURLToPath(new URL('../../', import.meta.url))
  const args = [
    '--import',
    'tsx',
    join(root, 'scripts', 'record.ts'),
    '--question-file',
    questionFile,
    '--answers-file',
    answersFile,
    '--output',
    output,
    '--title',
    'TEST: HTTP capture',
    '--base-url',
    'http://127.0.0.1:' + address.port,
  ]
  try {
    await writeFile(questionFile, 'TEST: original')
    await writeFile(answersFile, JSON.stringify(['TEST: extra detail']))
    await promisify(execFile)(process.execPath, args, { cwd: root, timeout: 20_000 })
    const savedText = await readFile(output, 'utf8')
    const saved = recordingSchema.parse(JSON.parse(savedText))
    expect(calls).toEqual([
      { path: '/query', body: { question: 'TEST: original', debug: true } },
      {
        path: '/query',
        body: {
          question: appendClarification('TEST: original', 'TEST: extra detail'),
          debug: true,
        },
      },
    ])
    expect(saved.steps.map((step) => step.response)).toEqual(returned)
    expect(saved.kind).toBe('recorded')
    expect(saved.recorded_at).toMatch(/^\d{4}-\d{2}-\d{2}T/)
    await expect(
      promisify(execFile)(process.execPath, args, { cwd: root, timeout: 20_000 }),
    ).rejects.toThrow('기존 파일')
    expect(calls).toHaveLength(2)
    expect(await readFile(output, 'utf8')).toBe(savedText)
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
    await rm(temporary, { recursive: true, force: true })
  }
}, 30_000)
