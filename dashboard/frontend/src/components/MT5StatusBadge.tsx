import { useMT5Store } from '../store/mt5Store'

export function MT5StatusBadge() {
  const connected = useMT5Store((s) => s.connected)
  const build = useMT5Store((s) => s.build)
  const login = useMT5Store((s) => s.account?.login)

  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className={`h-2 w-2 rounded-full ${
          connected ? 'bg-live shadow-[0_0_6px_#22c55e]' : 'bg-neg'
        }`}
      />
      <span className={connected ? 'text-live' : 'text-neg'}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
      {build !== null && <span className="text-muted">build {build}</span>}
      {login !== undefined && (
        <span className="text-muted">#{login}</span>
      )}
    </div>
  )
}
