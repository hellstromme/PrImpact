/** Syntax-highlighted code block with optional line highlighting. */
export default function CodeBlock({
  lines,
  startLine = 1,
  highlightLine,
  language = 'text',
}: {
  lines: string[]
  startLine?: number
  highlightLine?: number
  language?: string
}) {
  return (
    <div className="rounded-lg overflow-hidden bg-surface-container-lowest border border-outline-variant/10 text-sm">
      <div className="flex items-center justify-between px-4 py-2 bg-surface-container-low border-b border-outline-variant/10">
        <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
          {language}
        </span>
        <span className="font-mono text-[10px] text-on-surface-variant">
          {lines.length > 0 ? `L${startLine}–${startLine + lines.length - 1}` : `L${startLine}`}
        </span>
      </div>
      <pre className="overflow-x-auto p-4 m-0">
        {lines.map((line, i) => {
          const lineNo = startLine + i
          const isHighlight = highlightLine === lineNo
          return (
            <div
              key={lineNo}
              className={[
                'flex gap-4 font-mono text-xs leading-5 px-0',
                isHighlight
                  ? 'bg-tertiary-container/20 -mx-4 px-4 border-l-2 border-tertiary'
                  : '',
              ].join(' ')}
            >
              <span className="text-on-surface-variant select-none w-8 shrink-0 text-right">
                {lineNo}
              </span>
              <span className={isHighlight ? 'text-on-surface' : 'text-on-surface-variant'}>
                {line || ' '}
              </span>
            </div>
          )
        })}
      </pre>
    </div>
  )
}
