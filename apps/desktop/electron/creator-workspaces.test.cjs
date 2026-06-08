const test = require('node:test')
const assert = require('node:assert/strict')

const {
  normalizeCreatorWorkspacesManifest,
  parseCreatorWorkspacesJson
} = require('./creator-workspaces.cjs')

test('normalizes workspaces and defaults auth mode to none', () => {
  const manifest = normalizeCreatorWorkspacesManifest({
    workspaces: [
      {
        id: 'lufei',
        profile: 'lufei-creator-profile',
        displayName: '路飞设计沉思录',
        gatewayUrl: 'https://claudewiki.cn/hermes/'
      }
    ]
  }, 'test')

  assert.deepEqual(manifest, {
    source: 'test',
    version: 1,
    workspaces: [
      {
        id: 'lufei',
        profile: 'lufei-creator-profile',
        displayName: '路飞设计沉思录',
        gatewayUrl: 'https://claudewiki.cn/hermes',
        authMode: 'none'
      }
    ]
  })
})

test('accepts tenants as a compatibility alias', () => {
  const manifest = normalizeCreatorWorkspacesManifest({
    tenants: [
      {
        authMode: 'token',
        displayName: '求职咨询助手',
        gatewayUrl: 'https://claudewiki.cn/hermes',
        id: 'career-coach',
        profile: 'career-coach-copilot'
      }
    ]
  })

  assert.equal(manifest.workspaces[0].authMode, 'token')
  assert.equal(manifest.workspaces[0].id, 'career-coach')
})

test('rejects duplicate workspace ids', () => {
  assert.throws(
    () =>
      normalizeCreatorWorkspacesManifest({
        workspaces: [
          { id: 'lufei', profile: 'lufei', displayName: 'A', gatewayUrl: 'https://a.example.com' },
          { id: 'lufei', profile: 'lufei-b', displayName: 'B', gatewayUrl: 'https://b.example.com' }
        ]
      }),
    /Duplicate workspace id/
  )
})

test('parseCreatorWorkspacesJson reports invalid JSON', () => {
  assert.throws(() => parseCreatorWorkspacesJson('{nope'), /Invalid creator workspaces JSON/)
})

