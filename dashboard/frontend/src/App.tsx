import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Overview } from './pages/Overview'
import { LiveTrading } from './pages/LiveTrading'
import { StrategyDetail } from './pages/StrategyDetail'
import { ShadowTesting } from './pages/ShadowTesting'
import { Backtest } from './pages/Backtest'
import { SignalFeed } from './pages/SignalFeed'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Overview />} />
        <Route path="/live" element={<LiveTrading />} />
        <Route path="/strategy/:name" element={<StrategyDetail />} />
        <Route path="/shadow" element={<ShadowTesting />} />
        <Route path="/backtest" element={<Backtest />} />
        <Route path="/signals" element={<SignalFeed />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
