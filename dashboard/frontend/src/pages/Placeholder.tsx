interface PageProps {
  title: string
  note: string
}

export function Placeholder({ title, note }: PageProps) {
  return (
    <div>
      <h1 className="text-lg font-semibold">{title}</h1>
      <p className="mt-2 text-sm text-muted">{note}</p>
    </div>
  )
}
