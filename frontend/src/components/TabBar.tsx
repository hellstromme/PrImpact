export interface Tab {
  id: string
  label: string
  icon?: string
}

export default function TabBar({
  tabs,
  activeTab,
  onSelect,
}: {
  tabs: Tab[]
  activeTab: string
  onSelect: (id: string) => void
}) {
  return (
    <div className="flex border-b border-outline-variant/10 bg-surface-container-low">
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab
        return (
          <button
            key={tab.id}
            onClick={() => onSelect(tab.id)}
            className={[
              'flex items-center gap-2 px-5 py-3 text-xs font-mono uppercase tracking-widest transition-colors border-b-2',
              isActive
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface hover:border-outline-variant',
            ].join(' ')}
          >
            {tab.icon && (
              <span className="material-symbols-outlined text-[16px] leading-none">
                {tab.icon}
              </span>
            )}
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
