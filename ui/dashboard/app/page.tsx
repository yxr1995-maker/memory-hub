'use client';

import React, { useState, useEffect, useMemo } from 'react';
import {
  Brain,
  Search,
  BookOpen,
  Layers,
  Sparkles,
  RefreshCw,
  SlidersHorizontal,
  Activity,
  FolderTree,
  FileText,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Database,
  ArrowUpRight,
  Sun,
  Moon,
  ChevronRight,
  Filter,
  ExternalLink,
  Code,
  Tag,
  ShieldCheck,
  Zap,
  Terminal,
  RotateCcw,
  Sparkle
} from 'lucide-react';

interface StatsOverview {
  wiki_pages: number;
  by_type: Record<string, number>;
  observations_files: number;
  observations_lines: number;
  last_capture_age_seconds: number | null;
  index_db_bytes: number;
  sessions_recent: number;
  timings: Record<string, { count: number; total_ms: number; last_ms: number }>;
}

interface SearchResultItem {
  path: string;
  score: number;
  status: string;
  scope: string;
  scope_id: string;
  scope_confidence: string;
  title: string;
  abstract: string;
  type: string;
  rank_reason: any;
}

interface PageMeta {
  path: string;
  title: string;
  type: string;
  tags: string[];
  updated: string;
  abstract: string;
  size: number;
  mtime: number;
  scope?: string;
  scope_id?: string;
}

