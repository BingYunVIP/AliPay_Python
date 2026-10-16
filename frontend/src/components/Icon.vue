<script setup>
import { Sun, Moon, Languages, Menu, X, ArrowRight, ArrowUpRight, ShieldCheck, Timer, Zap, Code2,
  Monitor, Smartphone, AppWindow, LayoutGrid, Check, Copy, Terminal, CreditCard, LogOut,
  RefreshCw, KeyRound, Search, CircleHelp, CheckCircle2, AlertCircle, ChevronRight,
  LoaderCircle, Mail, BookOpen, Store, Users, Globe, Network, LockKeyhole, Pause, Play, Plus,
  PanelLeft, LayoutDashboard, SlidersHorizontal, FlaskConical, ReceiptText, Webhook, Settings2,
  ScrollText, Wallet, TrendingUp, Download, CalendarDays, ChevronDown, ChevronLeft,
  MoreHorizontal, Bell, CheckCheck, CopyCheck, ArrowDownLeft, Circle, UserRound, FileText } from '@lucide/vue'
defineProps({ name: String, size: { type: [Number, String], default: 20 } })
const icons = { sun: Sun, moon: Moon, language: Languages, menu: Menu, x: X, arrow: ArrowRight, external: ArrowUpRight,
  shield: ShieldCheck, timer: Timer, zap: Zap, code: Code2, monitor: Monitor, phone: Smartphone, app: AppWindow,
  grid: LayoutGrid, check: Check, copy: Copy, terminal: Terminal, card: CreditCard, logout: LogOut,
  refresh: RefreshCw, key: KeyRound, search: Search, help: CircleHelp, success: CheckCircle2, alert: AlertCircle,
  chevron: ChevronRight, loader: LoaderCircle, mail: Mail, book: BookOpen, store: Store, users: Users,
  globe: Globe, network: Network, lock: LockKeyhole, pause: Pause, play: Play, plus: Plus,
  sidebar: PanelLeft, overview: LayoutDashboard, sliders: SlidersHorizontal, test: FlaskConical,
  receipt: ReceiptText, webhook: Webhook, settings: Settings2, logs: ScrollText, wallet: Wallet,
  trend: TrendingUp, download: Download, calendar: CalendarDays, down: ChevronDown, back: ChevronLeft,
  more: MoreHorizontal, bell: Bell, checks: CheckCheck, copied: CopyCheck, incoming: ArrowDownLeft,
  circle: Circle, user: UserRound, file: FileText }
</script>
<template><component :is="icons[name] || Code2" :size="size" :stroke-width="1.65" aria-hidden="true" /></template>
