/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0f1117',
        surface: '#1a1d27',
        border: '#2a2d3a',
        muted: '#94a3b8',
        live: '#22c55e',
        shadow: '#3b82f6',
        off: '#64748b',
        pos: '#22c55e',
        neg: '#ef4444',
        accent: '#6366f1',
      },
    },
  },
  plugins: [],
}
