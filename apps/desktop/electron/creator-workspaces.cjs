const { normAuthMode, normalizeRemoteBaseUrl } = require('./connection-config.cjs')

const WORKSPACE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/
const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

function stringValue(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function normalizeCreatorWorkspace(raw, index = 0) {
  if (!raw || typeof raw !== 'object') {
    throw new Error(`Workspace #${index + 1} must be an object.`)
  }

  const id = stringValue(raw.id)
  const profile = stringValue(raw.profile)
  const displayName = stringValue(raw.displayName || raw.display_name)
  const gatewayUrl = stringValue(raw.gatewayUrl || raw.gateway_url)

  if (!WORKSPACE_ID_RE.test(id)) {
    throw new Error(`Workspace #${index + 1} has an invalid id.`)
  }
  if (!PROFILE_NAME_RE.test(profile)) {
    throw new Error(`Workspace "${id}" has an invalid profile.`)
  }
  if (!displayName) {
    throw new Error(`Workspace "${id}" is missing displayName.`)
  }
  if (!gatewayUrl) {
    throw new Error(`Workspace "${id}" is missing gatewayUrl.`)
  }

  const normalized = {
    id,
    profile,
    displayName,
    gatewayUrl: normalizeRemoteBaseUrl(gatewayUrl),
    authMode: normAuthMode(raw.authMode || raw.auth_mode || 'oauth')
  }

  const description = stringValue(raw.description)
  if (description) {
    normalized.description = description
  }

  return normalized
}

function normalizeCreatorWorkspacesManifest(raw, source = 'unknown') {
  const sourceLabel = stringValue(source) || 'unknown'
  const payload = Array.isArray(raw) ? { workspaces: raw } : raw

  if (!payload || typeof payload !== 'object') {
    throw new Error('Creator workspaces manifest must be an object.')
  }

  const list = Array.isArray(payload.workspaces)
    ? payload.workspaces
    : Array.isArray(payload.tenants)
      ? payload.tenants
      : null

  if (!list) {
    throw new Error('Creator workspaces manifest must include a workspaces array.')
  }

  const seen = new Set()
  const workspaces = list.map((entry, index) => {
    const workspace = normalizeCreatorWorkspace(entry, index)
    if (seen.has(workspace.id)) {
      throw new Error(`Duplicate workspace id: ${workspace.id}`)
    }
    seen.add(workspace.id)
    return workspace
  })

  return {
    source: sourceLabel,
    version: Number(payload.version) || 1,
    workspaces
  }
}

function parseCreatorWorkspacesJson(text, source = 'json') {
  let parsed
  try {
    parsed = JSON.parse(String(text || ''))
  } catch (error) {
    throw new Error(`Invalid creator workspaces JSON: ${error.message}`)
  }

  return normalizeCreatorWorkspacesManifest(parsed, source)
}

module.exports = {
  normalizeCreatorWorkspace,
  normalizeCreatorWorkspacesManifest,
  parseCreatorWorkspacesJson
}
