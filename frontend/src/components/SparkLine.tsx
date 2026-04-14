/** Micro bar-chart with amber→red gradient, used in blast radius / run list rows. */
export default function SparkLine({
  values,
  width = 64,
  height = 20,
}: {
  values: (number | null)[]
  width?: number
  height?: number
}) {
  const valid = values.filter((v): v is number => v !== null && v >= 0)
  if (valid.length === 0) return <span className="text-on-surface-variant text-xs font-mono">—</span>

  const max = Math.max(...valid, 1)
  const barW = Math.max(1, Math.floor(width / valid.length) - 1)

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-label="churn sparkline"
      role="img"
    >
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#fabc45" />
          <stop offset="100%" stopColor="#fe554d" />
        </linearGradient>
      </defs>
      {valid.map((v, i) => {
        const barH = Math.max(2, Math.round((v / max) * (height - 2)))
        return (
          <rect
            key={i}
            x={i * (barW + 1)}
            y={height - barH}
            width={barW}
            height={barH}
            fill="url(#spark-grad)"
            rx={1}
          />
        )
      })}
    </svg>
  )
}
