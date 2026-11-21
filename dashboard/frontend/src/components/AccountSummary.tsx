import { useMT5Store } from '../store/mt5Store'

function money(value: number | undefined) {
  if (value === undefined || value === null) return '—'
  return `$${value.toFixed(2)}`
}

export function AccountSummary() {
  const account = useMT5Store((s) => s.account)

  if (!account) {
    return <span className="text-xs text-muted">Bridge offline</span>
  }

  const up = account.equity >= account.balance
  const level =
    account.margin_level_pct !== null && account.margin_level_pct !== undefined
      ? `${account.margin_level_pct.toFixed(0)}%`
      : '—'

  return (
    <div className="flex items-center gap-4 text-xs">
      <span className="text-muted">
        Balance: <span className="text-slate-200">{money(account.balance)}</span>
      </span>
      <span className="text-muted">
        Equity:{' '}
        <span className={up ? 'text-pos' : 'text-neg'}>{money(account.equity)}</span>
      </span>
      <span className="text-muted">
        Free: <span className="text-slate-200">{money(account.free_margin)}</span>
      </span>
      <span className="text-muted">
        Level: <span className="text-slate-200">{level}</span>
      </span>
    </div>
  )
}
