import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { Activity, BookOpen, ChevronDown, ChevronRight, ClipboardList, FileSearch, FileText, LayoutDashboard, Scale, Shield, ShieldCheck, ScrollText, Wrench } from 'lucide-react'

const primaryItems = [
  { path: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { path: '/documents', icon: FileText, label: 'Documents' },
  { path: '/evidence', icon: Activity, label: 'Evidence Passages' },
  { path: '/candidates', icon: ClipboardList, label: 'Candidates & Extraction' },
  { path: '/proposed-rule', icon: Scale, label: 'Proposed Rule Review' },
  { path: '/governance', icon: Shield, label: 'Governance' },
  { path: '/audit', icon: ScrollText, label: 'Audit Trail' },
]

function NavItem({ item, collapsed }) {
  return <NavLink end to={item.path} title={collapsed ? item.label : undefined} className={({ isActive }) => `flex min-h-11 items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${isActive ? 'bg-blue-50 font-semibold text-blue-800' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'}`}>
    <item.icon aria-hidden="true" size={19} className="shrink-0" />{!collapsed && <span>{item.label}</span>}
  </NavLink>
}

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const [policyOpen, setPolicyOpen] = useState(true)
  const [toolsOpen, setToolsOpen] = useState(false)

  return <div className="flex min-h-screen bg-slate-50 text-slate-900">
    <aside className={`${collapsed ? 'w-[4.5rem]' : 'w-64'} flex shrink-0 flex-col border-r border-slate-200 bg-white transition-[width]`}>
      <div className="flex h-16 items-center justify-between border-b border-slate-200 px-3">
        {!collapsed && <div className="flex items-center gap-2.5"><span className="grid h-9 w-9 place-items-center rounded-xl bg-blue-700 text-white"><ShieldCheck size={20} /></span><span className="font-bold tracking-tight">TraceRule AI</span></div>}
        <button type="button" onClick={() => setCollapsed(value => !value)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}><ChevronRight className={`transition-transform ${collapsed ? '' : 'rotate-180'}`} size={18} /></button>
      </div>
      <nav aria-label="Primary navigation" className="flex-1 space-y-1 p-3">
        {!collapsed && <p className="px-3 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Workspace</p>}
        {primaryItems.map(item => <NavItem key={item.path} item={item} collapsed={collapsed} />)}
        <div className="pt-3">
          {!collapsed && <button type="button" onClick={() => setPolicyOpen(value => !value)} aria-expanded={policyOpen} className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Policy Management {policyOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>}
          {(collapsed || policyOpen) && <NavItem item={{ path: '/bank-policies', icon: BookOpen, label: 'Bank Policies' }} collapsed={collapsed} />}
        </div>
        <div className="pt-1">
          {!collapsed && <button type="button" onClick={() => setToolsOpen(value => !value)} aria-expanded={toolsOpen} className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Tools & Demonstrations {toolsOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>}
          {(collapsed || toolsOpen) && <NavItem item={{ path: '/document-analyzer', icon: FileSearch, label: 'Analyzer & synthetic demo' }} collapsed={collapsed} />}
        </div>
      </nav>
      {!collapsed && <div className="border-t border-slate-200 p-4 text-xs text-slate-500"><p className="font-medium text-slate-700">Local regulatory workspace</p><p className="mt-1">Review support; not an authority system</p></div>}
    </aside>
    <main className="min-w-0 flex-1 overflow-x-hidden"><Outlet /></main>
  </div>
}
