/**
 * Diagnostics Page - 召回追踪 / 频道记忆 / 外部 API / LLM 用量。
 *
 * 页面为懒加载 chunk：首次进入才 import 并注入 diagnostics.css；所有文案
 * 在本模块内注册（window.lmRegisterMessages），不增加首屏 base bundle。
 */

import { esc, showToast, confirmAction } from "./utils.js";

/* ================================================================
   Lazy i18n messages (zh/en/ru/ja)
   ================================================================ */
const DIAG_MESSAGES = {
  "diag.tabTraces": { zh: "召回追踪", en: "Traces", ru: "Трассировки", ja: "検索トレース" },
  "diag.tabChannels": { zh: "频道记忆", en: "Channels", ru: "Каналы", ja: "チャンネル記憶" },
  "diag.tabExternal": { zh: "外部 API", en: "External API", ru: "Внешний API", ja: "外部 API" },
  "diag.tabUsage": { zh: "LLM 用量", en: "LLM usage", ru: "Использование LLM", ja: "LLM 使用量" },
  "diag.loading": { zh: "正在加载…", en: "Loading…", ru: "Загрузка…", ja: "読み込み中…" },
  "diag.refresh": { zh: "刷新", en: "Refresh", ru: "Обновить", ja: "更新" },
  "diag.retry": { zh: "重试", en: "Retry", ru: "Повторить", ja: "再試行" },
  "diag.loadFailed": { zh: "加载失败", en: "Failed to load", ru: "Не удалось загрузить", ja: "読み込みに失敗しました" },
  "diag.empty": { zh: "暂无数据", en: "No data", ru: "Нет данных", ja: "データがありません" },
  "diag.close": { zh: "关闭", en: "Close", ru: "Закрыть", ja: "閉じる" },
  "diag.time": { zh: "时间", en: "Time", ru: "Время", ja: "時刻" },
  "diag.scope": { zh: "会话/作用域", en: "Scope", ru: "Область", ja: "スコープ" },
  "diag.cacheHit": { zh: "缓存", en: "Cache", ru: "Кэш", ja: "キャッシュ" },
  "diag.cacheYes": { zh: "命中", en: "hit", ru: "попадание", ja: "ヒット" },
  "diag.cacheNo": { zh: "未命中", en: "miss", ru: "промах", ja: "ミス" },
  "diag.routes": { zh: "路由", en: "Routes", ru: "Маршруты", ja: "ルート" },
  "diag.injected": { zh: "注入", en: "Injected", ru: "Внедрено", ja: "注入" },
  "diag.duration": { zh: "耗时", en: "Duration", ru: "Длительность", ja: "所要時間" },
  "diag.limit": { zh: "条数", en: "Limit", ru: "Лимит", ja: "件数" },
  "diag.sessionOptional": { zh: "会话过滤（可选）", en: "Session filter (optional)", ru: "Фильтр сессии (опц.)", ja: "セッション絞り込み（任意）" },
  "diag.recorderOn": { zh: "追踪已开启", en: "Trace recorder on", ru: "Запись трассировок включена", ja: "トレース記録オン" },
  "diag.recorderOff": { zh: "追踪未开启", en: "Trace recorder off", ru: "Запись трассировок выключена", ja: "トレース記録オフ" },
  "diag.records": { zh: "记录 {0} 条", en: "{0} records", ru: "Записей: {0}", ja: "記録 {0} 件" },
  "diag.persistOn": { zh: "落盘开启", en: "Persist on", ru: "Сохранение включено", ja: "永続化オン" },
  "diag.persistOff": { zh: "仅内存", en: "Memory only", ru: "Только память", ja: "メモリのみ" },
  "diag.tracesEmpty": { zh: "暂无追踪记录；产生召回后再次刷新。", en: "No traces yet. Refresh after a recall runs.", ru: "Трассировок пока нет. Обновите после поиска.", ja: "トレースはまだありません。検索後に更新してください。" },
  "diag.tracesDisabled": { zh: "召回追踪未开启，请在配置中启用 traces 后刷新。", en: "Trace recording is disabled. Enable traces in the configuration and refresh.", ru: "Запись трассировок отключена. Включите traces в конфигурации и обновите.", ja: "トレース記録が無効です。設定で traces を有効にして更新してください。" },
  "diag.detail": { zh: "追踪详情", en: "Trace detail", ru: "Детали трассировки", ja: "トレース詳細" },
  "diag.route": { zh: "路由", en: "Route", ru: "Маршрут", ja: "ルート" },
  "diag.candidates": { zh: "候选", en: "Candidates", ru: "Кандидаты", ja: "候補" },
  "diag.ok": { zh: "成功", en: "OK", ru: "Успех", ja: "成功" },
  "diag.error": { zh: "错误", en: "Errors", ru: "Ошибки", ja: "エラー" },
  "diag.filters": { zh: "过滤条件", en: "Filters", ru: "Фильтры", ja: "フィルター" },
  "diag.minImportance": { zh: "最低重要性 {0}", en: "min importance {0}", ru: "мин. важность {0}", ja: "最低重要度 {0}" },
  "diag.minSimilarity": { zh: "最低相似度 {0}", en: "min similarity {0}", ru: "мин. сходство {0}", ja: "最低類似度 {0}" },
  "diag.rejected": { zh: "被拒绝", en: "Rejected", ru: "Отклонено", ja: "却下" },
  "diag.topCandidates": { zh: "候选 Top {0}", en: "Top {0} candidates", ru: "Топ-{0} кандидатов", ja: "候補トップ {0}" },
  "diag.score": { zh: "分数", en: "Score", ru: "Балл", ja: "スコア" },
  "diag.injectedList": { zh: "注入记忆", en: "Injected memories", ru: "Внедрённые памяти", ja: "注入された記憶" },
  "diag.agentic": { zh: "Agent 自主回忆", en: "Agentic recall", ru: "Агентный поиск", ja: "エージェント検索" },
  "diag.budget": { zh: "预算", en: "Budget", ru: "Бюджет", ja: "バジェット" },
  "diag.notes": { zh: "备注", en: "Notes", ru: "Заметки", ja: "ノート" },
  "diag.lineage": { zh: "血缘", en: "Lineage", ru: "Происхождение", ja: "系譜" },
  "diag.lineageTitle": { zh: "记忆血缘 #{0}", en: "Memory lineage #{0}", ru: "Происхождение памяти #{0}", ja: "記憶の系譜 #{0}" },
  "diag.memoryId": { zh: "记忆 ID", en: "Memory ID", ru: "ID памяти", ja: "記憶 ID" },
  "diag.memoryIdPh": { zh: "输入记忆 ID 查血缘", en: "Memory ID for lineage", ru: "ID памяти для происхождения", ja: "系譜を見る記憶 ID" },
  "diag.lineageSearch": { zh: "查血缘", en: "Load lineage", ru: "Загрузить", ja: "系譜を読む" },
  "diag.lineageEmpty": { zh: "该记忆没有可用的血缘信息。", en: "No lineage information for this memory.", ru: "Нет данных о происхождении памяти.", ja: "この記憶の系譜情報はありません。" },
  "diag.memoryInfo": { zh: "记忆信息", en: "Memory", ru: "Память", ja: "記憶情報" },
  "diag.summary": { zh: "摘要", en: "Summary", ru: "Сводка", ja: "要約" },
  "diag.importance": { zh: "重要性", en: "Importance", ru: "Важность", ja: "重要度" },
  "diag.status": { zh: "状态", en: "Status", ru: "Статус", ja: "ステータス" },
  "diag.createdAt": { zh: "创建时间", en: "Created", ru: "Создано", ja: "作成日時" },
  "diag.sources": { zh: "来源消息", en: "Source messages", ru: "Исходные сообщения", ja: "元メッセージ" },
  "diag.window": { zh: "窗口", en: "Window", ru: "Окно", ja: "期間" },
  "diag.supersedes": { zh: "被取代", en: "Supersedes", ru: "Заменяет", ja: "置き換え" },
  "diag.links": { zh: "关联 ID", en: "Linked IDs", ru: "Связанные ID", ja: "関連 ID" },
  "diag.graphSummary": { zh: "图谱摘要", en: "Graph summary", ru: "Сводка графа", ja: "グラフ概要" },
  "diag.graphCounts": { zh: "节点 {0} · 关系 {1}", en: "{0} nodes · {1} edges", ru: "узлов: {0} · связей: {1}", ja: "ノード {0} · 関係 {1}" },
  "diag.traceHits": { zh: "命中该记忆的追踪", en: "Traces hitting this memory", ru: "Трассировки с этой памятью", ja: "この記憶に一致したトレース" },
  "diag.channelsEmpty": { zh: "通道记忆未开启或无数据。", en: "Channel memories are disabled or empty.", ru: "Память каналов отключена или пуста.", ja: "チャンネル記憶が無効かデータがありません。" },
  "diag.platform": { zh: "平台", en: "Platform", ru: "Платформа", ja: "プラットフォーム" },
  "diag.channelId": { zh: "频道 ID", en: "Channel ID", ru: "ID канала", ja: "チャンネル ID" },
  "diag.memoryCount": { zh: "记忆数", en: "Memories", ru: "Памяти", ja: "記憶数" },
  "diag.latest": { zh: "最近写入", en: "Latest write", ru: "Последняя запись", ja: "最終書き込み" },
  "diag.channelMemories": { zh: "频道记忆：{0}", en: "Channel memories: {0}", ru: "Памяти канала: {0}", ja: "チャンネル記憶: {0}" },
  "diag.back": { zh: "返回频道列表", en: "Back to channels", ru: "Назад к каналам", ja: "チャンネル一覧へ" },
  "diag.preview": { zh: "内容预览", en: "Preview", ru: "Предпросмотр", ja: "内容プレビュー" },
  "diag.loadMore": { zh: "加载更多", en: "Load more", ru: "Загрузить ещё", ja: "さらに読み込む" },
  "diag.channelMemoriesEmpty": { zh: "该频道暂无记忆。", en: "No memories in this channel.", ru: "В этом канале нет памятей.", ja: "このチャンネルに記憶はありません。" },
  "diag.keys": { zh: "外部 API Key", en: "External API keys", ru: "Ключи внешнего API", ja: "外部 API キー" },
  "diag.name": { zh: "名称", en: "Name", ru: "Имя", ja: "名前" },
  "diag.prefix": { zh: "前缀", en: "Prefix", ru: "Префикс", ja: "プレフィックス" },
  "diag.permissions": { zh: "权限", en: "Permissions", ru: "Права", ja: "権限" },
  "diag.personaWhitelist": { zh: "人格白名单", en: "Persona allowlist", ru: "Список персон", ja: "ペルソナ許可リスト" },
  "diag.scopeWhitelist": { zh: "作用域白名单", en: "Scope allowlist", ru: "Список областей", ja: "スコープ許可リスト" },
  "diag.rateLimit": { zh: "请求/分钟", en: "Requests/min", ru: "Запросов/мин", ja: "リクエスト/分" },
  "diag.writeLimit": { zh: "写入/分钟", en: "Writes/min", ru: "Записей/мин", ja: "書き込み/分" },
  "diag.lastUsed": { zh: "最近使用", en: "Last used", ru: "Последнее использование", ja: "最終使用" },
  "diag.active": { zh: "有效", en: "Active", ru: "Активен", ja: "有効" },
  "diag.revoked": { zh: "已吊销", en: "Revoked", ru: "Отозван", ja: "失効" },
  "diag.revoke": { zh: "吊销", en: "Revoke", ru: "Отозвать", ja: "失効させる" },
  "diag.revokeTitle": { zh: "确认吊销该 Key？", en: "Revoke this key?", ru: "Отозвать этот ключ?", ja: "このキーを失効させますか？" },
  "diag.revokeMessage": { zh: "Key「{0}」将立即失效，无法恢复。", en: "Key “{0}” stops working immediately and cannot be restored.", ru: "Ключ «{0}» немедленно перестанет работать.", ja: "キー「{0}」は即座に無効になり、元に戻せません。" },
  "diag.revokedDone": { zh: "Key 已吊销", en: "Key revoked", ru: "Ключ отозван", ja: "キーを失効させました" },
  "diag.revokeFailed": { zh: "吊销 Key 失败", en: "Failed to revoke key", ru: "Не удалось отозвать ключ", ja: "キーの失効に失敗しました" },
  "diag.createTitle": { zh: "创建外部 API Key", en: "Create external API key", ru: "Создать ключ внешнего API", ja: "外部 API キーを作成" },
  "diag.create": { zh: "创建 Key", en: "Create key", ru: "Создать ключ", ja: "キーを作成" },
  "diag.creating": { zh: "创建中…", en: "Creating…", ru: "Создание…", ja: "作成中…" },
  "diag.mode": { zh: "模式", en: "Mode", ru: "Режим", ja: "モード" },
  "diag.readOnly": { zh: "只读", en: "Read only", ru: "Только чтение", ja: "読み取り専用" },
  "diag.readWrite": { zh: "读写", en: "Read/write", ru: "Чтение/запись", ja: "読み書き" },
  "diag.personasPh": { zh: "persona1, persona2（逗号分隔）", en: "persona1, persona2 (comma separated)", ru: "persona1, persona2 (через запятую)", ja: "persona1, persona2（カンマ区切り）" },
  "diag.scopesPh": { zh: "scope1, scope2（逗号分隔）", en: "scope1, scope2 (comma separated)", ru: "scope1, scope2 (через запятую)", ja: "scope1, scope2（カンマ区切り）" },
  "diag.createFailed": { zh: "创建 Key 失败", en: "Failed to create key", ru: "Не удалось создать ключ", ja: "キーの作成に失敗しました" },
  "diag.tokenTitle": { zh: "一次性 Token", en: "One-time token", ru: "Одноразовый токен", ja: "ワンタイムトークン" },
  "diag.tokenWarning": { zh: "该 Token 只显示一次，关闭后无法再次查看，请立即复制保存。", en: "This token is shown once. Copy it now; it cannot be displayed again.", ru: "Токен показывается один раз. Скопируйте его сейчас.", ja: "このトークンは一度だけ表示されます。今すぐコピーしてください。" },
  "diag.tokenCopy": { zh: "复制", en: "Copy", ru: "Копировать", ja: "コピー" },
  "diag.tokenCopied": { zh: "已复制", en: "Copied", ru: "Скопировано", ja: "コピーしました" },
  "diag.tokenClose": { zh: "我已保存，关闭", en: "Saved, close", ru: "Сохранил, закрыть", ja: "保存しました、閉じる" },
  "diag.noKeys": { zh: "暂无外部 API Key。", en: "No external API keys yet.", ru: "Ключей внешнего API пока нет.", ja: "外部 API キーはまだありません。" },
  "diag.externalDisabled": { zh: "外部 API 未启用", en: "External API is disabled", ru: "Внешний API отключён", ja: "外部 API は無効です" },
  "diag.externalDisabledHint": { zh: "在 AstrBot 插件配置页开启 external_api.enabled 并重载插件后，可在此管理 API Key 与审计。", en: "Enable external_api.enabled on the AstrBot plugin config page and reload the plugin to manage keys and audit here.", ru: "Включите external_api.enabled на странице конфигурации плагина AstrBot и перезагрузите плагин, чтобы управлять ключами и аудитом.", ja: "AstrBot のプラグイン設定で external_api.enabled を有効にしてプラグインを再読み込みすると、ここでキーと監査を管理できます。" },
  "diag.audit": { zh: "最近审计（50 条）", en: "Recent audit (50)", ru: "Аудит (50)", ja: "最近の監査（50 件）" },
  "diag.action": { zh: "动作", en: "Action", ru: "Действие", ja: "操作" },
  "diag.statusCode": { zh: "状态码", en: "Status", ru: "Статус", ja: "ステータス" },
  "diag.latency": { zh: "耗时 (ms)", en: "Latency (ms)", ru: "Задержка (мс)", ja: "レイテンシ (ms)" },
  "diag.keyName": { zh: "Key", en: "Key", ru: "Ключ", ja: "キー" },
  "diag.auditEmpty": { zh: "暂无审计记录。", en: "No audit records yet.", ru: "Записей аудита пока нет.", ja: "監査記録はまだありません。" },
  "diag.usageCalls": { zh: "调用次数", en: "Calls", ru: "Вызовы", ja: "呼び出し回数" },
  "diag.usageInput": { zh: "输入 token", en: "Input tokens", ru: "Входные токены", ja: "入力トークン" },
  "diag.usageOutput": { zh: "输出 token", en: "Output tokens", ru: "Выходные токены", ja: "出力トークン" },
  "diag.usageTotal": { zh: "总 token", en: "Total tokens", ru: "Всего токенов", ja: "合計トークン" },
  "diag.usageEstimated": { zh: "估算调用", en: "Estimated calls", ru: "Оценочные вызовы", ja: "推定呼び出し" },
  "diag.usageFallback": { zh: "回退次数", en: "Fallbacks", ru: "Резервные вызовы", ja: "フォールバック" },
  "diag.provider": { zh: "Provider", en: "Provider", ru: "Провайдер", ja: "プロバイダー" },
  "diag.usageProviders": { zh: "Provider 分布", en: "Provider distribution", ru: "Распределение провайдеров", ja: "プロバイダー分布" },
  "diag.usageFallbackChain": { zh: "回退链", en: "Fallback chain", ru: "Цепочка резерва", ja: "フォールバック連鎖" },
  "diag.usageNoFallback": { zh: "未配置回退", en: "No fallback configured", ru: "Резерв не настроен", ja: "フォールバック未設定" },
  "diag.usageTimeout": { zh: "单 Provider 超时", en: "Per-provider timeout", ru: "Тайм-аут провайдера", ja: "プロバイダー別タイムアウト" },
  "diag.seconds": { zh: "{0} 秒", en: "{0}s", ru: "{0} с", ja: "{0} 秒" },
  "diag.usageEmpty": { zh: "暂无用量数据。", en: "No usage data yet.", ru: "Данных об использовании пока нет.", ja: "使用量データはまだありません。" },
  "diag.unknown": { zh: "--", en: "--", ru: "--", ja: "--" },
};