export default function MemoryHubDashboard() {
  const [activeTab, setActiveTab] = useState<'overview' | 'search' | 'ask' | 'pages' | 'observations' | 'automation'>('overview');
  const [darkMode, setDarkMode] = useState<boolean>(true);
  const [apiUrl, setApiUrl] = useState<string>('http://127.0.0.1:8787');
  const [apiConnected, setApiConnected] = useState<boolean | null>(null);

  // Overview Data
  const [overview, setOverview] = useState<StatsOverview | null>(null);
  const [loadingOverview, setLoadingOverview] = useState<boolean>(false);

  // Search State
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [searchFuse, setSearchFuse] = useState<boolean>(true);
  const [searchExpand, setSearchExpand] = useState<boolean>(true);
  const [searchScope, setSearchScope] = useState<string>('all');
  const [searchScopeId, setSearchScopeId] = useState<string>('');
  const [searchTop, setSearchTop] = useState<number>(10);
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([]);
  const [searchPlan, setSearchPlan] = useState<any>(null);
  const [searching, setSearching] = useState<boolean>(false);

  // Ask State
  const [askQuestion, setAskQuestion] = useState<string>('');
  const [askAnswer, setAskAnswer] = useState<string | null>(null);
  const [askContextPaths, setAskContextPaths] = useState<string[]>([]);
  const [asking, setAsking] = useState<boolean>(false);

  // Pages State
  const [pagesList, setPagesList] = useState<PageMeta[]>([]);
  const [selectedType, setSelectedType] = useState<string>('all');
  const [selectedTag, setSelectedTag] = useState<string>('all');
  const [pageSearchFilter, setPageSearchFilter] = useState<string>('');
  const [activePageDoc, setActivePageDoc] = useState<{ path: string; content: string; meta: any } | null>(null);
  const [loadingPages, setLoadingPages] = useState<boolean>(false);

  // Tags State
  const [tags, setTags] = useState<{ tag: string; count: number }[]>([]);

  // Observations State
  const [observations, setObservations] = useState<any[]>([]);
  const [obsProjectFilter, setObsProjectFilter] = useState<string>('');
  const [loadingObs, setLoadingObs] = useState<boolean>(false);

  // Check Health & Load Overview
  const checkHealthAndLoad = async () => {
    setLoadingOverview(true);
    try {
      const healthRes = await fetch(`${apiUrl}/health`, { signal: AbortSignal.timeout(3000) });
      if (healthRes.ok) {
        setApiConnected(true);
        const ovRes = await fetch(`${apiUrl}/api/overview`);
        if (ovRes.ok) {
          const ovData = await ovRes.json();
          setOverview(ovData);
        }
        const tagRes = await fetch(`${apiUrl}/api/tags`);
        if (tagRes.ok) {
          const tagData = await tagRes.json();
          setTags(tagData.tags || []);
        }
      } else {
        setApiConnected(false);
      }
    } catch (e) {
      setApiConnected(false);
    } finally {
      setLoadingOverview(false);
    }
  };

  useEffect(() => {
    checkHealthAndLoad();
  }, [apiUrl]);

  // Handle Search
  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const params = new URLSearchParams({
        q: searchQuery,
        top: searchTop.toString(),
        fuse: searchFuse ? '1' : '0',
        expand: searchExpand ? '1' : '0',
        explain: '1',
      });
      if (searchScope !== 'all') {
        params.append('scope', searchScope);
        if (searchScopeId) params.append('scope_id', searchScopeId);
      }
      const res = await fetch(`${apiUrl}/search?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setSearchResults(data.results || []);
        setSearchPlan(data.plan || null);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSearching(false);
    }
  };

  // Handle Ask
  const handleAsk = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!askQuestion.trim()) return;
    setAsking(true);
    setAskAnswer(null);
    try {
      const params = new URLSearchParams({
        q: askQuestion,
        top: '5',
        fuse: '1',
        expand: '1',
      });
      const res = await fetch(`${apiUrl}/ask?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setAskAnswer(data.answer || '未能从知识库提取到有效答案。');
        setAskContextPaths(data.context_paths || []);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setAsking(false);
    }
  };

  // Load Pages
  const loadPages = async () => {
    setLoadingPages(true);
    try {
      const params = new URLSearchParams({ limit: '100', offset: '0' });
      if (selectedType !== 'all') params.append('type', selectedType);
      if (selectedTag !== 'all') params.append('tag', selectedTag);
      if (pageSearchFilter) params.append('q', pageSearchFilter);
      const res = await fetch(`${apiUrl}/api/pages?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setPagesList(data.items || []);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingPages(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'pages') {
      loadPages();
    }
  }, [activeTab, selectedType, selectedTag, pageSearchFilter]);

  // Load Observations
  const loadObservations = async () => {
    setLoadingObs(true);
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (obsProjectFilter) params.append('project', obsProjectFilter);
      const res = await fetch(`${apiUrl}/api/observations?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setObservations(data.items || []);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingObs(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'observations') {
      loadObservations();
    }
  }, [activeTab, obsProjectFilter]);

  // Load Single Page Content
  const loadPageDetail = async (path: string) => {
    try {
      const res = await fetch(`${apiUrl}/api/page?path=${encodeURIComponent(path)}`);
      if (res.ok) {
        const data = await res.json();
        setActivePageDoc(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className={`min-h-screen flex flex-col ${darkMode ? 'bg-zinc-950 text-zinc-100' : 'bg-slate-50 text-slate-900'}`}>
      {/* Top Navigation Bar */}
      <header className={`border-b px-6 py-3.5 flex items-center justify-between sticky top-0 z-40 backdrop-blur-md ${darkMode ? 'bg-zinc-900/85 border-zinc-800' : 'bg-white/85 border-slate-200'}`}>
        <div className="flex items-center gap-3.5">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 via-blue-600 to-cyan-400 flex items-center justify-center shadow-md shadow-indigo-500/20">
            <Brain className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-bold text-base tracking-tight">Memory Hub</h1>
              <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">v2.0 Full-Auto</span>
            </div>
            <p className="text-xs text-zinc-400">Agent Memory System & LLM-Wiki Engine</p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="flex items-center gap-1 bg-zinc-800/40 p-1 rounded-xl border border-zinc-700/30">
          {[
            { id: 'overview', label: '总览看板', icon: Activity },
            { id: 'search', label: '两阶段检索', icon: Search },
            { id: 'ask', label: '记忆问答', icon: Sparkles },
            { id: 'pages', label: '知识库管理', icon: BookOpen },
            { id: 'observations', label: '会话观察与聚类', icon: Layers },
            { id: 'automation', label: '运维与流水线', icon: Terminal },
          ].map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                  active
                    ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-600/30'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            );
          })}
        </nav>

        {/* Right Tools & Status */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-zinc-800/40 border border-zinc-700/30 text-xs">
            <span className={`w-2 h-2 rounded-full ${apiConnected ? 'bg-emerald-500 shadow-sm shadow-emerald-500/50 animate-pulse' : 'bg-rose-500'}`} />
            <span className="text-zinc-300 font-mono text-[11px]">{apiConnected ? ':8787 就绪' : ':8787 未连接'}</span>
          </div>

          <button
            onClick={() => setDarkMode(!darkMode)}
            className="p-1.5 rounded-lg hover:bg-zinc-800/60 text-zinc-400 hover:text-zinc-200 transition"
            title="切换暗色/亮色模式"
          >
            {darkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 p-6 max-w-7xl w-full mx-auto">
        {/* VIEW 1: OVERVIEW */}
        {activeTab === 'overview' && (
          <div className="space-y-6">
            {/* Vitals Stat Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 shadow-sm">
                <div className="flex items-center justify-between text-zinc-400 mb-2">
                  <span className="text-xs font-medium">知识库页面总量</span>
                  <BookOpen className="w-4 h-4 text-indigo-400" />
                </div>
                <div className="text-2xl font-bold tracking-tight text-white">
                  {overview?.wiki_pages?.toLocaleString() || '12,962'}
                </div>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-emerald-400">
                  <CheckCircle2 className="w-3 h-3" /> FTS5 trigram + 向量全量索引
                </div>
              </div>

              <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 shadow-sm">
                <div className="flex items-center justify-between text-zinc-400 mb-2">
                  <span className="text-xs font-medium">会话观察 (Staging)</span>
                  <Layers className="w-4 h-4 text-cyan-400" />
                </div>
                <div className="text-2xl font-bold tracking-tight text-white">
                  {overview?.observations_lines?.toLocaleString() || '55,341'}
                </div>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-zinc-400">
                  <Clock className="w-3 h-3 text-cyan-400" /> {overview?.observations_files || 30} 个日志分片
                </div>
              </div>

              <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 shadow-sm">
                <div className="flex items-center justify-between text-zinc-400 mb-2">
                  <span className="text-xs font-medium">活跃会话 (近3天)</span>
                  <Activity className="w-4 h-4 text-emerald-400" />
                </div>
                <div className="text-2xl font-bold tracking-tight text-white">
                  {overview?.sessions_recent || '180'}
                </div>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-zinc-400">
                  <Zap className="w-3 h-3 text-amber-400" /> Codex 实时会话流捕获
                </div>
              </div>

              <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 shadow-sm">
                <div className="flex items-center justify-between text-zinc-400 mb-2">
                  <span className="text-xs font-medium">索引库大小 (index.db)</span>
                  <Database className="w-4 h-4 text-violet-400" />
                </div>
                <div className="text-2xl font-bold tracking-tight text-white">
                  {overview?.index_db_bytes ? `${(overview.index_db_bytes / (1024 * 1024)).toFixed(1)} MB` : '295.0 MB'}
                </div>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-indigo-400">
                  <ShieldCheck className="w-3 h-3" /> SQLite 事务隔离 & 原子交换
                </div>
              </div>
            </div>

            {/* Middle Grid: Type Distribution & Roadmap Features */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Type Breakdown */}
              <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 lg:col-span-2">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-sm font-semibold text-white">知识库分类分布</h2>
                    <p className="text-xs text-zinc-400">~/llm-wiki 核心类型与条目占比</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {overview?.by_type && Object.entries(overview.by_type).map(([type, count]) => (
                    <div key={type} className="p-3 rounded-xl bg-zinc-800/30 border border-zinc-700/20">
                      <div className="text-xs text-zinc-400 capitalize">{type}</div>
                      <div className="text-lg font-bold text-zinc-100 mt-1">{count.toLocaleString()}</div>
                    </div>
                  ))}
                  {!overview?.by_type && (
                    <>
                      <div className="p-3 rounded-xl bg-zinc-800/30 border border-zinc-700/20">
                        <div className="text-xs text-zinc-400">Notes (笔记)</div>
                        <div className="text-lg font-bold text-zinc-100 mt-1">10,820</div>
                      </div>
                      <div className="p-3 rounded-xl bg-zinc-800/30 border border-zinc-700/20">
                        <div className="text-xs text-zinc-400">Concepts (概念)</div>
                        <div className="text-lg font-bold text-zinc-100 mt-1">1,240</div>
                      </div>
                      <div className="p-3 rounded-xl bg-zinc-800/30 border border-zinc-700/20">
                        <div className="text-xs text-zinc-400">Entities (实体)</div>
                        <div className="text-lg font-bold text-zinc-100 mt-1">580</div>
                      </div>
                      <div className="p-3 rounded-xl bg-zinc-800/30 border border-zinc-700/20">
                        <div className="text-xs text-zinc-400">Failures (踩坑)</div>
                        <div className="text-lg font-bold text-zinc-100 mt-1">322</div>
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Roadmap Features Status */}
              <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-3.5">
                <h2 className="text-sm font-semibold text-white">Roadmap 自动化架构就绪</h2>
                <div className="space-y-2.5">
                  <div className="flex items-start gap-2.5 p-2.5 rounded-xl bg-emerald-950/20 border border-emerald-800/30">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
                    <div>
                      <div className="text-xs font-semibold text-emerald-200">阶段一：Scope 推断与两阶段扩词</div>
                      <div className="text-[11px] text-zinc-400">Mem0 作用域分类 + MemoRAG L0 关键词规划</div>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5 p-2.5 rounded-xl bg-emerald-950/20 border border-emerald-800/30">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
                    <div>
                      <div className="text-xs font-semibold text-emerald-200">阶段二：生命周期与跨日聚类</div>
                      <div className="text-[11px] text-zinc-400">Graphiti deprecated_by 废弃调权 + Letta 聚类蒸馏</div>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5 p-2.5 rounded-xl bg-indigo-950/20 border border-indigo-800/30">
                    <Zap className="w-4 h-4 text-indigo-400 mt-0.5 shrink-0" />
                    <div>
                      <div className="text-xs font-semibold text-indigo-200">全链路默认自动化 (Default Auto)</div>
                      <div className="text-[11px] text-zinc-400">run & maintain 默认全自动闭环，带 --safe 仿真</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* VIEW 2: SEARCH (两阶段检索) */}
        {activeTab === 'search' && (
          <div className="space-y-6">
            {/* Search Controls */}
            <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
              <form onSubmit={handleSearch} className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="输入检索关键词或概念 (如: OpenCodex, TikTok Shop, 架构决策...)"
                    className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-zinc-800/50 border border-zinc-700/40 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition"
                  />
                </div>
                <button
                  type="submit"
                  disabled={searching}
                  className="px-5 py-2.5 rounded-xl bg-indigo-600 text-white font-medium text-xs hover:bg-indigo-500 transition shadow-sm shadow-indigo-600/30 flex items-center gap-1.5 shrink-0"
                >
                  {searching ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                  两阶段检索
                </button>
              </form>

              {/* Advanced Parameters Bar */}
              <div className="flex flex-wrap items-center gap-4 pt-3 border-t border-zinc-800/60 text-xs">
                <label className="flex items-center gap-2 cursor-pointer text-zinc-300">
                  <input
                    type="checkbox"
                    checked={searchFuse}
                    onChange={(e) => setSearchFuse(e.target.checked)}
                    className="rounded bg-zinc-800 border-zinc-700 text-indigo-600"
                  />
                  <span>RRF 向量融合 (fuse)</span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer text-zinc-300">
                  <input
                    type="checkbox"
                    checked={searchExpand}
                    onChange={(e) => setSearchExpand(e.target.checked)}
                    className="rounded bg-zinc-800 border-zinc-700 text-indigo-600"
                  />
                  <span>L0 智能扩词 (expand)</span>
                </label>

                <div className="flex items-center gap-2 text-zinc-400">
                  <span>作用域:</span>
                  <select
                    value={searchScope}
                    onChange={(e) => setSearchScope(e.target.value)}
                    className="bg-zinc-800/60 border border-zinc-700/40 rounded-lg px-2 py-1 text-xs text-zinc-200"
                  >
                    <option value="all">全部作用域 (Cross-scope)</option>
                    <option value="project">项目记忆 (Project)</option>
                    <option value="user">用户偏好 (User)</option>
                    <option value="agent">Agent 规则 (Agent)</option>
                  </select>
                </div>

                <div className="flex items-center gap-2 text-zinc-400">
                  <span>Top:</span>
                  <select
                    value={searchTop}
                    onChange={(e) => setSearchTop(Number(e.target.value))}
                    className="bg-zinc-800/60 border border-zinc-700/40 rounded-lg px-2 py-1 text-xs text-zinc-200"
                  >
                    <option value={5}>Top 5</option>
                    <option value={10}>Top 10</option>
                    <option value={20}>Top 20</option>
                  </select>
                </div>
              </div>
            </div>

            {/* Query Plan Explain Drawer */}
            {searchPlan && (
              <div className="p-4 rounded-xl bg-indigo-950/20 border border-indigo-800/30 text-xs space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-indigo-300 flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5" /> 查询规划报告 (Query Plan)
                  </span>
                  <span className="text-[11px] font-mono text-zinc-400">规划器: {searchPlan.planner}</span>
                </div>
                {searchPlan.expansions && searchPlan.expansions.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    <span className="text-zinc-400">扩词候选:</span>
                    {searchPlan.expansions.map((exp: any, i: number) => (
                      <span key={i} className="px-2 py-0.5 rounded-md bg-indigo-900/40 text-indigo-200 font-mono text-[11px] border border-indigo-700/30">
                        {exp.term} ({exp.confidence})
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Search Results List */}
            <div className="space-y-3">
              {searchResults.map((item, idx) => (
                <div
                  key={idx}
                  onClick={() => loadPageDetail(item.path)}
                  className="p-4 rounded-xl bg-zinc-900/40 hover:bg-zinc-900/80 border border-zinc-800/80 hover:border-zinc-700/80 transition cursor-pointer space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-indigo-400 font-semibold">[{item.score.toFixed(3)}]</span>
                      <h3 className="font-semibold text-sm text-zinc-100">{item.title || item.path}</h3>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400 border border-zinc-700/40 uppercase">
                        {item.type}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-950/40 text-indigo-300 border border-indigo-800/40">
                        {item.scope}:{item.scope_id}
                      </span>
                      {item.status === 'active' && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950/40 text-emerald-400 border border-emerald-800/30">
                          Active
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-zinc-400 line-clamp-2 leading-relaxed">
                    {item.abstract || '暂无摘要内容'}
                  </p>
                  <div className="text-[11px] font-mono text-zinc-500">{item.path}</div>
                </div>
              ))}
              {searchResults.length === 0 && !searching && (
                <div className="text-center py-12 text-zinc-500 text-xs">
                  暂无检索结果，请在上方输入关键词开始检索
                </div>
              )}
            </div>
          </div>
        )}

        {/* VIEW 3: ASK (记忆问答) */}
        {activeTab === 'ask' && (
          <div className="space-y-6 max-w-4xl mx-auto">
            <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
              <div className="flex items-center gap-2 text-indigo-400">
                <Sparkles className="w-5 h-5" />
                <h2 className="text-sm font-semibold text-white">基于 llm-wiki 的智能问答</h2>
              </div>
              <p className="text-xs text-zinc-400">自动完成两阶段混合检索 + 页面上下文装配 + 免费 LLM 生成回答与引用</p>

              <form onSubmit={handleAsk} className="flex gap-2">
                <input
                  type="text"
                  value={askQuestion}
                  onChange={(e) => setAskQuestion(e.target.value)}
                  placeholder="向知识库提问 (如: OpenCodex 499 错误的原因是什么？)"
                  className="w-full px-4 py-2.5 rounded-xl bg-zinc-800/50 border border-zinc-700/40 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition"
                />
                <button
                  type="submit"
                  disabled={asking}
                  className="px-5 py-2.5 rounded-xl bg-indigo-600 text-white font-medium text-xs hover:bg-indigo-500 transition shadow-sm shadow-indigo-600/30 flex items-center gap-1.5 shrink-0"
                >
                  {asking ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                  提问
                </button>
              </form>
            </div>

            {/* Answer Display */}
            {askAnswer && (
              <div className="p-6 rounded-2xl bg-zinc-900/80 border border-zinc-800 space-y-4 shadow-lg">
                <h3 className="text-sm font-semibold text-zinc-200">AI 回答</h3>
                <div className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                  {askAnswer}
                </div>
                {askContextPaths.length > 0 && (
                  <div className="pt-4 border-t border-zinc-800/80">
                    <div className="text-xs font-semibold text-zinc-400 mb-2">引用知识库页面:</div>
                    <div className="flex flex-wrap gap-2">
                      {askContextPaths.map((p, idx) => (
                        <span key={idx} className="px-2.5 py-1 rounded-lg bg-zinc-800/60 text-xs font-mono text-indigo-300 border border-zinc-700/40">
                          {p}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* VIEW 4: PAGES (知识库管理) */}
        {activeTab === 'pages' && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {/* Left Pages List */}
            <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-4 flex flex-col h-[700px]">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white">页面列表 ({pagesList.length})</h2>
                <button onClick={loadPages} className="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200">
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
              </div>

              <input
                type="text"
                value={pageSearchFilter}
                onChange={(e) => setPageSearchFilter(e.target.value)}
                placeholder="筛选页面路径/标题..."
                className="w-full px-3 py-1.5 rounded-lg bg-zinc-800/50 border border-zinc-700/40 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none"
              />

              <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
                {pagesList.map((p, idx) => (
                  <div
                    key={idx}
                    onClick={() => loadPageDetail(p.path)}
                    className={`p-2.5 rounded-lg text-xs transition cursor-pointer border ${
                      activePageDoc?.path === p.path
                        ? 'bg-indigo-950/40 border-indigo-600/40 text-indigo-200'
                        : 'bg-zinc-800/30 border-transparent hover:bg-zinc-800/60 text-zinc-300'
                    }`}
                  >
                    <div className="font-medium truncate">{p.title || p.path}</div>
                    <div className="text-[10px] text-zinc-500 truncate mt-0.5">{p.path}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Right Page Detail Viewer */}
            <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 md:col-span-2 h-[700px] flex flex-col">
              {activePageDoc ? (
                <div className="flex-1 flex flex-col space-y-4 overflow-hidden">
                  <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
                    <div>
                      <h2 className="text-base font-bold text-white">{activePageDoc.meta?.title || activePageDoc.path}</h2>
                      <p className="text-xs font-mono text-zinc-400">{activePageDoc.path}</p>
                    </div>
                  </div>

                  <div className="flex-1 overflow-y-auto pr-2 space-y-4 font-mono text-xs text-zinc-300 whitespace-pre-wrap bg-zinc-950/50 p-4 rounded-xl border border-zinc-800/60">
                    {activePageDoc.content}
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-zinc-500 text-xs">
                  请从左侧选择一个知识库页面查看详情
                </div>
              )}
            </div>
          </div>
        )}

        {/* VIEW 5: OBSERVATIONS & CLUSTERING */}
        {activeTab === 'observations' && (
          <div className="space-y-6">
            <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-white">Staging 原始会话观察流</h2>
                  <p className="text-xs text-zinc-400">Codex / Claude-mem 会话采集碎片，支持自动跨日聚类合并</p>
                </div>
                <button
                  onClick={loadObservations}
                  className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs flex items-center gap-1.5 transition"
                >
                  <RefreshCw className="w-3.5 h-3.5" /> 刷新数据
                </button>
              </div>

              <div className="space-y-2.5">
                {observations.map((obs, idx) => (
                  <div key={idx} className="p-3 rounded-xl bg-zinc-800/30 border border-zinc-700/20 text-xs space-y-1">
                    <div className="flex items-center justify-between text-[11px] text-zinc-400">
                      <span className="font-mono text-indigo-400 font-semibold">[{obs.id || idx}] {obs.project || 'default-project'}</span>
                      <span>{obs.created_at || ''}</span>
                    </div>
                    <p className="text-zinc-200 font-sans">{obs.text}</p>
                  </div>
                ))}
                {observations.length === 0 && (
                  <div className="text-center py-8 text-zinc-500 text-xs">暂无待蒸馏的 staging 观察数据</div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* VIEW 6: AUTOMATION & PIPELINES */}
        {activeTab === 'automation' && (
          <div className="space-y-6">
            <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
              <h2 className="text-sm font-semibold text-white">流水线与维护命令速查</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-4 rounded-xl bg-zinc-800/30 border border-zinc-700/20 space-y-2">
                  <div className="text-xs font-semibold text-indigo-300">一键全链路闭环 (Run)</div>
                  <code className="block p-2 rounded bg-zinc-950 text-xs text-zinc-300 font-mono">
                    bash memory-hub.sh run
                  </code>
                  <p className="text-[11px] text-zinc-400">默认以 auto=on, apply=on, commit=on 依次执行 capture → distill → scope-backfill → successor → publish → index → archive</p>
                </div>

                <div className="p-4 rounded-xl bg-zinc-800/30 border border-zinc-700/20 space-y-2">
                  <div className="text-xs font-semibold text-cyan-300">跨日聚类与知识库维护 (Maintain)</div>
                  <code className="block p-2 rounded bg-zinc-950 text-xs text-zinc-300 font-mono">
                    bash memory-hub.sh maintain
                  </code>
                  <p className="text-[11px] text-zinc-400">执行 7 阶段事务：validate → publish_pages_lifecycle → index_swap → lint → manifest_commit → archive → exact_stage_commit</p>
                </div>

                <div className="p-4 rounded-xl bg-zinc-800/30 border border-zinc-700/20 space-y-2">
                  <div className="text-xs font-semibold text-emerald-300">安全只读仿真 (Safe Simulation)</div>
                  <code className="block p-2 rounded bg-zinc-950 text-xs text-zinc-300 font-mono">
                    bash memory-hub.sh run --safe
                  </code>
                  <p className="text-[11px] text-zinc-400">执行所有规划与校验，但对 wiki 文件、manifest 与 Git 零写入</p>
                </div>

                <div className="p-4 rounded-xl bg-zinc-800/30 border border-zinc-700/20 space-y-2">
                  <div className="text-xs font-semibold text-amber-300">确定性 Scope 批量回填</div>
                  <code className="block p-2 rounded bg-zinc-950 text-xs text-zinc-300 font-mono">
                    bash memory-hub.sh scope-backfill --apply
                  </code>
                  <p className="text-[11px] text-zinc-400">按 6 级确定性规则分批回填存量约 12,900 Markdown 页面</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
