import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { DesktopCreatorWorkspace, DesktopCreatorWorkspacesManifest } from '@/global'
import { AlertCircle, Check, Globe, Layers3, Loader2, RefreshCw } from '@/lib/icons'
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
  selectWorkspace: (workspace: DesktopCreatorWorkspace) => Promise<void>
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

  const selectWorkspace = useCallback(async (workspace: DesktopCreatorWorkspace) => {
    const desktop = window.hermesDesktop

    if (!desktop?.creatorWorkspaces) {
      throw new Error('Desktop workspace bridge is unavailable.')
    }

    setApplyingId(workspace.id)
    setError(null)

    try {
      await desktop.saveConnectionConfig({
        mode: 'remote',
        profile: workspace.profile,
        remoteAuthMode: workspace.authMode,
        remoteUrl: workspace.gatewayUrl
      })
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

  if (!open || workspaces.length === 0 && gate.status === 'ready') {
    return null
  }

  return (
    <div className="fixed inset-0 z-80 grid place-items-center bg-(--ui-bg-primary)/96 px-6 py-8 text-(--ui-text-primary) backdrop-blur-xl [-webkit-app-region:no-drag]">
      <div className="w-full max-w-4xl">
        <div className="mb-7 flex items-start justify-between gap-4">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-(--ui-stroke-tertiary) px-2.5 py-1 text-[0.6875rem] font-medium uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
              <Layers3 className="size-3.5" />
              Creator Workspace
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-(--ui-text-primary)">Choose your workspace</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-(--ui-text-tertiary)">
              Pick the creator space this Desktop should load. Hermes will connect directly to that workspace gateway.
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

              return (
                <button
                  className={cn(
                    'group min-h-40 rounded-lg border p-4 text-left transition',
                    selected
                      ? 'border-primary/60 bg-primary/10 shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--primary)_30%,transparent)]'
                      : 'border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) hover:border-(--ui-stroke-secondary) hover:bg-(--ui-bg-tertiary)',
                    gate.applyingId && !applying && 'opacity-55'
                  )}
                  disabled={Boolean(gate.applyingId)}
                  key={workspace.id}
                  onClick={() => void gate.selectWorkspace(workspace)}
                  type="button"
                >
                  <div className="flex items-start gap-3">
                    <div className="grid size-9 shrink-0 place-items-center rounded-md bg-(--ui-bg-quaternary) text-(--ui-text-secondary)">
                      {applying ? <Loader2 className="size-4 animate-spin" /> : <Globe className="size-4" />}
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
                  {workspace.description ? (
                    <p className="mt-4 line-clamp-2 text-xs leading-5 text-(--ui-text-tertiary)">
                      {workspace.description}
                    </p>
                  ) : null}
                  <div className="mt-4 flex items-center justify-between gap-3 border-t border-(--ui-stroke-tertiary) pt-3">
                    <span className="truncate font-mono text-[0.6875rem] text-(--ui-text-quaternary)">
                      {workspace.gatewayUrl}
                    </span>
                    <span className="shrink-0 rounded-full bg-(--ui-bg-quaternary) px-2 py-0.5 text-[0.625rem] uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                      {workspace.authMode === 'none' ? 'no login' : workspace.authMode}
                    </span>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
