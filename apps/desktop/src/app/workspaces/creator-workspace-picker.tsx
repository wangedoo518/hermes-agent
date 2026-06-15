import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { DesktopCreatorWorkspace, DesktopCreatorWorkspacesManifest } from '@/global'
import { AlertCircle, Check, Globe, KeyRound, Layers3, Loader2, LogIn, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'

type GateStatus = 'error' | 'loading' | 'needs-selection' | 'ready'

interface CreatorWorkspaceGate {
  applyingId: null | string
  bootEnabled: boolean
  error: null | string
  manifest: DesktopCreatorWorkspacesManifest | null
  pickerOpen: boolean
  reload: () => Promise<void>
  selectedWorkspace: DesktopCreatorWorkspace | null
  selectWorkspace: (workspace: DesktopCreatorWorkspace, token?: string) => Promise<void>
  setPickerOpen: (open: boolean) => void
  status: GateStatus
}

const EMPTY_MANIFEST: DesktopCreatorWorkspacesManifest = {
  source: 'none',
  version: 1,
  workspaces: []
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function useCreatorWorkspaceGate(): CreatorWorkspaceGate {
  const [manifest, setManifest] = useState<DesktopCreatorWorkspacesManifest | null>(null)
  const [selectedId, setSelectedId] = useState<null | string>(null)
  const [status, setStatus] = useState<GateStatus>('loading')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [applyingId, setApplyingId] = useState<null | string>(null)
  const [error, setError] = useState<null | string>(null)

  const reload = useCallback(async () => {
    const desktop = window.hermesDesktop

    if (!desktop?.creatorWorkspaces) {
      setManifest(EMPTY_MANIFEST)
      setSelectedId(null)
      setStatus('ready')
      setPickerOpen(false)
      setError(null)

      return
    }

    setStatus('loading')
    setError(null)

    try {
      const [nextManifest, selection] = await Promise.all([
        desktop.creatorWorkspaces.list(),
        desktop.creatorWorkspaces.getSelection()
      ])

      const workspaces = nextManifest.workspaces ?? []
      const selected = workspaces.find(workspace => workspace.id === selection.workspaceId) ?? null

      setManifest(nextManifest)
      setSelectedId(selected?.id ?? null)

      if (workspaces.length === 0 || selected) {
        setStatus('ready')
        setPickerOpen(false)
      } else {
        setStatus('needs-selection')
        setPickerOpen(true)
      }
    } catch (err) {
      setStatus('error')
      setPickerOpen(true)
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const selectedWorkspace = useMemo(() => {
    if (!manifest || !selectedId) {
      return null
    }

    return manifest.workspaces.find(workspace => workspace.id === selectedId) ?? null
  }, [manifest, selectedId])

  const selectWorkspace = useCallback(async (workspace: DesktopCreatorWorkspace, token = '') => {
    const desktop = window.hermesDesktop

    if (!desktop?.creatorWorkspaces) {
      throw new Error('Desktop workspace bridge is unavailable.')
    }

    setApplyingId(workspace.id)
    setError(null)

    try {
      // Pin this profile to its gateway (per-profile remote override)...
      await desktop.saveConnectionConfig({
        mode: 'remote',
        profile: workspace.profile,
        remoteAuthMode: workspace.authMode,
        remoteToken: workspace.authMode === 'token' ? token : undefined,
        remoteUrl: workspace.gatewayUrl
      })

      // ...AND set the GLOBAL connection to remote. On a fresh install the
      // active profile isn't resolved yet when boot is enabled, so a
      // per-profile override alone can be missed by resolveRemoteBackend(
      // primaryProfileKey()) and the launcher falls through to the local
      // backend bootstrap (which clones hermes-agent from GitHub and fails on
      // networks that can't reach github.com). Setting the global remote makes
      // first-launch boot connect to the gateway regardless of profile timing.
      await desktop.saveConnectionConfig({
        mode: 'remote',
        remoteAuthMode: workspace.authMode,
        remoteToken: workspace.authMode === 'token' ? token : undefined,
        remoteUrl: workspace.gatewayUrl
      })

      if (workspace.authMode === 'oauth') {
        const result = await desktop.oauthLoginConnectionConfig(workspace.gatewayUrl)

        if (!result?.connected) {
          throw new Error('Sign in did not complete. Try again to connect this workspace.')
        }
      }

      await desktop.creatorWorkspaces.setSelection(workspace.id)
      setSelectedId(workspace.id)
      setStatus('ready')
      setPickerOpen(false)
      await desktop.profile.set(workspace.profile)
    } catch (err) {
      setError(errorMessage(err))
      setStatus('needs-selection')
      setPickerOpen(true)
    } finally {
      setApplyingId(null)
    }
  }, [])

  return {
    applyingId,
    bootEnabled: status === 'ready',
    error,
    manifest,
    pickerOpen,
    reload,
    selectedWorkspace,
    selectWorkspace,
    setPickerOpen,
    status
  }
}

export function CreatorWorkspacePickerOverlay({ gate }: { gate: CreatorWorkspaceGate }) {
  const workspaces = gate.manifest?.workspaces ?? []
  const required = gate.status === 'needs-selection' || gate.status === 'error'
  const open = gate.pickerOpen || gate.status === 'loading' || required
  const [tokenWorkspaceId, setTokenWorkspaceId] = useState<null | string>(null)
  const [tokens, setTokens] = useState<Record<string, string>>({})
  const [tokenError, setTokenError] = useState<null | string>(null)

  if (!open || workspaces.length === 0 && gate.status === 'ready') {
    return null
  }

  return (
    // While this gate blocks the backend boot, the onboarding (z-1300) and
    // gateway-connecting (z-1200) overlays are both mounted and full-screen —
    // the picker must sit above them or the app looks stuck on "Starting
    // Hermes… 2%". Stay below boot-failure/install (z-1400) and the error
    // boundary (z-1500).
    <div className="fixed inset-0 z-[1350] grid place-items-center bg-(--ui-bg-primary)/96 px-6 py-8 text-(--ui-text-primary) backdrop-blur-xl [-webkit-app-region:no-drag]">
      <div className="w-full max-w-4xl">
        <div className="mb-7 flex items-start justify-between gap-4">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-(--ui-stroke-tertiary) px-2.5 py-1 text-[0.6875rem] font-medium uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
              <Layers3 className="size-3.5" />
              Creator Workspace
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-(--ui-text-primary)">Choose your workspace</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-(--ui-text-tertiary)">
              Pick the creator space this Desktop should load. Secured gateways open the official Hermes sign-in window.
            </p>
          </div>
          {!required && gate.selectedWorkspace ? (
            <Button onClick={() => gate.setPickerOpen(false)} size="sm" variant="ghost">
              Close
            </Button>
          ) : null}
        </div>

        {gate.status === 'loading' ? (
          <div className="flex min-h-52 items-center justify-center rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)">
            <div className="flex items-center gap-2 text-sm text-(--ui-text-tertiary)">
              <Loader2 className="size-4 animate-spin" />
              Loading workspaces...
            </div>
          </div>
        ) : gate.status === 'error' ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              <div>
                <div className="font-medium">Could not load workspaces</div>
                <div className="mt-1 leading-5">{gate.error}</div>
                <Button className="mt-4" onClick={() => void gate.reload()} size="sm" variant="outline">
                  <RefreshCw />
                  Retry
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {workspaces.map(workspace => {
              const selected = gate.selectedWorkspace?.id === workspace.id
              const applying = gate.applyingId === workspace.id
              const needsToken = workspace.authMode === 'token'
              const needsSignIn = workspace.authMode === 'oauth'
              const tokenOpen = tokenWorkspaceId === workspace.id
              const token = tokens[workspace.id] ?? ''

              const choose = () => {
                setTokenError(null)

                if (needsToken) {
                  setTokenWorkspaceId(workspace.id)

                  return
                }

                void gate.selectWorkspace(workspace)
              }

              const connectWithToken = () => {
                const trimmed = token.trim()

                if (!trimmed) {
                  setTokenError('Paste the workspace token first.')

                  return
                }

                setTokenError(null)
                void gate.selectWorkspace(workspace, trimmed)
              }

              return (
                <div
                  className={cn(
                    'group min-h-40 rounded-lg border p-4 text-left transition',
                    selected
                      ? 'border-primary/60 bg-primary/10 shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--primary)_30%,transparent)]'
                      : 'border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) hover:border-(--ui-stroke-secondary) hover:bg-(--ui-bg-tertiary)',
                    gate.applyingId && !applying && 'opacity-55'
                  )}
                  key={workspace.id}
                >
                  <button
                    className="w-full text-left"
                    disabled={Boolean(gate.applyingId)}
                    onClick={choose}
                    type="button"
                  >
                    <div className="flex items-start gap-3">
                      <div className="grid size-9 shrink-0 place-items-center rounded-md bg-(--ui-bg-quaternary) text-(--ui-text-secondary)">
                        {applying ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : needsSignIn ? (
                          <LogIn className="size-4" />
                        ) : needsToken ? (
                          <KeyRound className="size-4" />
                        ) : (
                          <Globe className="size-4" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <div className="truncate text-sm font-semibold text-(--ui-text-primary)">
                            {workspace.displayName}
                          </div>
                          {selected ? <Check className="size-4 shrink-0 text-primary" /> : null}
                        </div>
                        <div className="mt-1 truncate font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
                          {workspace.profile}
                        </div>
                      </div>
                    </div>
                  </button>
                  {workspace.description ? (
                    <p className="mt-4 line-clamp-2 text-xs leading-5 text-(--ui-text-tertiary)">
                      {workspace.description}
                    </p>
                  ) : null}
                  {needsToken && tokenOpen ? (
                    <div className="mt-4 space-y-2 rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) p-3">
                      <Input
                        autoFocus
                        disabled={Boolean(gate.applyingId)}
                        onChange={event => {
                          setTokens(previous => ({ ...previous, [workspace.id]: event.target.value }))
                          setTokenError(null)
                        }}
                        onKeyDown={event => {
                          if (event.key === 'Enter') {
                            connectWithToken()
                          }
                        }}
                        placeholder="Workspace token"
                        type="password"
                        value={token}
                      />
                      {tokenError ? <div className="text-xs text-destructive">{tokenError}</div> : null}
                      <Button disabled={Boolean(gate.applyingId)} onClick={connectWithToken} size="sm">
                        {applying ? <Loader2 className="size-4 animate-spin" /> : <KeyRound />}
                        Connect
                      </Button>
                    </div>
                  ) : null}
                  <div className="mt-4 flex items-center justify-between gap-3 border-t border-(--ui-stroke-tertiary) pt-3">
                    <span className="truncate font-mono text-[0.6875rem] text-(--ui-text-quaternary)">
                      {workspace.gatewayUrl}
                    </span>
                    <span className="shrink-0 rounded-full bg-(--ui-bg-quaternary) px-2 py-0.5 text-[0.625rem] uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                      {workspace.authMode === 'oauth'
                        ? 'sign in'
                        : workspace.authMode === 'token'
                          ? 'token'
                          : 'no login'}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
