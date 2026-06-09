/* global localStorage, URLSearchParams, CustomEvent */

(() => {
  const LANG_KEY = "lmem_lang";
  const SUPPORTED = ["zh", "en", "ru"];
  let urlLanguageOverride = false;

  const MSG = {
    /* ---- Common ---- */
    "common.close":       { zh: "关闭", en: "Close", ru: "Закрыть" },
    "common.cancel":      { zh: "取消", en: "Cancel", ru: "Отмена" },
    "common.clear":       { zh: "清空", en: "Clear", ru: "Очистить" },
    "common.save":        { zh: "保存", en: "Save", ru: "Сохранить" },
    "common.refresh":     { zh: "刷新", en: "Refresh", ru: "Обновить" },
    "common.search":      { zh: "搜索", en: "Search", ru: "Поиск" },
    "common.confirm":     { zh: "确定", en: "Confirm", ru: "Подтвердить" },
    "common.loading":     { zh: "加载中...", en: "Loading...", ru: "Загрузка..." },
    "common.noData":      { zh: "暂无数据", en: "No data", ru: "Нет данных" },
    "common.unavailable": { zh: "暂不可用", en: "Unavailable", ru: "Недоступно" },
    "common.page":        { zh: "第 {0} / {1} 页 · 共 {2} 条", en: "Page {0}/{1} · {2} total", ru: "Стр. {0}/{1} · всего {2}" },
    "common.perPage":     { zh: "每页", en: "Per page", ru: "На стр." },
    "common.perPage20":   { zh: "20 条/页", en: "20 per page", ru: "20 на стр." },
    "common.perPage50":   { zh: "50 条/页", en: "50 per page", ru: "50 на стр." },
    "common.perPage100":  { zh: "100 条/页", en: "100 per page", ru: "100 на стр." },

    /* ---- Title / Header ---- */
    "page.title":         { zh: "LivingMemory 控制台", en: "LivingMemory Console", ru: "Консоль LivingMemory" },
    "header.title":       { zh: "LivingMemory 管理面板", en: "LivingMemory Dashboard", ru: "Панель LivingMemory" },
    "header.subtitle":    { zh: "长期记忆与会话管理 · 基于混合检索的智能记忆系统", en: "Long-term memory & session management · Hybrid retrieval system", ru: "Долгосрочная память и управление сессиями · Гибридная поисковая система" },
    "header.theme":       { zh: "切换主题", en: "Toggle theme", ru: "Сменить тему" },
    "header.lang":        { zh: "语言", en: "Language", ru: "Язык" },
    "language.current.zh": { zh: "中文", en: "Chinese", ru: "Китайский" },
    "language.current.en": { zh: "英文", en: "English", ru: "Английский" },
    "language.current.ru": { zh: "俄文", en: "Russian", ru: "Русский" },
    "language.toast":     { zh: "语言：{0}", en: "Language: {0}", ru: "Язык: {0}" },

    /* ---- Navigation ---- */
    "nav.memory":         { zh: "记忆管理", en: "Memory", ru: "Память" },
    "nav.graph":          { zh: "知识图谱", en: "Knowledge Graph", ru: "Граф знаний" },
    "nav.recallTest":     { zh: "召回测试", en: "Recall Test", ru: "Тест поиска" },
    "nav.system":         { zh: "系统概览", en: "System", ru: "Система" },
    "nav.recall":         { zh: "召回测试", en: "Recall Test", ru: "Тест поиска" },

    /* ---- Nuke ---- */
    "nuke.cancel":        { zh: "取消核爆", en: "Cancel Nuke", ru: "Отменить сброс" },
    "nuke.button":        { zh: "核爆清除", en: "Nuke Clear", ru: "Полный сброс" },
    "nuke.startToast":    { zh: "💥 核爆倒计时启动！", en: "💥 Nuke countdown started!", ru: "💥 Обратный отсчёт запущен!" },
    "nuke.cancelledToast":{ zh: " 核爆已取消！记忆保留", en: " Nuke cancelled! Memories preserved.", ru: " Сброс отменён! Память сохранена." },
    "nuke.cancelFail":    { zh: "取消失败，请稍后重试", en: "Cancel failed, please retry", ru: "Не удалось отменить, попробуйте позже" },
    "nuke.countdown":     { zh: "所有记忆将在 {0} 秒后被抹除。立即取消以中止核爆！", en: "All memories will be erased in {0}s. Cancel now to abort!", ru: "Вся память будет удалена через {0} сек. Отмените сейчас!" },
    "nuke.erasing":       { zh: "正在抹除所有记忆... 请保持窗口打开。", en: "Erasing all memories... Keep this window open.", ru: "Удаление всей памяти... Не закрывайте окно." },
    "nuke.doneTable":     { zh: " 核爆完成！所有记忆已被抹除。点击「刷新」重新加载。", en: " Nuke complete! All memories erased. Click Refresh to reload.", ru: " Сброс завершён! Вся память удалена. Нажмите Обновить." },
    "nuke.doneToast":     { zh: " 核爆完成！所有记忆已从界面移除（仅视觉效果）", en: " Nuke complete! All memories removed (visual only).", ru: " Сброс завершён! Память удалена (визуально)." },
    "nuke.cantStart":     { zh: "无法启动核爆模式", en: "Cannot start nuke mode", ru: "Не удалось запустить режим сброса" },

    /* ---- Stats ---- */
    "stats.total":        { zh: "总记忆", en: "Total", ru: "Всего" },
    "stats.active":       { zh: "活跃", en: "Active", ru: "Активно" },
    "stats.archived":     { zh: "已归档", en: "Archived", ru: "Архив" },
    "stats.deleted":      { zh: "已删除", en: "Deleted", ru: "Удалено" },
    "stats.sessions":     { zh: "活跃会话", en: "Active Sessions", ru: "Активных сессий" },
    "stats.graphNodes":   { zh: "图谱节点", en: "Graph Nodes", ru: "Узлы графа" },
    "stats.atoms":        { zh: "原子记忆", en: "Atoms", ru: "Атомы" },

    /* ---- Filter ---- */
    "filter.keyword":     { zh: "关键字（支持 memory_id / 内容搜索）", en: "Keyword (memory_id / content)", ru: "Ключевое слово (memory_id / контент)" },
    "filter.sessionId":   { zh: "会话 ID（可选）", en: "Session ID (optional)", ru: "ID сессии (опц.)" },
    "filter.statusAll":   { zh: "全部状态", en: "All Statuses", ru: "Все статусы" },
    "filter.statusActive":{ zh: "活跃", en: "Active", ru: "Активно" },
    "filter.statusArchived":{ zh: "已归档", en: "Archived", ru: "Архив" },
    "filter.statusDeleted":{ zh: "已删除", en: "Deleted", ru: "Удалено" },
    "filter.typeAll":     { zh: "全部类型", en: "All Types", ru: "Все типы" },
    "filter.apply":       { zh: "筛选", en: "Filter", ru: "Фильтр" },

    /* ---- Sort ---- */
    "sort.createdDesc":   { zh: "最新创建", en: "Newest first", ru: "Сначала новые" },
    "sort.createdAsc":    { zh: "最早创建", en: "Oldest first", ru: "Сначала старые" },
    "sort.updatedDesc":   { zh: "最近更新", en: "Recently updated", ru: "Недавно обновлено" },
    "sort.importanceDesc":{ zh: "重要性高到低", en: "Importance high to low", ru: "Важность по убыванию" },
    "sort.importanceAsc": { zh: "重要性低到高", en: "Importance low to high", ru: "Важность по возрастанию" },
    "sort.typeAsc":       { zh: "类型 A-Z", en: "Type A-Z", ru: "Тип A-Z" },

    /* ---- Table ---- */
    "table.id":           { zh: "记忆 ID", en: "Memory ID", ru: "ID памяти" },
    "table.summary":      { zh: "摘要", en: "Summary", ru: "Сводка" },
    "table.type":         { zh: "类型", en: "Type", ru: "Тип" },
    "table.importance":   { zh: "重要性", en: "Importance", ru: "Важность" },
    "table.status":       { zh: "状态", en: "Status", ru: "Статус" },
    "table.created":      { zh: "创建时间", en: "Created", ru: "Создано" },
    "table.lastAccess":   { zh: "最后访问", en: "Last Access", ru: "Доступ" },
    "table.actions":      { zh: "操作", en: "Actions", ru: "Действия" },
    "table.detail":       { zh: "详情", en: "Detail", ru: "Детали" },
    "table.noSummary":    { zh: "（无摘要）", en: "(No summary)", ru: "(Нет сводки)" },
    "table.noContent":    { zh: "（无内容）", en: "(No content)", ru: "(Нет контента)" },
    "table.noData":       { zh: "暂无数据", en: "No data", ru: "Нет данных" },
    "table.na":           { zh: "--", en: "--", ru: "--" },
    "table.updated":      { zh: "更新于 {0}", en: "Updated {0}", ru: "Обновлено {0}" },

    /* ---- Pagination ---- */
    "pagination.prev":    { zh: "上一页", en: "Previous", ru: "Пред." },
    "pagination.next":    { zh: "下一页", en: "Next", ru: "След." },
    "pagination.allLoaded":{ zh: "共 {0} 条记录（已加载全部）", en: "{0} records (all loaded)", ru: "{0} записей (загружено все)" },
    "pagination.filtering":{ zh: "筛选中:", en: "Filtering:", ru: "Фильтр:" },
    "pagination.byKeyword":{ zh: "关键词=\"{0}\"", en: "keyword=\"{0}\"", ru: "слово=\"{0}\"" },
    "pagination.byStatus":{ zh: "状态=\"{0}\"", en: "status=\"{0}\"", ru: "статус=\"{0}\"" },
    "pagination.bySession":{ zh: "会话=\"{0}\"", en: "session=\"{0}\"", ru: "сессия=\"{0}\"" },

    /* ---- Search / Results Toast ---- */
    "search.resultToast": { zh: "搜索结果：找到 {0} 条记忆，当前显示第 {1} 条", en: "Search: {0} memories found, showing {1}", ru: "Поиск: найдено {0}, показано {1}" },

    /* ---- Delete ---- */
    "delete.confirmTitle":{ zh: "️  确认删除？", en: "️  Confirm Delete?", ru: "️  Подтвердить удаление?" },
    "delete.confirmMsg":  { zh: "即将删除 {0} 条记忆。\n此操作无法撤销！\n\n点击\"确定\"继续删除，点击\"取消\"保留。", en: "About to delete {0} memories.\nThis cannot be undone!\n\nClick OK to proceed, Cancel to keep them.", ru: "Будет удалено {0} записей.\nЭто необратимо!\n\nНажмите ОК для удаления, Отмена для сохранения." },
    "delete.cancelled":   { zh: "已取消删除操作", en: "Deletion cancelled", ru: "Удаление отменено" },
    "delete.deleting":    { zh: "删除中...", en: "Deleting...", ru: "Удаление..." },
    "delete.allFailed":   { zh: " 删除失败：全部 {0} 条记忆无法删除\n失败ID: {1}\n请检查日志了解详情", en: " Delete failed: all {0} memories could not be deleted\nFailed IDs: {1}", ru: " Ошибка: все {0} записей не удалены\nID: {1}" },
    "delete.partialFailed":{ zh: "️ 部分删除失败：成功 {0} 条，失败 {1} 条\n失败ID: {2}", en: "️ Partial failure: {0} succeeded, {1} failed\nFailed IDs: {2}", ru: "️ Частичная ошибка: {0} удалено, {1} не удалено\nID: {2}" },
    "delete.success":     { zh: " 已成功删除 {0} 条记忆", en: " Successfully deleted {0} memories", ru: " Удалено {0} записей" },
    "delete.successOne":  { zh: "已删除记忆 #{0}", en: "Deleted memory #{0}", ru: "Удалена память #{0}" },
    "delete.none":        { zh: "️ 没有删除任何记忆", en: "️ No memories were deleted", ru: "️ Ничего не удалено" },
    "delete.error":       { zh: "删除失败，请稍后重试", en: "Delete failed, please try again later", ru: "Ошибка удаления, попробуйте позже" },

    /* ---- Archive ---- */
    "archive.success":    { zh: "已归档 {0} 条记忆", en: "Archived {0} memories", ru: "Архивировано {0} записей" },
    "archive.fail":       { zh: "归档失败", en: "Archive failed", ru: "Ошибка архивации" },
    "archive.error":      { zh: "归档失败", en: "Archive failed", ru: "Ошибка архивации" },

    /* ---- Detail Drawer ---- */
    "detail.title":       { zh: "记忆详情", en: "Memory Detail", ru: "Детали памяти" },
    "detail.edit":        { zh: "编辑记忆", en: "Edit Memory", ru: "Редактировать" },
    "detail.close":       { zh: "关闭详情", en: "Close detail", ru: "Закрыть" },
    "detail.memoryId":    { zh: "记忆 ID", en: "Memory ID", ru: "ID памяти" },
    "detail.source":      { zh: "来源", en: "Source", ru: "Источник" },
    "detail.sourceCustom":{ zh: "自定义存储", en: "Custom Storage", ru: "Пользовательское" },
    "detail.sourceVector":{ zh: "向量存储", en: "Vector Storage", ru: "Векторное" },
    "detail.status":      { zh: "状态", en: "Status", ru: "Статус" },
    "detail.importance":  { zh: "重要性", en: "Importance", ru: "Важность" },
    "detail.type":        { zh: "类型", en: "Type", ru: "Тип" },
    "detail.created":     { zh: "创建时间", en: "Created", ru: "Создано" },
    "detail.lastAccess":  { zh: "最后访问", en: "Last Access", ru: "Доступ" },
    "detail.notFound":    { zh: "未找到对应的记录", en: "Record not found", ru: "Запись не найдена" },

    /* ---- Edit Modal ---- */
    "edit.title":         { zh: "编辑记忆", en: "Edit Memory", ru: "Редактировать память" },
    "edit.field":         { zh: "编辑字段", en: "Edit Field", ru: "Поле" },
    "edit.fieldContent":  { zh: "内容", en: "Content", ru: "Содержимое" },
    "edit.fieldImportance":{ zh: "重要性", en: "Importance", ru: "Важность" },
    "edit.fieldType":     { zh: "类型", en: "Type", ru: "Тип" },
    "edit.fieldStatus":   { zh: "状态", en: "Status", ru: "Статус" },
    "edit.newContent":    { zh: "新内容", en: "New Content", ru: "Новое содержимое" },
    "edit.newContentPh":  { zh: "输入新的记忆内容", en: "Enter new memory content", ru: "Введите новое содержимое" },
    "edit.newImportance": { zh: "新重要性 (0-10)", en: "New Importance (0-10)", ru: "Новая важность (0-10)" },
    "edit.importanceHint":{ zh: "重要性越高，记忆被召回的优先级越高", en: "Higher importance → higher recall priority", ru: "Выше важность → выше приоритет" },
    "edit.newType":       { zh: "新类型", en: "New Type", ru: "Новый тип" },
    "edit.typePh":        { zh: "如: FACT, EVENT, PREFERENCE", en: "e.g. FACT, EVENT, PREFERENCE", ru: "напр. FACT, EVENT, PREFERENCE" },
    "edit.typeHint":      { zh: "记忆类型用于分类管理", en: "Memory type is used for categorization", ru: "Тип памяти для категоризации" },
    "edit.newStatus":     { zh: "新状态", en: "New Status", ru: "Новый статус" },
    "edit.statusPh":      { zh: "活跃", en: "Active", ru: "Активно" },
    "edit.statusArchived":{ zh: "已归档", en: "Archived", ru: "Архив" },
    "edit.statusDeleted": { zh: "已删除", en: "Deleted", ru: "Удалено" },
    "edit.statusHint":    { zh: "已删除的记忆不会被召回", en: "Deleted memories won't be recalled", ru: "Удалённая память не извлекается" },
    "edit.reason":        { zh: "更新原因 (可选)", en: "Update Reason (optional)", ru: "Причина (опц.)" },
    "edit.reasonPh":      { zh: "说明本次更新的原因", en: "Explain the reason for this update", ru: "Укажите причину обновления" },
    "edit.noItem":        { zh: "未找到当前记忆信息", en: "Current memory info not found", ru: "Информация о памяти не найдена" },
    "edit.enterValue":    { zh: "请输入新值", en: "Please enter a new value", ru: "Введите новое значение" },
    "edit.updateFailed":  { zh: "更新失败", en: "Update failed", ru: "Ошибка обновления" },
    "edit.success":       { zh: "更新成功", en: "Update successful", ru: "Обновлено успешно" },

    /* ---- Status pills ---- */
    "status.active":      { zh: "活跃", en: "Active", ru: "Активно" },
    "status.archived":    { zh: "已归档", en: "Archived", ru: "Архив" },
    "status.deleted":     { zh: "已删除", en: "Deleted", ru: "Удалено" },

    /* ---- Type labels ---- */
    "type.general":       { zh: "通用", en: "General", ru: "Общее" },
    "type.fact":          { zh: "事实", en: "Fact", ru: "Факт" },
    "type.factual":       { zh: "事实", en: "Factual", ru: "Факт" },
    "type.preference":    { zh: "偏好", en: "Preference", ru: "Предпочтение" },
    "type.event":         { zh: "事件", en: "Event", ru: "Событие" },
    "type.episodic":      { zh: "事件", en: "Episodic", ru: "Эпизод" },
    "type.relational":    { zh: "关系", en: "Relational", ru: "Связь" },
    "type.planned":       { zh: "计划", en: "Planned", ru: "План" },
    "type.opinion":       { zh: "观点", en: "Opinion", ru: "Мнение" },

    /* ---- Graph Hero ---- */
    "graph.kicker":       { zh: "Graph Memory Explorer", en: "Graph Memory Explorer", ru: "Graph Memory Explorer" },
    "graph.title":        { zh: "知识图谱视图", en: "Knowledge Graph View", ru: "Граф знаний" },
    "graph.subtitle":     { zh: "从双路四模式召回结果中观察人物、主题、事实与记忆之间的连接。", en: "Explore connections between people, topics, facts, and memories from dual-route four-mode recall.", ru: "Исследуйте связи между людьми, темами, фактами и памятью из двухмаршрутного четырёхрежимного поиска." },

    /* ---- Graph Toolbar ---- */
    "graph.queryLabel":   { zh: "图谱查询", en: "Graph Query", ru: "Запрос графа" },
    "graph.queryPh":      { zh: "输入人物、主题、事实或整句，查看召回到的图谱子图", en: "Enter a person, topic, fact or sentence to view the recalled subgraph", ru: "Введите персону, тему, факт или фразу для просмотра подграфа" },
    "graph.sessionLabel": { zh: "会话过滤", en: "Session Filter", ru: "Фильтр сессии" },
    "graph.sessionPh":    { zh: "可选：限定 session_id", en: "Optional: limit to session_id", ru: "Опц.: ограничить session_id" },
    "graph.personaLabel": { zh: "人格过滤", en: "Persona Filter", ru: "Фильтр персоны" },
    "graph.personaPh":    { zh: "可选：限定 persona_id", en: "Optional: limit to persona_id", ru: "Опц.: ограничить persona_id" },
    "graph.memoryIdLabel":{ zh: "记忆 ID", en: "Memory ID", ru: "ID памяти" },
    "graph.memoryIdPh":   { zh: "输入记忆 ID 定位局部子图", en: "Enter memory ID to locate subgraph", ru: "Введите ID памяти для поиска подграфа" },
    "graph.searchBtn":    { zh: "检索图谱", en: "Search Graph", ru: "Искать в графе" },
    "graph.focusBtn":     { zh: "定位记忆", en: "Focus Memory", ru: "Фокус памяти" },
    "graph.overviewBtn":  { zh: "最近概览", en: "Recent Overview", ru: "Обзор" },

    /* ---- Graph Stats ---- */
    "graph.visibleNodes": { zh: "可视节点", en: "Visible Nodes", ru: "Видимых узлов" },
    "graph.nodes":        { zh: "节点", en: "Nodes", ru: "Узлы" },
    "graph.edges":        { zh: "关系", en: "Relations", ru: "Связи" },
    "graph.visibleEdges": { zh: "关系边", en: "Relation Edges", ru: "Связей" },
    "graph.visibleEntries":{ zh: "图谱条目", en: "Graph Entries", ru: "Записей графа" },
    "graph.routeLabel":   { zh: "检索视角", en: "Retrieval Route", ru: "Маршрут поиска" },
    "graph.visibleMemories":{ zh: "关联记忆", en: "Related Memories", ru: "Связанных памятей" },

    /* ---- Graph Panels ---- */
    "graph.canvasTitle":  { zh: "图谱画布", en: "Graph Canvas", ru: "Холст графа" },
    "graph.canvasSubtitle":{ zh: "点击节点、记忆卡片或召回结果即可切换焦点。", en: "Click nodes, memory cards or retrieval results to switch focus.", ru: "Нажмите узел, карточку памяти или результат поиска для смены фокуса." },
    "graph.focusDetail":  { zh: "焦点详情", en: "Focus Detail", ru: "Детали фокуса" },
    "graph.topNodes":     { zh: "核心节点", en: "Top Nodes", ru: "Ключевые узлы" },
    "graph.relatedMemories":{ zh: "相关记忆", en: "Related Memories", ru: "Связанная память" },
    "graph.retrievalPath":{ zh: "召回路径", en: "Retrieval Path", ru: "Путь поиска" },

    /* ---- Graph Status / Modes ---- */
    "graph.modeOverview": { zh: "最近概览", en: "Recent Overview", ru: "Обзор" },
    "graph.modeQuery":    { zh: "检索视图", en: "Retrieval View", ru: "Вид поиска" },
    "graph.modeFocus":    { zh: "记忆聚焦", en: "Memory Focus", ru: "Фокус памяти" },
    "graph.modeUnknown":  { zh: "图谱视图", en: "Graph View", ru: "Вид графа" },
    "graph.routeDual":    { zh: "文档 + 图 · 关键词 + 向量", en: "Doc + Graph · Keyword + Vector", ru: "Док + Граф · Ключ + Вектор" },
    "graph.routeBrowse":  { zh: "图谱浏览", en: "Graph Browse", ru: "Обзор графа" },
    "graph.statusDefault":{ zh: "展示图记忆中的核心连接。", en: "Showing core connections in graph memory.", ru: "Показаны основные связи в графе памяти." },
    "graph.statusQuery":  { zh: "当前展示 \"{0}\" 的双路四模式召回对应子图。", en: "Showing dual-route four-mode subgraph for \"{0}\".", ru: "Показан подграф для \"{0}\" (два маршрута, четыре режима)." },
    "graph.statusFocus":  { zh: "当前聚焦记忆 #{0} 的关系子图。", en: "Focused on relation subgraph of memory #{0}.", ru: "Фокус на подграфе связей памяти #{0}." },
    "graph.filterSession":{ zh: "会话 {0}", en: "Session {0}", ru: "Сессия {0}" },
    "graph.filterPersona":{ zh: "人格 {0}", en: "Persona {0}", ru: "Персона {0}" },
    "graph.filterPrefix": { zh: " 过滤条件：{0}", en: " Filter: {0}", ru: " Фильтр: {0}" },

    /* ---- Graph Node Types ---- */
    "graph.nodeTopic":    { zh: "主题", en: "Topic", ru: "Тема" },
    "graph.nodePerson":   { zh: "人物", en: "Person", ru: "Человек" },
    "graph.nodeFact":     { zh: "事实", en: "Fact", ru: "Факт" },
    "graph.nodeSummary":  { zh: "摘要", en: "Summary", ru: "Сводка" },
    "graph.nodeUnknown":  { zh: "节点", en: "Node", ru: "Узел" },

    /* ---- Graph Score Labels ---- */
    "graph.scoreDocKW":   { zh: "文档关键词", en: "Doc Keyword", ru: "Ключ. слова док." },
    "graph.scoreDocVec":  { zh: "文档向量", en: "Doc Vector", ru: "Вектор док." },
    "graph.scoreGraphKW": { zh: "图关键词", en: "Graph Keyword", ru: "Ключ. слова графа" },
    "graph.scoreGraphVec":{ zh: "图向量", en: "Graph Vector", ru: "Вектор графа" },

    /* ---- Graph Disabled ---- */
    "graph.disabledBadge":{ zh: "图记忆未启用", en: "Graph Disabled", ru: "Граф отключён" },
    "graph.disabledMsg":  { zh: "当前实例未启用图记忆功能，请先开启图记忆并完成索引。", en: "Graph memory is not enabled. Enable it and complete indexing first.", ru: "Граф памяти не включён. Включите его и завершите индексацию." },
    "graph.disabledRoute":{ zh: "未启用", en: "Disabled", ru: "Отключено" },
    "graph.disabledLegend":{ zh: "暂无图数据", en: "No graph data", ru: "Нет данных графа" },
    "graph.disabledMemories":{ zh: "暂无可展示的图记忆", en: "No graph memories to display", ru: "Нет граф-памятей для показа" },
    "graph.disabledRetrieval":{ zh: "点击\"最近概览\"加载图谱，或直接输入检索词。", en: "Click Recent Overview to load graph, or enter a search term.", ru: "Нажмите Обзор для загрузки графа или введите запрос." },
    "graph.disabledInspector":{ zh: "请选择节点或记忆查看详细信息。", en: "Select a node or memory to view details.", ru: "Выберите узел или память для просмотра." },
    "graph.disabledCanvas":{ zh: "当前实例尚未启用图记忆。", en: "Graph memory is not yet enabled.", ru: "Граф памяти ещё не включён." },

    /* ---- Graph Error ---- */
    "graph.errorBadge":   { zh: "图谱加载失败", en: "Graph Load Failed", ru: "Ошибка загрузки графа" },
    "graph.errorLegend":  { zh: "请求失败", en: "Request Failed", ru: "Ошибка запроса" },
    "graph.errorFetch":   { zh: "无法加载图谱概览", en: "Cannot load graph overview", ru: "Не удалось загрузить обзор графа" },

    /* ---- Graph Canvas Messages ---- */
    "graph.canvasDefault":{ zh: "点击\"最近概览\"加载图谱，或直接输入检索词。", en: "Click Recent Overview to load graph, or enter a search term.", ru: "Нажмите Обзор для загрузки графа или введите запрос." },
    "graph.canvasNo3D":   { zh: "3D 图谱组件未加载，请刷新页面并检查静态资源。", en: "3D graph component not loaded. Refresh and check static assets.", ru: "3D компонент графа не загружен. Обновите страницу." },
    "graph.canvasEmpty":  { zh: "当前范围内暂无可视化图数据。", en: "No visible graph data in the current range.", ru: "Нет видимых данных графа в текущем диапазоне." },
    "graph.canvasNoScene":{ zh: "当前页面未能加载 3D 图谱组件，请刷新页面后重试。", en: "Failed to load 3D graph component. Refresh and retry.", ru: "Не удалось загрузить 3D компонент. Обновите страницу." },

    /* ---- Graph Loading ---- */
    "graph.loadingOverview":{ zh: "正在加载最近图谱概览...", en: "Loading recent graph overview...", ru: "Загрузка обзора графа..." },
    "graph.loadingQuery": { zh: "正在检索\"{0}\"相关图谱...", en: "Retrieving graph for \"{0}\"...", ru: "Поиск графа для \"{0}\"..." },
    "graph.loadingFocus": { zh: "正在聚焦记忆 #{0} 的关系图...", en: "Focusing on relation graph of memory #{0}...", ru: "Фокус на графе связей памяти #{0}..." },
    "graph.loadingGeneric":{ zh: "图谱载入中...", en: "Loading graph...", ru: "Загрузка графа..." },

    /* ---- Graph Errors (actions) ---- */
    "graph.queryFail":    { zh: "图谱检索失败", en: "Graph retrieval failed", ru: "Ошибка поиска в графе" },
    "graph.focusEmpty":   { zh: "请输入要定位的记忆 ID。", en: "Please enter a memory ID to focus.", ru: "Введите ID памяти для фокуса." },
    "graph.focusNotInt":  { zh: "记忆 ID 必须是整数。", en: "Memory ID must be an integer.", ru: "ID памяти должен быть целым числом." },
    "graph.focusFail":    { zh: "定位记忆失败", en: "Memory focus failed", ru: "Ошибка фокуса памяти" },
    "graph.statsFailed":  { zh: "获取图谱统计失败", en: "Failed to fetch graph stats", ru: "Не удалось получить статистику графа" },

    /* ---- Graph Legend ---- */
    "graph.legendEmpty":  { zh: "暂无图谱连接", en: "No graph connections", ru: "Нет соединений в графе" },

    /* ---- Graph Panels Content ---- */
    "graph.noTopNodes":   { zh: "暂无核心节点", en: "No top nodes", ru: "Нет ключевых узлов" },
    "graph.noRelatedMemories":{ zh: "暂无关联记忆", en: "No related memories", ru: "Нет связанной памяти" },
    "graph.noRetrieval":  { zh: "执行检索后，这里会展示文档 / 图 × 关键词 / 向量的召回细节。", en: "After retrieval, doc/graph × keyword/vector recall details appear here.", ru: "После поиска здесь появятся детали поиска док/граф × ключ/вектор." },
    "graph.noInspector":  { zh: "点击节点、记忆卡片或召回结果查看详细信息。", en: "Click a node, memory card or retrieval result to view details.", ru: "Нажмите узел, карточку памяти или результат поиска для деталей." },
    "graph.unnamedNode":  { zh: "未命名节点", en: "Unnamed Node", ru: "Безымянный узел" },
    "graph.noSummary":    { zh: "无摘要", en: "No summary", ru: "Нет сводки" },
    "graph.focusThisMemory":{ zh: "聚焦此记忆", en: "Focus This Memory", ru: "Фокус на память" },
    "graph.noSession":    { zh: "未设置会话", en: "No session set", ru: "Сессия не задана" },

    /* ---- Graph Inspector ---- */
    "graph.inspectorMemoryCount":{ zh: "关联记忆", en: "Related", ru: "Связано" },
    "graph.inspectorDegree":{ zh: "连接度", en: "Degree", ru: "Степень" },
    "graph.inspectorEntryCount":{ zh: "命中条目", en: "Hit Entries", ru: "Записей" },
    "graph.inspectorWeight":{ zh: "权重", en: "Weight", ru: "Вес" },
    "graph.inspectorRelatedMemories":{ zh: "相关记忆", en: "Related Memories", ru: "Связанная память" },
    "graph.inspectorNoRelatedMemories":{ zh: "暂无相关记忆", en: "No related memories", ru: "Нет связанной памяти" },
    "graph.inspectorRelatedEntries":{ zh: "相关条目", en: "Related Entries", ru: "Связанные записи" },
    "graph.inspectorNoRelatedEntries":{ zh: "暂无相关条目", en: "No related entries", ru: "Нет связанных записей" },
    "graph.inspectorNodeDist":{ zh: "节点分布", en: "Node Distribution", ru: "Распределение узлов" },
    "graph.inspectorNoNodes":{ zh: "暂无节点", en: "No nodes", ru: "Нет узлов" },
    "graph.inspectorGraphEntries":{ zh: "图谱条目", en: "Graph Entries", ru: "Записи графа" },
    "graph.inspectorNoGraphEntries":{ zh: "暂无图谱条目", en: "No graph entries", ru: "Нет записей графа" },
    "graph.inspectorNodeCount":{ zh: "节点", en: "Nodes", ru: "Узлы" },
    "graph.inspectorEntryCount2":{ zh: "条目", en: "Entries", ru: "Записи" },
    "graph.inspectorRelationCount":{ zh: "关系", en: "Relations", ru: "Связи" },
    "graph.inspectorImportance":{ zh: "重要性", en: "Importance", ru: "Важность" },
    "graph.inspectorMemory":{ zh: "记忆", en: "Memory", ru: "Память" },

    /* ---- Graph Tooltip ---- */
    "graph.tooltipMemory": { zh: "记忆 {0} · 关系 {1} · 条目 {2}", en: "Memory {0} · Rel {1} · Entries {2}", ru: "Память {0} · Связ {1} · Запис {2}" },

    /* ---- Graph Bridge Error ---- */
    "graph.bridgeError":  { zh: "当前页面必须运行在 AstrBot 官方插件 Page 内。", en: "This page must run inside an AstrBot plugin page.", ru: "Страница должна работать внутри страницы плагина AstrBot." },

    /* ---- Recall Test ---- */
    "recall.clearBtn":    { zh: "清空结果", en: "Clear Results", ru: "Очистить" },
    "recall.title":       { zh: "记忆召回功能测试", en: "Memory Recall Test", ru: "Тест поиска памяти" },
    "recall.subtitle":    { zh: "输入查询语句，测试混合检索引擎的召回能力", en: "Enter a query to test the hybrid retrieval engine", ru: "Введите запрос для теста гибридного поиска" },
    "recall.queryLabel":  { zh: "查询内容", en: "Query", ru: "Запрос" },
    "recall.queryPh":     { zh: "输入你的查询语句，系统将使用混合检索（BM25+向量相似度）进行召回", en: "Enter your query. The system uses hybrid retrieval (BM25 + vector similarity).", ru: "Введите запрос. Система использует гибридный поиск (BM25 + векторы)." },
    "recall.countLabel":  { zh: "返回数量", en: "Result Count", ru: "Кол-во результатов" },
    "recall.kLabel":      { zh: "结果数 (k)", en: "Results (k)", ru: "Результаты (k)" },
    "recall.countPh":     { zh: "返回的记忆数量", en: "Number of memories to return", ru: "Количество возвращаемых памятей" },
    "recall.sessionLabel":{ zh: "会话 ID (可选)", en: "Session ID (optional)", ru: "ID сессии (опц.)" },
    "recall.sessionPh":   { zh: "输入会话 ID 以过滤特定会话的记忆（支持多种格式）", en: "Enter session ID to filter memories (supports multiple formats)", ru: "Введите ID сессии для фильтрации (разные форматы)" },
    "recall.searchBtn":   { zh: "执行召回", en: "Run Recall", ru: "Запустить поиск" },
    "recall.resultTitle": { zh: "召回结果", en: "Recall Results", ru: "Результаты поиска" },
    "recall.resultCount": { zh: "召回数量", en: "Recall Count", ru: "Найдено" },
    "recall.resultsCount":{ zh: "{0} 条结果", en: "{0} results", ru: "{0} результатов" },
    "recall.time":        { zh: "查询耗时", en: "Query Time", ru: "Время запроса" },
    "recall.empty":       { zh: "暂无召回结果 · 请输入查询内容并执行召回", en: "No results · Enter a query and run recall", ru: "Нет результатов · Введите запрос и запустите поиск" },
    "recall.noMatch":     { zh: "未找到匹配的记忆", en: "No matching memories found", ru: "Совпадений не найдено" },
    "recall.noResults":   { zh: "未找到匹配的记忆", en: "No matching memories found", ru: "Совпадений не найдено" },
    "recall.enterQuery":  { zh: "请输入查询内容", en: "Please enter a query", ru: "Введите запрос" },
    "recall.queryRequired":{ zh: "请输入查询内容", en: "Please enter a query", ru: "Введите запрос" },
    "recall.searching":   { zh: "执行中...", en: "Running...", ru: "Поиск..." },
    "recall.successToast":{ zh: "成功召回 {0} 条记忆", en: "Recalled {0} memories", ru: "Найдено {0} памятей" },
    "recall.fail":        { zh: "召回失败", en: "Recall failed", ru: "Ошибка поиска" },
    "recall.testFailed":  { zh: "召回测试失败", en: "Recall test failed", ru: "Ошибка теста поиска" },
    "recall.timeElapsed": { zh: "耗时 {0} 秒", en: "{0}s elapsed", ru: "Затрачено {0} с" },

    /* ---- Recall Results Metadata ---- */
    "recall.resultId":    { zh: "记忆 ID:", en: "Memory ID:", ru: "ID памяти:" },
    "recall.resultScore": { zh: "相似度得分:", en: "Similarity Score:", ru: "Оценка схожести:" },
    "recall.resultSession":{ zh: "会话 UUID:", en: "Session UUID:", ru: "UUID сессии:" },
    "recall.resultImportance":{ zh: "重要性:", en: "Importance:", ru: "Важность:" },
    "recall.resultType":  { zh: "类型:", en: "Type:", ru: "Тип:" },
    "recall.resultStatus":{ zh: "状态:", en: "Status:", ru: "Статус:" },

    /* ---- Theme ---- */
    "theme.darkToast":    { zh: "🌙 已切换到深色模式", en: "🌙 Dark mode enabled", ru: "🌙 Тёмная тема включена" },
    "theme.lightToast":   { zh: "☀️ 已切换到浅色模式", en: "☀️ Light mode enabled", ru: "☀️ Светлая тема включена" },

    /* ---- Bridge Error ---- */
    "bridge.error":       { zh: "当前页面必须运行在 AstrBot 官方插件 Page 内", en: "This page must run inside an AstrBot plugin page", ru: "Страница должна работать внутри страницы плагина AstrBot" },

    /* ---- Misc ---- */
    "misc.requestFailed": { zh: "请求失败", en: "Request failed", ru: "Ошибка запроса" },
    "misc.initFail":      { zh: "初始化加载失败", en: "Initialization failed", ru: "Ошибка инициализации" },
    "misc.statsFail":     { zh: "获取统计信息失败", en: "Failed to fetch stats", ru: "Не удалось получить статистику" },
    "misc.statsUnavailable":{ zh: "无法获取统计信息", en: "Stats unavailable", ru: "Статистика недоступна" },
    "misc.fetchMemoriesFail":{ zh: "获取记忆失败", en: "Failed to fetch memories", ru: "Не удалось загрузить память" },
    "misc.loadFail":      { zh: "加载失败", en: "Load failed", ru: "Ошибка загрузки" },
    "misc.systemFail":    { zh: "系统概览加载失败", en: "Failed to load system overview", ru: "Не удалось загрузить обзор системы" },

    /* ---- System ---- */
    "system.importanceDistribution":{ zh: "重要性分布", en: "Importance Distribution", ru: "Распределение важности" },
    "system.atomTypes":   { zh: "原子类型", en: "Atom Types", ru: "Типы атомов" },
    "system.activeSessions":{ zh: "活跃会话", en: "Active Sessions", ru: "Активные сессии" },
    "system.versionBackups":{ zh: "版本备份", en: "Version Backups", ru: "Резервные копии" },
    "system.noActiveSessions":{ zh: "暂无活跃会话", en: "No active sessions", ru: "Нет активных сессий" },
    "system.noSessions":  { zh: "暂无会话", en: "No sessions", ru: "Нет сессий" },
    "system.noBackups":   { zh: "暂无备份", en: "No backups", ru: "Нет резервных копий" },
    "system.noAtoms":     { zh: "暂无原子数据", en: "No atom data", ru: "Нет данных атомов" },
    "system.files":       { zh: "个文件", en: "files", ru: "файлов" },
    "system.messages":    { zh: "条消息", en: "messages", ru: "сообщений" },
    "system.lastActive":  { zh: "最后活跃", en: "Last active", ru: "Посл. активность" },
    "system.fetchFailed": { zh: "获取系统数据失败", en: "Failed to fetch system data", ru: "Не удалось получить данные системы" },
    "system.atomFactual": { zh: "事实", en: "Factual", ru: "Фактическая" },
    "system.atomEpisodic":{ zh: "事件", en: "Episodic", ru: "Эпизодическая" },
    "system.atomPreference":{ zh: "偏好", en: "Preference", ru: "Предпочтения" },
    "system.atomRelational":{ zh: "关系", en: "Relational", ru: "Связи" },
    "system.atomPlanned": { zh: "计划", en: "Planned", ru: "Планы" },

    /* ---- Atom labels ---- */
    "atom.entity":        { zh: "实体", en: "Entity", ru: "Сущность" },
    "atom.event":         { zh: "事件", en: "Event", ru: "Событие" },
    "atom.preference":    { zh: "偏好", en: "Preference", ru: "Предпочтение" },
    "atom.topic":         { zh: "主题", en: "Topic", ru: "Тема" },

    /* ---- Memory Detail ---- */
    "detail.viewTitle":   { zh: "记忆详情", en: "Memory Detail", ru: "Детали памяти" },
    "detail.editTitle":   { zh: "编辑记忆", en: "Edit Memory", ru: "Редактировать память" },
    "detail.content":     { zh: "内容", en: "Content", ru: "Содержимое" },
    "detail.metadata":    { zh: "元数据", en: "Metadata", ru: "Метаданные" },
    "detail.graphContext":{ zh: "知识图谱关联", en: "Knowledge Graph Context", ru: "Контекст графа знаний" },
    "detail.keyFacts":    { zh: "关键事实", en: "Key Facts", ru: "Ключевые факты" },
    "detail.topics":      { zh: "主题", en: "Topics", ru: "Темы" },
    "detail.editHistory": { zh: "编辑历史", en: "Edit History", ru: "История изменений" },
    "detail.editBtn":     { zh: "编辑", en: "Edit", ru: "Редактировать" },
    "detail.deleteBtn":   { zh: "删除", en: "Delete", ru: "Удалить" },
    "detail.saveBtn":     { zh: "保存修改", en: "Save Changes", ru: "Сохранить" },
    "detail.cancelBtn":   { zh: "取消", en: "Cancel", ru: "Отмена" },
    "detail.memoryTitle": { zh: "记忆 #{0}", en: "Memory #{0}", ru: "Память #{0}" },
    "detail.editingTitle":{ zh: "正在编辑记忆 #{0}", en: "Editing Memory #{0}", ru: "Редактирование памяти #{0}" },
    "detail.sessionId":   { zh: "会话 ID", en: "Session ID", ru: "ID сессии" },
    "detail.personaId":   { zh: "人格 ID", en: "Persona ID", ru: "ID персоны" },
    "detail.updated":     { zh: "更新时间", en: "Updated", ru: "Обновлено" },
    "detail.updateReason":{ zh: "更新原因（可选）", en: "Update Reason (optional)", ru: "Причина обновления (опц.)" },
    "detail.reasonPh":    { zh: "说明本次更新的原因", en: "Why this update?", ru: "Причина обновления" },
    "detail.contentHint": { zh: "编辑内容将创建新记忆（ID会变更）", en: "Editing content creates a new memory (ID will change).", ru: "Изменение создаст новую память (ID изменится)." },
    "detail.noGraphData": { zh: "暂无图谱数据", en: "No graph data", ru: "Нет данных графа" },
    "detail.noChanges":   { zh: "没有检测到修改", en: "No changes", ru: "Нет изменений" },
    "detail.contentRequired":{ zh: "记忆内容不能为空", en: "Memory content cannot be empty", ru: "Содержимое памяти не может быть пустым" },
    "detail.contentUpdated":{ zh: "内容已更新（新 ID：{0}）", en: "Content updated (new ID: {0})", ru: "Содержимое обновлено (новый ID: {0})" },
    "detail.statusUpdated":{ zh: "状态 → {0}", en: "Status → {0}", ru: "Статус → {0}" },
    "detail.typeUpdated": { zh: "类型 → {0}", en: "Type → {0}", ru: "Тип → {0}" },
    "detail.importanceUpdated":{ zh: "重要性 → {0}", en: "Importance → {0}", ru: "Важность → {0}" },
    "detail.nodeMemories":{ zh: "关联记忆", en: "Memories", ru: "Память" },
    "detail.nodeDegree":  { zh: "连接度", en: "Degree", ru: "Степень" },
    "detail.nodeEntries": { zh: "条目", en: "Entries", ru: "Записи" },
    "detail.nodeWeight":  { zh: "权重", en: "Weight", ru: "Вес" },

    /* ---- Confirm dialog ---- */
    "confirm.deleteTitle":{ zh: "确认删除？", en: "Confirm delete?", ru: "Подтвердить удаление?" },
    "confirm.deleteMessage":{ zh: "即将删除记忆 #{0}。此操作无法撤销。", en: "Memory #{0} will be deleted. This cannot be undone.", ru: "Память #{0} будет удалена. Это необратимо." },
    "memory.deleted":     { zh: "记忆已删除", en: "Memory deleted", ru: "Память удалена" },
    "memory.deleteFailed":{ zh: "删除记忆失败", en: "Failed to delete memory", ru: "Не удалось удалить память" },

    /* ---- Graph 2D ---- */
    "graph2d.noData":     { zh: "暂无图谱数据", en: "No graph data available", ru: "Нет данных графа" },
    "graph2d.loading":    { zh: "加载图谱中...", en: "Loading graph...", ru: "Загрузка графа..." },
    "graph2d.moduleFail": { zh: "2D 图谱模块未加载，请刷新页面重试。", en: "2D graph module not loaded. Refresh and retry.", ru: "2D модуль графа не загружен. Обновите страницу." },
  };

  /* ---- Engine ---- */
  let currentLang = "zh";

  function getBridgeLocale() {
    try {
      const bridge = window.AstrBotPluginPage;
      if (bridge) {
        const ctx = bridge.getContext();
        if (ctx && ctx.locale) {
          const lang = String(ctx.locale).split("-")[0];
          if (SUPPORTED.includes(lang)) return lang;
        }
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  function detectLanguage() {
    try {
      const params = new URLSearchParams(window.location.search);
      const langParam = params.get("lang");
      if (langParam && SUPPORTED.includes(langParam)) {
        urlLanguageOverride = true;
        return langParam;
      }
    } catch (_) { /* ignore */ }

    try {
      const stored = localStorage.getItem(LANG_KEY);
      if (stored && SUPPORTED.includes(stored)) return stored;
    } catch (_) { /* ignore */ }

    const bridgeLocale = getBridgeLocale();
    if (bridgeLocale) return bridgeLocale;

    try {
      const nav = (navigator.language || "").split("-")[0];
      if (SUPPORTED.includes(nav)) return nav;
    } catch (_) { /* ignore */ }

    return "zh";
  }

  function listenBridgeLocale() {
    try {
      const bridge = window.AstrBotPluginPage;
      if (!bridge || typeof bridge.onContext !== "function") return;
      bridge.onContext(function (ctx) {
        if (!ctx || !ctx.locale) return;
        const lang = String(ctx.locale).split("-")[0];
        let hasLocalOverride = false;
        try {
          hasLocalOverride = SUPPORTED.includes(localStorage.getItem(LANG_KEY));
        } catch (_) { /* ignore */ }
        if (!urlLanguageOverride && !hasLocalOverride && SUPPORTED.includes(lang) && lang !== currentLang) {
          window.setLanguage(lang, { persist: false, source: "bridge" });
        }
      });
    } catch (_) { /* ignore */ }
  }

  /**
   * @param {string} key
   * @param {...(string|number)} args - positional replacements for {0}, {1}, ...
   */
  window.t = function (key, ...args) {
    const entry = MSG[key];
    let template = entry ? (entry[currentLang] || entry.zh || key) : key;
    args.forEach((arg, i) => {
      template = template.replace(new RegExp("\\{" + i + "\\}", "g"), String(arg ?? ""));
    });
    return template;
  };

  window.setLanguage = function (lang, options = {}) {
    if (!SUPPORTED.includes(lang)) return;
    currentLang = lang;
    if (options.persist !== false) {
      try { localStorage.setItem(LANG_KEY, lang); } catch (_) { /* ignore */ }
    }
    document.documentElement.setAttribute("lang", lang === "zh" ? "zh-CN" : lang === "ru" ? "ru" : "en");
    applyI18n();
    window.dispatchEvent(new CustomEvent("languagechange", { detail: { lang, source: options.source || "local" } }));
  };

  window.getLanguage = function () {
    return currentLang;
  };

  function applyI18n() {
    // data-i18n → textContent
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = window.t(el.getAttribute("data-i18n"));
    });
    // data-i18n-placeholder → placeholder
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.setAttribute("placeholder", window.t(el.getAttribute("data-i18n-placeholder")));
    });
    // data-i18n-title → title
    document.querySelectorAll("[data-i18n-title]").forEach((el) => {
      el.setAttribute("title", window.t(el.getAttribute("data-i18n-title")));
    });
    // data-i18n-aria → aria-label
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      el.setAttribute("aria-label", window.t(el.getAttribute("data-i18n-aria")));
    });
  }

  // bootstrap
  currentLang = detectLanguage();
  document.documentElement.setAttribute("lang", currentLang === "zh" ? "zh-CN" : currentLang === "ru" ? "ru" : "en");
  document.addEventListener("DOMContentLoaded", () => {
    applyI18n();
    listenBridgeLocale();
  });
})();