function registerDiagnosticsMessages() {
  if (typeof window !== "undefined" && typeof window.lmRegisterMessages === "function") {
    window.lmRegisterMessages(DIAG_MESSAGES);
  }
}

function ensureDiagnosticsStyles() {
  if (typeof document === "undefined" || !document.head || typeof document.createElement !== "function") {
    return;
  }
  if (document.getElementById("diagnostics-page-styles")) return;
  const link = document.createElement("link");
  link.id = "diagnostics-page-styles";
  link.rel = "stylesheet";
  link.href = new URL("./assets/diagnostics.min.css", document.baseURI).href;
  document.head.appendChild(link);
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return String(Math.round(number)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatTime(value) {
  if (value === null || value === undefined || value === "") return "--";
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) {
    const millis = numeric < 1e12 ? numeric * 1000 : numeric;
    try {
      return new Date(millis).toLocaleString();
    } catch (_error) {
      return String(value);
    }
  }
  return String(value);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function formatOk(value) {
  if (typeof value === "boolean") return '<i data-lucide="' + (value ? "check" : "x") + '"></i>';
  return esc(String(value));
}

function routeRows(routes) {
  if (!routes) return [];
  if (Array.isArray(routes)) {
    return routes.map((route, index) => ({
      name: route.name || route.route || `route_${index + 1}`,
      candidates: route.candidates ?? route.candidate_count ?? 0,
      ok: route.ok ?? route.success ?? 0,
      error: route.error ?? route.errors ?? 0,
      ms: route.ms ?? route.elapsed_ms ?? route.duration_ms ?? 0,
    }));
  }
  return Object.entries(routes).map(([name, route]) => ({
    name,
    candidates: route?.candidates ?? route?.candidate_count ?? 0,
    ok: route?.ok ?? route?.success ?? 0,
    error: route?.error ?? route?.errors ?? 0,
    ms: route?.ms ?? route?.elapsed_ms ?? route?.duration_ms ?? 0,
  }));
}

export class DiagnosticsPage {
  constructor(state, apiClient) {
    this.state = state;
    this.api = apiClient;
    this.activeTab = "traces";
    this.loaded = { traces: false, channels: false, external: false, usage: false };
    this.loading = false;
    this.error = "";
    this.feedback = "";

    this.traceLimit = 20;
    this.traceSession = "";
    this.traces = null;
    this.activeTraceId = null;
    this.traceDetail = null;
    this.lineage = null;
    this.lineageMemoryId = "";

    this.channels = null;
    this.activeChannel = null;
    this.channelMemories = null;

    this.external = { keys: [], audit: [], disabled: false, status: null };
    this.token = null;
    this.keyForm = { name: "", mode: "read", personas: "", scopes: "", rate: "60", write: "0" };
    this._creatingKey = false;

    this.usage = null;
    this._bound = false;
  }

  /* ================= lifecycle ================= */

  init() {
    if (this._bound) return;
    registerDiagnosticsMessages();
    ensureDiagnosticsStyles();
    const root = this._element("page-diagnostics");
    if (root && typeof root.addEventListener === "function") {
      root.addEventListener("click", (event) => this._onClick(event));
      root.addEventListener("input", (event) => this._onInput(event));
      root.addEventListener("change", (event) => this._onChange(event));
      root.addEventListener("keydown", (event) => this._onKeydown(event));
    }
    this._bound = true;
  }

  async fetch() {
    this.init();
    await this.loadTab(this.activeTab, true);
  }

  async loadTab(tab, force = false) {
    this.activeTab = tab;
    this.render();
    if (this.loaded[tab] && !force) return;
    this.loading = true;
    this.error = "";
    this.render();
    try {
      if (tab === "traces") await this.fetchTraces();
      else if (tab === "channels") await this.fetchChannels();
      else if (tab === "external") await this.fetchExternal();
      else if (tab === "usage") await this.fetchUsage();
      this.loaded[tab] = true;
    } catch (error) {
      this.error = error.message || window.t("diag.loadFailed");
    } finally {
      this.loading = false;
      this.render();
    }
  }

  async activateTab(tab) {
    if (!["traces", "channels", "external", "usage"].includes(tab)) return;
    this.activeTab = tab;
    this.error = "";
    this.render();
    if (!this.loaded[tab]) await this.loadTab(tab);
  }

  async refreshActive() {
    this.loaded[this.activeTab] = false;
    await this.loadTab(this.activeTab, true);
    if (this.activeTab === "traces" && this.activeTraceId != null) {
      await this.openTrace(this.activeTraceId);
    }
  }

  /* ================= traces ================= */

  async fetchTraces() {
    const params = { limit: this.traceLimit };
    if (this.traceSession) params.session_id = this.traceSession;
    const data = await this.api.get("traces", params);
    this.traces = data || {};
  }

  async openTrace(traceId) {
    try {
      const data = await this.api.get(`traces/${traceId}`);
      this.activeTraceId = traceId;
      this.traceDetail = data && data.trace ? data.trace : data;
      this.lineage = null;
      this.render();
    } catch (error) {
      this.feedback = error.message || window.t("diag.loadFailed");
      this.render();
    }
  }

  closeTrace() {
    this.activeTraceId = null;
    this.traceDetail = null;
    this.lineage = null;
    this.render();
  }

  async loadLineage(memoryId) {
    const id = String(memoryId == null ? "" : memoryId).trim();
    if (!id) return;
    this.lineageMemoryId = id;
    try {
      const data = await this.api.get(`memories/${id}/lineage`);
      this.lineage = { memoryId: id, ...(data || {}) };
      this.render();
    } catch (error) {
      this.feedback = error.message || window.t("diag.loadFailed");
      this.render();
    }
  }

  closeLineage() {
    this.lineage = null;
    this.render();
  }

  /* ================= channels ================= */

  async fetchChannels() {
    const data = await this.api.get("channels");
    this.channels = data || {};
  }

  async openChannel(channel) {
    this.activeChannel = channel || {};
    this.channelMemories = null;
    this.render();
    await this.loadChannelMemories(1);
  }

  async loadChannelMemories(pageToLoad) {
    if (!this.activeChannel) return;
    const scope = this.activeChannel.scope || this.activeChannel.channel_id || "";
    const data = await this.api.get("memories", {
      session_id: scope,
      page: pageToLoad,
      page_size: 20,
      status: "all",
    });
    const items = asArray(data && data.items);
    if (pageToLoad === 1 || !this.channelMemories) {
      this.channelMemories = { items, total: data ? data.total : 0, page: 1 };
    } else {
      this.channelMemories = {
        items: this.channelMemories.items.concat(items),
        total: data ? data.total : this.channelMemories.total,
        page: pageToLoad,
      };
    }
    this.render();
  }

  backToChannels() {
    this.activeChannel = null;
    this.channelMemories = null;
    this.render();
  }

  /* ================= external API ================= */

  async fetchExternal() {
    const status = await this.api.get("external/status");
    if (!status || status.enabled === false) {
      this.external = { keys: [], audit: [], disabled: true, status: status || null };
      this.loaded.external = true;
      return;
    }
    const [keysData, auditData] = await Promise.all([
      this.api.get("external/keys"),
      this.api.get("external/audit", { limit: 50 }),
    ]);
    this.external = {
      keys: asArray(keysData && keysData.keys),
      audit: asArray(auditData && auditData.records),
      disabled: false,
      status,
    };
    this.loaded.external = true;
  }

  async createKey(values = null) {
    if (this._creatingKey) return null;
    const form = values || this._readKeyForm();
    if (!form || !String(form.name || "").trim()) {
      this.feedback = window.t("diag.createFailed");
      this.render();
      return null;
    }
    this._creatingKey = true;
    this.render();
    try {
      const body = {
        name: String(form.name).trim(),
        mode: form.mode === "write" ? "write" : "read",
      };
      const personas = this._splitList(form.personas);
      const scopes = this._splitList(form.scopes);
      if (personas.length) body.allowed_personas = personas;
      if (scopes.length) body.allowed_scopes = scopes;
      const rate = Number(form.rate);
      if (Number.isFinite(rate) && rate > 0) body.rate_limit_per_min = Math.round(rate);
      const write = Number(form.write);
      if (Number.isFinite(write) && write > 0) body.write_limit_per_min = Math.round(write);
      const data = await this.api.post("external/keys", body);
      this.token = (data && (data.token || data.key)) || "";
      this.keyForm = { name: "", mode: "read", personas: "", scopes: "", rate: "60", write: "0" };
      this._creatingKey = false;
      await this.fetchExternal();
      this.render();
      return this.token;
    } catch (error) {
      this.feedback = error.message || window.t("diag.createFailed");
      this._creatingKey = false;
      this.render();
      return null;
    }
  }

  dismissToken() {
    this.token = null;
    this.render();
  }

  async copyToken() {
    if (!this.token) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(this.token);
      }
      this.feedback = window.t("diag.tokenCopied");
    } catch (_error) {
      this.feedback = this.token;
    }
    this.render();
  }

  async _confirmRevoke(key) {
    return confirmAction({
      title: window.t("diag.revokeTitle"),
      message: window.t("diag.revokeMessage", key.name || key.id),
      confirmLabel: window.t("diag.revoke"),
      destructive: true,
    });
  }

  async revokeKey(key) {
    const target = typeof key === "object" ? key : { id: key };
    if (!(await this._confirmRevoke(target))) return;
    try {
      /* 宿主插件 iframe bridge 只有 GET/POST，吊销走静态路由 POST 别名。 */
      await this.api.post("external/keys/revoke", { id: target.id });
      this.feedback = window.t("diag.revokedDone");
      await this.fetchExternal();
    } catch (error) {
      this.feedback = error.message || window.t("diag.revokeFailed");
    }
    this.render();
  }

  async refreshAudit() {
    const data = await this.api.get("external/audit", { limit: 50 });
    this.external = { ...this.external, audit: asArray(data && data.records) };
    this.render();
  }

  /* ================= usage ================= */

  async fetchUsage() {
    const data = await this.api.get("usage");
    this.usage = data || {};
  }

  _splitList(value) {
    return String(value || "")
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  _readKeyForm() {
    const read = (field) => {
      const element = this._element(`diag-key-${field}`);
      return element ? element.value : "";
    };
    const modeEl = this._element("diag-key-mode");
    return {
      name: read("name"),
      mode: modeEl ? modeEl.value : "read",
      personas: read("personas"),
      scopes: read("scopes"),
      rate: read("rate"),
      write: read("write"),
    };
  }

  _element(id) {
    return document.getElementById(id);
  }

  /* ================= event handling ================= */

  _onClick(event) {
    const target = event.target;
    if (!target || typeof target.closest !== "function") return;
    const actionEl = target.closest("[data-diag-action]");
    if (actionEl) {
      const action = actionEl.dataset.diagAction;
      if (action === "tab") this.activateTab(actionEl.dataset.diagTab);
      else if (action === "refresh") this.refreshActive();
      else if (action === "trace-open") this.openTrace(actionEl.dataset.traceId);
      else if (action === "trace-close") this.closeTrace();
      else if (action === "lineage") this.loadLineage(actionEl.dataset.memoryId);
      else if (action === "lineage-close") this.closeLineage();
      else if (action === "lineage-search") {
        const input = this._element("diag-lineage-memory");
        this.loadLineage(input ? input.value : this.lineageMemoryId);
      } else if (action === "channel-open") {
        const index = Number(actionEl.dataset.channelIndex);
        this.openChannel((this.channels && this.channels.channels && this.channels.channels[index]) || null);
      } else if (action === "channel-back") this.backToChannels();
      else if (action === "channel-more") {
        this.loadChannelMemories((this.channelMemories ? this.channelMemories.page : 1) + 1);
      } else if (action === "key-create") this.createKey();
      else if (action === "key-revoke") {
        const key = (this.external.keys || []).find((item) => String(item.id) === String(actionEl.dataset.keyId));
        this.revokeKey(key || { id: actionEl.dataset.keyId });
      } else if (action === "token-copy") this.copyToken();
      else if (action === "token-dismiss") this.dismissToken();
      else if (action === "audit-refresh") this.refreshAudit();
      else if (action === "feedback-dismiss") {
        this.feedback = "";
        this.render();
      }
      return;
    }
    const row = target.closest("[data-diag-row]");
    if (!row) return;
    if (row.dataset.diagRow === "trace") this.openTrace(row.dataset.traceId);
    else if (row.dataset.diagRow === "channel") {
      this.openChannel((this.channels && this.channels.channels && this.channels.channels[Number(row.dataset.channelIndex)]) || null);
    }
  }

  _onInput(event) {
    const target = event.target;
    if (!target || !target.dataset) return;
    if (target.dataset.diagField === "trace-session") this.traceSession = target.value;
    else if (target.dataset.diagField === "lineage-memory") this.lineageMemoryId = target.value;
    else if (target.dataset.keyField) {
      this.keyForm[target.dataset.keyField] = target.value;
    }
  }

  _onChange(event) {
    const target = event.target;
    if (!target || !target.dataset) return;
    if (target.dataset.diagField === "trace-limit") {
      this.traceLimit = Number(target.value) || 20;
      this.loaded.traces = false;
      this.loadTab("traces", true);
    }
  }

  _onKeydown(event) {
    const target = event.target;
    if (!target || !target.dataset || target.dataset.diagRole !== "tab") return;
    const order = ["traces", "channels", "external", "usage"];
    const current = order.indexOf(target.dataset.diagTab);
    if (current === -1) return;
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1) % order.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + order.length) % order.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = order.length - 1;
    else return;
    event.preventDefault();
    this.activateTab(order[next]);
    const nextTab = this._element(`diag-tab-${order[next]}`);
    if (nextTab && typeof nextTab.focus === "function") nextTab.focus();
  }

  /* ================= rendering ================= */

  render() {
    const root = this._element("page-diagnostics");
    if (!root) return;
    const header =
      '<header class="page-header"><div class="page-heading">' +
      '<h1 class="page-title">' + esc(window.t("nav.diagnostics")) + "</h1></div>" +
      '<div class="page-actions"><button type="button" class="btn btn-secondary btn-sm" data-diag-action="refresh">' +
      esc(window.t("diag.refresh")) + "</button></div></header>";
    const tabs = ["traces", "channels", "external", "usage"]
      .map((tab) => {
        const selected = tab === this.activeTab;
        return (
          '<button type="button" class="diag-tab" role="tab" id="diag-tab-' + tab +
          '" aria-selected="' + String(selected) + '" aria-controls="diag-panel-' + tab +
          '" tabindex="' + (selected ? "0" : "-1") +
          '" data-diag-action="tab" data-diag-tab="' + tab +
          '" data-diag-role="tab">' + esc(window.t(this._tabLabelKey(tab))) + "</button>"
        );
      })
      .join("");
    let body = "";
    if (this.loading) body = '<p class="diag-state">' + esc(window.t("diag.loading")) + "</p>";
    else if (this.error) {
      body =
        '<div class="request-feedback is-error" role="status"><span>' + esc(this.error) + "</span>" +
        '<button type="button" class="btn btn-sm btn-secondary" data-diag-action="refresh">' +
        esc(window.t("diag.retry")) + "</button></div>";
    } else {
      body = '<div id="diag-panel-' + this.activeTab + '" role="tabpanel" aria-labelledby="diag-tab-' +
        this.activeTab + '">' + this._renderPanel() + "</div>";
    }
    const feedback = this.feedback
      ? '<div class="request-feedback" role="status"><span>' + esc(this.feedback) + "</span>" +
        '<button type="button" class="btn btn-sm btn-secondary" data-diag-action="feedback-dismiss">' +
        esc(window.t("diag.close")) + "</button></div>"
      : "";
    root.innerHTML =
      header + '<div class="page-body diag-body">' + feedback +
      '<div class="diag-tabs" role="tablist" aria-label="' + esc(window.t("nav.diagnostics")) + '">' +
      tabs + "</div>" + body + "</div>";
    window.lmHydrateIcons?.();
  }

  _tabLabelKey(tab) {
    return {
      traces: "diag.tabTraces",
      channels: "diag.tabChannels",
      external: "diag.tabExternal",
      usage: "diag.tabUsage",
    }[tab];
  }

  _renderPanel() {
    if (this.activeTab === "traces") return this._renderTraces();
    if (this.activeTab === "channels") return this._renderChannels();
    if (this.activeTab === "external") return this._renderExternal();
    return this._renderUsage();
  }

  _empty(text) {
    return '<div class="empty-state"><i data-lucide="database" aria-hidden="true"></i><span>' +
      esc(text) + "</span></div>";
  }

  /* ----- traces ----- */

  _renderTraces() {
    const data = this.traces || {};
    const enabled = data.enabled !== false;
    let html = '<div class="diag-toolbar">';
    html += '<label class="diag-field"><span>' + esc(window.t("diag.limit")) + "</span>" +
      '<select class="input input-sm" data-diag-field="trace-limit">' +
      [10, 20, 50].map((value) => '<option value="' + value + '"' +
        (Number(this.traceLimit) === value ? " selected" : "") + ">" + value + "</option>").join("") +
      "</select></label>";
    html += '<label class="diag-field"><span>' + esc(window.t("diag.sessionOptional")) + "</span>" +
      '<input class="input input-sm" type="text" data-diag-field="trace-session" value="' +
      esc(this.traceSession) + '"></label>';
    html += '<button type="button" class="btn btn-secondary btn-sm" data-diag-action="refresh">' +
      esc(window.t("diag.refresh")) + "</button>";
    html += '<span class="diag-status">' +
      esc(window.t(enabled ? "diag.recorderOn" : "diag.recorderOff")) +
      (data.total_records != null ? " · " + esc(window.t("diag.records", data.total_records)) : "") +
      (data.persist_enabled != null
        ? " · " + esc(window.t(data.persist_enabled ? "diag.persistOn" : "diag.persistOff"))
        : "") +
      "</span></div>";
    if (!enabled) return html + this._empty(window.t("diag.tracesDisabled"));
    const traces = asArray(data.traces);
    if (!traces.length) return html + this._empty(window.t("diag.tracesEmpty"));
    html += '<div class="diag-table-wrap"><table class="data-table diag-table"><thead><tr>' +
      ["diag.time", "diag.scope", "diag.cacheHit", "diag.routes", "diag.injected", "diag.duration"]
        .map((key) => "<th>" + esc(window.t(key)) + "</th>").join("") +
      "</tr></thead><tbody>";
    traces.forEach((trace, index) => {
      const id = trace.id != null ? trace.id : trace.trace_id != null ? trace.trace_id : index;
      const routeRowsList = routeRows(trace.routes);
      const routeSummary = routeRowsList.length
        ? routeRowsList.map((route) => route.name + ":" + route.candidates).join(" / ")
        : "--";
      const injectedCount =
        trace.injected_count != null
          ? trace.injected_count
          : typeof trace.injected === "number"
            ? trace.injected
            : asArray(trace.injected).length;
      const injectedChars = trace.injected_chars != null ? trace.injected_chars : "";
      const cache = trace.cache_hit != null ? trace.cache_hit : trace.cache && trace.cache.hit;
      html += '<tr class="diag-row" data-diag-row="trace" data-trace-id="' + esc(String(id)) + '">' +
        "<td>" + esc(formatTime(trace.ts ?? trace.created_at ?? trace.timestamp)) + "</td>" +
        "<td>" + esc(trace.scope || trace.umo || trace.session_id || "--") + "</td>" +
        "<td>" + esc(window.t(cache ? "diag.cacheYes" : "diag.cacheNo")) + "</td>" +
        '<td title="' + esc(routeSummary) + '">' + esc(routeSummary) + "</td>" +
        "<td>" + esc(String(injectedCount)) + (injectedChars !== "" ? " / " + esc(String(injectedChars)) + " chars" : "") + "</td>" +
        "<td>" + esc(String(trace.duration_ms ?? trace.elapsed_ms ?? "--")) + " ms</td></tr>";
    });
    html += "</tbody></table></div>";
    if (this.traceDetail) html += this._renderTraceDetail();
    html += this._renderLineageSearch();
    if (this.lineage) html += this._renderLineage();
    return html;
  }

  _renderTraceDetail() {
    const trace = this.traceDetail || {};
    let html = '<section class="diag-panel"><div class="diag-panel-head"><h3>' +
      esc(window.t("diag.detail")) + "</h3>" +
      '<button type="button" class="btn btn-secondary btn-sm" data-diag-action="trace-close">' +
      esc(window.t("diag.close")) + "</button></div>";
    const rows = routeRows(trace.routes);
    html += "<h4>" + esc(window.t("diag.routes")) + "</h4>";
    if (rows.length) {
      html += '<table class="data-table diag-table"><thead><tr><th>' + esc(window.t("diag.route")) +
        "</th><th>" + esc(window.t("diag.candidates")) + "</th><th>" + esc(window.t("diag.ok")) +
        "</th><th>" + esc(window.t("diag.error")) + "</th><th>" + esc(window.t("diag.duration")) +
        "</th></tr></thead><tbody>" +
        rows.map((route) => "<tr><td>" + esc(route.name) + "</td><td>" + esc(String(route.candidates)) +
          "</td><td>" + formatOk(route.ok) + "</td><td>" + esc(String(route.error)) +
          "</td><td>" + esc(String(route.ms)) + " ms</td></tr>").join("") +
        "</tbody></table>";
    } else {
      html += this._empty(window.t("diag.empty"));
    }
    const filters = trace.filters || {};
    html += "<h4>" + esc(window.t("diag.filters")) + "</h4><div class=\"diag-chips\">";
    if (filters.min_importance != null) {
      html += '<span class="diag-chip">' + esc(window.t("diag.minImportance", filters.min_importance)) + "</span>";
    }
    if (filters.min_similarity != null) {
      html += '<span class="diag-chip">' + esc(window.t("diag.minSimilarity", filters.min_similarity)) + "</span>";
    }
    const rejected = filters.rejected || filters.rejected_reasons || {};
    Object.entries(rejected).forEach(([reason, count]) => {
      html += '<span class="diag-chip">' + esc(reason) + ": " + esc(String(count)) + "</span>";
    });
    html += "</div>";
    const candidates = asArray(trace.candidates || trace.top_candidates).slice(0, 10);
    html += "<h4>" + esc(window.t("diag.topCandidates", 10)) + "</h4>";
    if (candidates.length) {
      html += '<ul class="diag-list">' + candidates.map((candidate) => {
        const memoryId = candidate.memory_id ?? candidate.doc_id ?? candidate.id;
        const score = candidate.score ?? candidate.final_score ?? "--";
        return "<li>#" + esc(String(memoryId)) + ' <span class="diag-muted">' +
          esc(window.t("diag.score")) + " " + esc(String(score)) + "</span>" +
          (memoryId != null
            ? ' <button type="button" class="btn btn-ghost btn-sm" data-diag-action="lineage" data-memory-id="' +
              esc(String(memoryId)) + '">' + esc(window.t("diag.lineage")) + "</button>"
            : "") + "</li>";
      }).join("") + "</ul>";
    } else {
      html += this._empty(window.t("diag.empty"));
    }
    const injected = asArray(trace.injected);
    html += "<h4>" + esc(window.t("diag.injectedList")) + "</h4>";
    if (injected.length) {
      html += '<ul class="diag-list">' + injected.map((item) => {
        const memoryId = typeof item === "object" ? item.memory_id ?? item.doc_id ?? item.id : item;
        return "<li>#" + esc(String(memoryId)) +
          (typeof item === "object" && item.chars != null ? ' <span class="diag-muted">' + esc(String(item.chars)) + " chars</span>" : "") +
          ' <button type="button" class="btn btn-ghost btn-sm" data-diag-action="lineage" data-memory-id="' +
          esc(String(memoryId)) + '">' + esc(window.t("diag.lineage")) + "</button></li>";
      }).join("") + "</ul>";
    } else {
      html += this._empty(window.t("diag.empty"));
    }
    if (trace.agentic != null) {
      html += '<p class="diag-meta">' + esc(window.t("diag.agentic")) + ": " + esc(JSON.stringify(trace.agentic)) + "</p>";
    }
    const budget = trace.budget != null ? trace.budget : trace.budget_truncated;
    if (budget != null) {
      html += '<p class="diag-meta">' + esc(window.t("diag.budget")) + ": " +
        esc(typeof budget === "object" ? JSON.stringify(budget) : String(budget)) + "</p>";
    }
    const notes = Array.isArray(trace.notes) ? trace.notes.join("; ") : trace.notes;
    if (notes) {
      html += '<p class="diag-meta">' + esc(window.t("diag.notes")) + ": " + esc(String(notes)) + "</p>";
    }
    return html + "</section>";
  }

  _renderLineageSearch() {
    return '<div class="diag-lineage-search"><label class="diag-field"><span>' +
      esc(window.t("diag.memoryId")) + "</span>" +
      '<input class="input input-sm" type="text" id="diag-lineage-memory" data-diag-field="lineage-memory" value="' +
      esc(this.lineageMemoryId) + '" placeholder="' + esc(window.t("diag.memoryIdPh")) + '"></label>' +
      '<button type="button" class="btn btn-secondary btn-sm" data-diag-action="lineage-search">' +
      esc(window.t("diag.lineageSearch")) + "</button></div>";
  }

  _renderLineage() {
    const lineage = this.lineage || {};
    const memory = lineage.memory || {};
    let html = '<section class="diag-panel"><div class="diag-panel-head"><h3>' +
      esc(window.t("diag.lineageTitle", lineage.memoryId || memory.id || "--")) + "</h3>" +
      '<button type="button" class="btn btn-secondary btn-sm" data-diag-action="lineage-close">' +
      esc(window.t("diag.close")) + "</button></div>";
    html += "<h4>" + esc(window.t("diag.memoryInfo")) + "</h4><dl class=\"diag-dl\">" +
      "<dt>" + esc(window.t("diag.memoryId")) + "</dt><dd>" + esc(String(memory.id ?? lineage.memoryId ?? "--")) + "</dd>" +
      "<dt>" + esc(window.t("diag.summary")) + "</dt><dd>" + esc(String(memory.summary || memory.text || "--")) + "</dd>" +
      "<dt>" + esc(window.t("diag.importance")) + "</dt><dd>" + esc(String(memory.importance ?? "--")) + "</dd>" +
      "<dt>" + esc(window.t("diag.status")) + "</dt><dd>" + esc(String(memory.status ?? "--")) + "</dd>" +
      "<dt>" + esc(window.t("diag.createdAt")) + "</dt><dd>" + esc(formatTime(memory.created_at ?? memory.create_time)) + "</dd></dl>";
    const sourcesPayload = lineage.sources;
    const sources = Array.isArray(sourcesPayload)
      ? sourcesPayload
      : asArray(sourcesPayload && sourcesPayload.messages);
    const sourceWindow = isPlainObject(sourcesPayload)
      ? sourcesPayload.source_window || sourcesPayload.source_windows
      : null;
    html += "<h4>" + esc(window.t("diag.sources")) + "</h4>";
    if (sources.length) {
      html += '<ul class="diag-list">' + sources.map((source) => {
        const text = source.content || source.text || (source.sender_name ? source.sender_name + ": " : "") + (source.message || "");
        const windowText = source.timestamp ? formatTime(source.timestamp) : (source.window || "");
        return "<li>" + esc(String(text || "--")) +
          (windowText ? ' <span class="diag-muted">' + esc(window.t("diag.window")) + " " + esc(String(windowText)) + "</span>" : "") +
          "</li>";
      }).join("") + "</ul>";
    } else {
      html += this._empty(window.t("diag.empty"));
    }
    if (sourceWindow) {
      html += '<p class="diag-meta">' + esc(window.t("diag.window")) + ": " +
        esc(JSON.stringify(sourceWindow)) + "</p>";
    }
    const supersedesPayload = lineage.supersedes;
    const supersedeChain = isPlainObject(supersedesPayload)
      ? asArray(supersedesPayload.chain)
      : asArray(supersedesPayload);
    const supersededBy = isPlainObject(supersedesPayload) ? supersedesPayload.superseded_by : null;
    const supersedesOf = isPlainObject(supersedesPayload) ? asArray(supersedesPayload.of) : [];
    html += "<h4>" + esc(window.t("diag.supersedes")) + "</h4><p class=\"diag-meta\">" +
      esc(
        supersedeChain.length
          ? supersedeChain.map((item) => (typeof item === "object" ? item.id ?? item : item)).map(String).join(" -> ")
          : supersededBy != null
            ? String(supersededBy)
            : supersedesOf.map((item) => (typeof item === "object" ? item.id ?? JSON.stringify(item) : item)).map(String).join(", ") || "--"
      ) + "</p>";
    const linksPayload = lineage.links;
    let linksText = "--";
    if (Array.isArray(linksPayload)) {
      linksText = linksPayload.map(String).join(", ") || "--";
    } else if (isPlainObject(linksPayload)) {
      const parts = [];
      if (linksPayload.previous_id != null) parts.push("previous:" + linksPayload.previous_id);
      if (linksPayload.parent_memory_id != null) parts.push("parent:" + linksPayload.parent_memory_id);
      const related = asArray(linksPayload.related_ids);
      if (related.length) parts.push("related:" + related.join(", "));
      linksText = parts.join(" · ") || "--";
    }
    html += "<h4>" + esc(window.t("diag.links")) + "</h4><p class=\"diag-meta\">" +
      esc(linksText) + "</p>";
    const graph = lineage.graph || {};
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : asArray(graph.node_ids);
    const edges = Array.isArray(graph.edges) ? graph.edges : asArray(graph.edge_ids);
    html += "<h4>" + esc(window.t("diag.graphSummary")) + "</h4><p class=\"diag-meta\">" +
      esc(window.t("diag.graphCounts",
        graph.node_count ?? nodes.length,
        graph.edge_count ?? edges.length)) + "</p>";
    if (nodes.length) {
      html += '<ul class="diag-list">' + nodes.slice(0, 8).map((node) =>
        "<li>" + esc(typeof node === "object" ? (node.label || node.id || JSON.stringify(node)) : String(node)) + "</li>"
      ).join("") + "</ul>";
    }
    const traces = asArray(lineage.traces);
    html += "<h4>" + esc(window.t("diag.traceHits")) + "</h4>";
    html += traces.length
      ? '<ul class="diag-list">' + traces.slice(0, 10).map((trace) => {
          const id = trace.id ?? trace.trace_id ?? trace;
          return "<li>#" + esc(String(id)) + " " +
            (trace.ts || trace.created_at ? esc(formatTime(trace.ts ?? trace.created_at)) : "") + "</li>";
        }).join("") + "</ul>"
      : this._empty(window.t("diag.empty"));
    html += "</section>";
    return html;
  }

  /* ----- channels ----- */

  _renderChannels() {
    if (this.activeChannel) return this._renderChannelMemories();
    const channels = asArray(this.channels && this.channels.channels);
    if (!channels.length) return this._empty(window.t("diag.channelsEmpty"));
    let html = '<div class="diag-table-wrap"><table class="data-table diag-table"><thead><tr>' +
      ["diag.scope", "diag.platform", "diag.channelId", "diag.memoryCount", "diag.latest"]
        .map((key) => "<th>" + esc(window.t(key)) + "</th>").join("") + "</tr></thead><tbody>";
    channels.forEach((channel, index) => {
      html += '<tr class="diag-row" data-diag-row="channel" data-channel-index="' + index + '">' +
        "<td>" + esc(channel.scope || "--") + "</td>" +
        "<td>" + esc(channel.platform || "--") + "</td>" +
        "<td>" + esc(channel.channel_id || "--") + "</td>" +
        "<td>" + esc(String(channel.memory_count ?? 0)) + "</td>" +
        "<td>" + esc(formatTime(channel.latest_create_time)) + "</td></tr>";
    });
    html += "</tbody></table></div>";
    return html;
  }

  _renderChannelMemories() {
    const channel = this.activeChannel || {};
    const label = channel.scope || channel.channel_id || "--";
    let html = '<div class="diag-panel-head"><h3>' + esc(window.t("diag.channelMemories", label)) + "</h3>" +
      '<button type="button" class="btn btn-secondary btn-sm" data-diag-action="channel-back">' +
      esc(window.t("diag.back")) + "</button></div>";
    const data = this.channelMemories;
    if (!data || !data.items.length) return html + this._empty(window.t("diag.channelMemoriesEmpty"));
    html += '<div class="diag-table-wrap"><table class="data-table diag-table"><thead><tr>' +
      ["diag.memoryId", "diag.preview", "diag.importance", "diag.status", "diag.createdAt"]
        .map((key) => "<th>" + esc(window.t(key)) + "</th>").join("") + "</tr></thead><tbody>";
    data.items.forEach((item) => {
      const metadata = item.metadata || {};
      const preview = String(item.summary || item.text || item.content || "").slice(0, 120);
      const importance = Number(metadata.importance ?? item.importance ?? 0);
      html += "<tr><td>#" + esc(String(item.id ?? item.memory_id ?? "--")) + "</td><td>" +
        esc(preview) + "</td><td>" + esc((importance <= 1 ? importance * 10 : importance).toFixed(1)) +
        "</td><td>" + esc(metadata.status || item.status || "--") + "</td><td>" +
        esc(formatTime(metadata.create_time ?? item.created_at)) + "</td></tr>";
    });
    html += "</tbody></table></div>";
    if (data.items.length < data.total) {
      html += '<button type="button" class="btn btn-secondary btn-sm" data-diag-action="channel-more">' +
        esc(window.t("diag.loadMore")) + "</button>";
    }
    return html;
  }

  /* ----- external API ----- */

  _renderExternal() {
    if (this.external.disabled) {
      const status = this.external.status || {};
      const listen = status.listen_host ? esc(status.listen_host + ":" + status.listen_port) : "";
      return '<section class="diag-panel"><div class="diag-panel-head"><h3>' +
        esc(window.t("diag.externalDisabled")) + "</h3></div>" +
        '<p class="diag-hint">' + esc(window.t("diag.externalDisabledHint")) + "</p>" +
        (listen ? '<p class="diag-hint">' + listen + "</p>" : "") + "</section>";
    }
    let html = "";
    if (this.token) {
      html += '<section class="diag-panel diag-token-panel"><div class="diag-panel-head"><h3>' +
        esc(window.t("diag.tokenTitle")) + "</h3></div>" +
        '<p class="diag-warning">' + esc(window.t("diag.tokenWarning")) + "</p>" +
        '<code class="diag-token">' + esc(this.token) + "</code>" +
        '<div class="diag-actions"><button type="button" class="btn btn-secondary btn-sm" data-diag-action="token-copy">' +
        esc(window.t("diag.tokenCopy")) + "</button>" +
        '<button type="button" class="btn btn-primary btn-sm" data-diag-action="token-dismiss">' +
        esc(window.t("diag.tokenClose")) + "</button></div></section>";
    }
    html += '<section class="diag-panel"><div class="diag-panel-head"><h3>' + esc(window.t("diag.keys")) +
      "</h3></div>";
    const keys = asArray(this.external.keys);
    if (!keys.length) {
      html += this._empty(window.t("diag.noKeys"));
    } else {
      html += '<div class="diag-table-wrap"><table class="data-table diag-table diag-table-wide"><thead><tr>' +
        ["diag.name", "diag.prefix", "diag.permissions", "diag.personaWhitelist", "diag.scopeWhitelist",
          "diag.rateLimit", "diag.lastUsed", "diag.createdAt", "diag.status"]
          .map((key) => "<th>" + esc(window.t(key)) + "</th>").join("") + "<th></th></tr></thead><tbody>";
      keys.forEach((key) => {
        const revoked = Boolean(key.revoked_at);
        const scopes = asArray(key.scopes).length ? key.scopes : [];
        html += "<tr><td>" + esc(key.name || "--") + "</td><td>" + esc((key.key_prefix || "--") + "…") +
          "</td><td>" + esc(scopes.includes("write") || key.mode === "write" ? window.t("diag.readWrite") : window.t("diag.readOnly")) +
          "</td><td>" + esc(asArray(key.allowed_personas).join(", ") || "--") +
          "</td><td>" + esc(asArray(key.allowed_scopes).join(", ") || "--") +
          "</td><td>" + esc(String(key.rate_limit_per_min ?? "--")) + "/" + esc(String(key.write_limit_per_min ?? 0)) +
          "</td><td>" + esc(formatTime(key.last_used_at)) + "</td><td>" + esc(formatTime(key.created_at)) +
          "</td><td>" + esc(window.t(revoked ? "diag.revoked" : "diag.active")) + "</td><td>" +
          (revoked ? "" : '<button type="button" class="btn btn-danger btn-sm" data-diag-action="key-revoke" data-key-id="' +
            esc(String(key.id)) + '">' + esc(window.t("diag.revoke")) + "</button>") + "</td></tr>";
      });
      html += "</tbody></table></div>";
    }
    html += "</section>";
    html += '<section class="diag-panel"><div class="diag-panel-head"><h3>' + esc(window.t("diag.createTitle")) +
      "</h3></div><div class=\"diag-form\">" +
      '<label class="diag-field"><span>' + esc(window.t("diag.name")) + ' *</span>' +
      '<input class="input input-sm" id="diag-key-name" data-key-field="name" value="' + esc(this.keyForm.name) + '"></label>' +
      '<label class="diag-field"><span>' + esc(window.t("diag.mode")) + "</span>" +
      '<select class="input input-sm" id="diag-key-mode" data-key-field="mode">' +
      '<option value="read"' + (this.keyForm.mode === "read" ? " selected" : "") + ">" + esc(window.t("diag.readOnly")) + "</option>" +
      '<option value="write"' + (this.keyForm.mode === "write" ? " selected" : "") + ">" + esc(window.t("diag.readWrite")) + "</option></select></label>" +
      '<label class="diag-field"><span>' + esc(window.t("diag.personaWhitelist")) + "</span>" +
      '<input class="input input-sm" id="diag-key-personas" data-key-field="personas" value="' + esc(this.keyForm.personas) +
      '" placeholder="' + esc(window.t("diag.personasPh")) + '"></label>' +
      '<label class="diag-field"><span>' + esc(window.t("diag.scopeWhitelist")) + "</span>" +
      '<input class="input input-sm" id="diag-key-scopes" data-key-field="scopes" value="' + esc(this.keyForm.scopes) +
      '" placeholder="' + esc(window.t("diag.scopesPh")) + '"></label>' +
      '<label class="diag-field"><span>' + esc(window.t("diag.rateLimit")) + "</span>" +
      '<input class="input input-sm" type="number" min="0" id="diag-key-rate" data-key-field="rate" value="' + esc(this.keyForm.rate) + '"></label>' +
      '<label class="diag-field"><span>' + esc(window.t("diag.writeLimit")) + "</span>" +
      '<input class="input input-sm" type="number" min="0" id="diag-key-write" data-key-field="write" value="' + esc(this.keyForm.write) + '"></label>' +
      '<button type="button" class="btn btn-primary btn-sm" data-diag-action="key-create"' +
      (this._creatingKey ? " disabled" : "") + ">" +
      esc(window.t(this._creatingKey ? "diag.creating" : "diag.create")) + "</button></div></section>";
    html += '<section class="diag-panel"><div class="diag-panel-head"><h3>' + esc(window.t("diag.audit")) +
      "</h3><button type=\"button\" class=\"btn btn-secondary btn-sm\" data-diag-action=\"audit-refresh\">" +
      esc(window.t("diag.refresh")) + "</button></div>";
    const audit = asArray(this.external.audit);
    if (!audit.length) {
      html += this._empty(window.t("diag.auditEmpty"));
    } else {
      html += '<div class="diag-table-wrap"><table class="data-table diag-table"><thead><tr>' +
        ["diag.time", "diag.keyName", "diag.action", "diag.statusCode", "diag.latency", "diag.memoryId"]
          .map((key) => "<th>" + esc(window.t(key)) + "</th>").join("") + "</tr></thead><tbody>";
      audit.slice(0, 50).forEach((record) => {
        html += "<tr><td>" + esc(formatTime(record.ts)) + "</td><td>" +
          esc(record.key_name || record.key_id || "--") + "</td><td>" + esc(record.action || "--") +
          "</td><td>" + esc(String(record.status_code ?? "--")) + "</td><td>" +
          esc(String(record.latency_ms ?? "--")) + "</td><td>" + esc(String(record.memory_id ?? "--")) + "</td></tr>";
      });
      html += "</tbody></table></div>";
    }
    return html + "</section>";
  }

  /* ----- usage ----- */

  _renderUsage() {
    const usage = this.usage && this.usage.usage ? this.usage.usage : this.usage || {};
    const providers = usage.providers && typeof usage.providers === "object" ? usage.providers : {};
    const fallback = asArray(this.usage && this.usage.fallback_provider_ids);
    const timeout = this.usage ? this.usage.timeout_seconds : null;
    const cards = [
      ["diag.usageCalls", usage.calls],
      ["diag.usageInput", usage.input_tokens],
      ["diag.usageOutput", usage.output_tokens],
      ["diag.usageTotal", usage.total_tokens],
      ["diag.usageEstimated", usage.estimated_calls],
      ["diag.usageFallback", usage.fallback_calls],
    ];
    let html = '<div class="diag-cards">' + cards.map(([key, value]) =>
      '<div class="diag-card"><div class="diag-card-label">' + esc(window.t(key)) + "</div>" +
      '<div class="diag-card-value">' + esc(formatNumber(value)) + "</div></div>"
    ).join("") + "</div>";
    html += '<section class="diag-panel"><div class="diag-panel-head"><h3>' + esc(window.t("diag.usageProviders")) +
      "</h3></div>";
    const providerRows = Object.entries(providers).sort((a, b) => Number(b[1]) - Number(a[1]));
    if (!providerRows.length) {
      html += this._empty(window.t("diag.usageEmpty"));
    } else {
      html += '<table class="data-table diag-table"><thead><tr><th>' + esc(window.t("diag.provider")) +
        "</th><th>" + esc(window.t("diag.usageCalls")) + "</th></tr></thead><tbody>" +
        providerRows.map(([name, count]) => "<tr><td>" + esc(name) + "</td><td>" +
          esc(formatNumber(count)) + "</td></tr>").join("") + "</tbody></table>";
    }
    html += "</section>";
    html += '<section class="diag-panel"><div class="diag-panel-head"><h3>' + esc(window.t("diag.usageFallbackChain")) +
      "</h3></div><p class=\"diag-meta\">" +
      (fallback.length ? esc(fallback.join(" → ")) : esc(window.t("diag.usageNoFallback"))) + "</p>" +
      '<p class="diag-meta">' + esc(window.t("diag.usageTimeout")) + ": " +
      esc(timeout != null ? window.t("diag.seconds", timeout) : "--") + "</p></section>";
    return html;
  }
}
