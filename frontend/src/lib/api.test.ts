import { api } from './api'

describe('api — getAnnotations error propagation', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('throws on 404 response', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('{"detail":"not found"}', { status: 404 })
    )
    await expect(api.getAnnotations('run1')).rejects.toThrow('HTTP 404')
  })

  it('throws on 500 response', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('Internal Server Error', { status: 500 })
    )
    await expect(api.getAnnotations('run1')).rejects.toThrow('HTTP 500')
  })
})

describe('api — saveAnnotation error propagation', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('throws on 422 validation error', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('{"detail":"validation error"}', { status: 422 })
    )
    await expect(
      api.saveAnnotation('run1', 'sig123', { muted: true })
    ).rejects.toThrow('HTTP 422')
  })

  it('throws on 404 signal not found', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('{"detail":"Signal not found in run"}', { status: 404 })
    )
    await expect(
      api.saveAnnotation('run1', 'sig123', { muted: true })
    ).rejects.toThrow('HTTP 404')
  })
})
