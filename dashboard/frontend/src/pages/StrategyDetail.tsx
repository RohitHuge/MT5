import { useParams } from 'react-router-dom'
import { Placeholder } from './Placeholder'

export function StrategyDetail() {
  const { name } = useParams<{ name: string }>()
  return (
    <Placeholder
      title={name ?? 'Strategy'}
      note="Indicator chart, signal history and mode toggle land in Phase 3."
    />
  )
}
