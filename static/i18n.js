/**
 * LivingMemory WebUI i18n module
 * Supports: zh (中文), en (English)
 * Future: ru (Русский)
 */

const TRANSLATIONS = {
  zh: {
    // Meta
    "meta.title": "LivingMemory 控制台",

    // Login
    "login.title": "LivingMemory",
    "login.subtitle": "智能记忆管理系统",
    "login.username_label": "用户名",
    "login.password_label": "访问密码",
    "login.password_placeholder": "请输入访问密码",
    "login.submit": "登录",
    "login.error_empty": "请输入密码",
    "login.error_failed": "登录失败，请重试",

    // Header
    "header.title": "LivingMemory 管理面板",
    "header.subtitle": "长期记忆与会话管理 · 基于混合检索的智能记忆系统",
    "header.logout": "退出登录",
    "header.theme_tooltip": "切换主题",
    "header.lang_tooltip": "切换语言",

    // Tabs
    "tab.memories": "记忆管理",
    "tab.graph": "知识图谱",
    "tab.recall": "召回测试",

    // Toolbar - Memories
    "toolbar.refresh": "刷新",
    "toolbar.nuke": "核爆清除",
    "toolbar.nuke_cancel": "取消核爆",
    "toolbar.keyword_placeholder": "关键字（支持 memory_id / 内容搜索）",
    "toolbar.status_all": "全部状态",
    "toolbar.status_active": "活跃",
    "toolbar.status_archived": "已归档",
    "toolbar.status_deleted": "已删除",
    "toolbar.filter": "筛选",
    "toolbar.select_all": "全选",
    "toolbar.delete_selected": "删除选中",
    "toolbar.deleting": "删除中...",

    // Stats
    "stat.total": "总记忆",
    "stat.active": "活跃",
    "stat.archived": "已归档",
    "stat.deleted": "已删除",
    "stat.sessions": "活跃会话",

    // Table
    "table.col_id": "记忆 ID",
    "table.col_summary": "摘要",
    "table.col_type": "类型",
    "table.col_importance": "重要性",
    "table.col_status": "状态",
    "table.col_created": "创建时间",
    "table.col_accessed": "最后访问",
    "table.col_action": "操作",
    "table.empty": "暂无数据",
    "table.no_summary": "（无摘要）",
    "table.no_content": "（无内容）",
    "table.detail": "详情",

    // Status pills
    "status.active": "活跃",
    "status.archived": "已归档",
    "status.deleted": "已删除",

    // Pagination
    "page.prev": "上一页",
    "page.next": "下一页",
    "page.per_page": "每页",
    "page.info": "第 {page} / {total} 页 · 共 {count} 条",
    "page.all_loaded": "共 {count} 条记录（已加载全部）",
    "page.filtering": "筛选中",
    "page.keyword": "关键词",
    "page.status": "状态",

    // Detail Drawer
    "detail.title": "记忆详情",
    "detail.edit": "编辑记忆",
    "detail.source": "来源",
    "detail.source_storage": "自定义存储",
    "detail.source_vector": "向量存储",
    "detail.status": "状态",
    "detail.importance": "重要性",
    "detail.type": "类型",
    "detail.created": "创建时间",
    "detail.accessed": "最后访问",

    // Edit Modal
    "modal.title": "编辑记忆",
    "modal.field": "编辑字段",
    "modal.field_content": "内容",
    "modal.field_importance": "重要性",
    "modal.field_type": "类型",
    "modal.field_status": "状态",
    "modal.content_label": "新内容",
    "modal.content_placeholder": "输入新的记忆内容",
    "modal.importance_label": "新重要性 (0-10)",
    "modal.importance_hint": "重要性越高，记忆被召回的优先级越高",
    "modal.type_label": "新类型",
    "modal.type_placeholder": "如: FACT, EVENT, PREFERENCE",
    "modal.type_hint": "记忆类型用于分类管理",
    "modal.status_active": "活跃",
    "modal.status_archived": "已归档",
    "modal.status_deleted": "已删除",
    "modal.status_hint": "已删除的记忆不会被召回",
    "modal.reason_label": "更新原因 (可选)",
    "modal.reason_placeholder": "说明本次更新的原因",
    "modal.cancel": "取消",
    "modal.save": "保存",

    // Graph
    "graph.kicker": "Graph Memory Explorer",
    "graph.title": "知识图谱视图",
    "graph.subtitle": "从双路四模式召回结果中观察人物、主题、事实与记忆之间的连接。",
    "graph.mode_recent": "最近概览",
    "graph.status_line": "展示最近活跃的图记忆子图",
    "graph.query_label": "图谱查询",
    "graph.query_placeholder": "输入人物、主题、事实或整句，查看召回到的图谱子图",
    "graph.session_filter": "会话过滤",
    "graph.session_placeholder": "可选：限定 session_id",
    "graph.persona_filter": "人格过滤",
    "graph.persona_placeholder": "可选：限定 persona_id",
    "graph.memory_id": "记忆 ID",
    "graph.memory_id_placeholder": "输入记忆 ID 定位局部子图",
    "graph.search": "检索图谱",
    "graph.focus": "定位记忆",
    "graph.overview": "最近概览",
    "graph.visible_nodes": "可视节点",
    "graph.edges": "关系边",
    "graph.entries": "图谱条目",
    "graph.perspective": "检索视角",
    "graph.perspective_label": "图谱浏览",
    "graph.related_memories": "关联记忆",
    "graph.canvas_title": "图谱画布",
    "graph.canvas_subtitle": "点击节点、记忆卡片或召回结果即可切换焦点。",
    "graph.focus_detail": "焦点详情",
    "graph.core_nodes": "核心节点",
    "graph.recall_path": "召回路径",
    "graph.node_topic": "主题",
    "graph.node_person": "人物",
    "graph.node_fact": "事实",
    "graph.node_summary": "摘要",
    "graph.mode_overview": "最近概览",
    "graph.mode_query": "检索视图",
    "graph.mode_memory_focus": "记忆聚焦",
    "graph.score_doc_kw": "文档关键词",
    "graph.score_doc_vec": "文档向量",
    "graph.score_graph_kw": "图关键词",
    "graph.score_graph_vec": "图向量",
    "graph.canvas_hint": "点击\"最近概览\"加载图谱，或直接输入检索词。",
    "graph.canvas_no_3d": "3D 图谱组件未加载，请刷新页面并检查静态资源。",
    "graph.not_logged_in": "当前会话未登录，请先登录 WebUI。",
    "graph.server_format_error": "服务器响应格式错误",
    "graph.request_failed": "图谱请求失败",
    "graph.loading_overview": "正在加载最近图谱概览...",
    "graph.load_failed": "无法加载图谱概览",
    "graph.searching_query": "正在检索\"{query}\"相关图谱...",
    "graph.search_failed": "图谱检索失败",
    "graph.enter_memory_id": "请输入要定位的记忆 ID。",
    "graph.memory_id_integer": "记忆 ID 必须是整数。",
    "graph.focusing_memory": "正在聚焦记忆 #{memoryId} 的关系图...",
    "graph.focus_failed": "定位记忆失败",
    "graph.loading": "图谱载入中...",
    "graph.disabled_title": "图记忆未启用",
    "graph.disabled_message": "当前实例未启用图记忆功能，请先开启图记忆并完成索引。",
    "graph.disabled_route": "未启用",
    "graph.no_data": "暂无图数据",
    "graph.no_memories": "暂无可展示的图记忆",
    "graph.select_node_hint": "请选择节点或记忆查看详细信息。",
    "graph.not_enabled": "当前实例尚未启用图记忆。",
    "graph.load_failed_title": "图谱加载失败",
    "graph.request_failed_chip": "请求失败",
    "graph.no_data_panel": "暂无数据",
    "graph.session_filter_label": "会话 {sessionId}",
    "graph.persona_filter_label": "人格 {personaId}",
    "graph.core_connections": "展示图记忆中的核心连接。",
    "graph.query_subgraph": "当前展示 \"{query}\" 的双路四模式召回对应子图。",
    "graph.memory_subgraph": "当前聚焦记忆 #{memoryId} 的关系子图。",
    "graph.filter_conditions": "过滤条件：{conditions}",
    "graph.route_doc_graph": "文档 + 图 · 关键词 + 向量",
    "graph.route_browse": "图谱浏览",
    "graph.no_connections": "暂无图谱连接",
    "graph.no_visible_data": "当前范围内暂无可视化图数据。",
    "graph.no_3d_reload": "当前页面未能加载 3D 图谱组件，请刷新页面后重试。",
    "graph.no_core_nodes": "暂无核心节点",
    "graph.unnamed_node": "未命名节点",
    "graph.degree": "度 {degree}",
    "graph.no_related_memories": "暂无关联记忆",
    "graph.no_summary": "无摘要",
    "graph.node_count_label": "节点 {count}",
    "graph.entry_count_label": "条目 {count}",
    "graph.edge_count_label": "关系 {count}",
    "graph.focus_memory_btn": "聚焦此记忆",
    "graph.retrieval_hint": "执行检索后，这里会展示文档 / 图 × 关键词 / 向量的召回细节。",
    "graph.memory_header": "记忆 #{memoryId}",
    "graph.select_node_inspector": "点击节点、记忆卡片或召回结果查看详细信息。",
    "graph.related_memories_title": "相关记忆",
    "graph.related_entries_title": "相关条目",
    "graph.no_related_memories_panel": "暂无相关记忆",
    "graph.no_related_entries_panel": "暂无相关条目",
    "graph.inspector_memory_label": "记忆 #{memoryId}",
    "graph.inspector_related_memories": "相关记忆",
    "graph.inspector_memory_count": "关联记忆",
    "graph.inspector_degree": "连接度",
    "graph.inspector_entries": "命中条目",
    "graph.inspector_weight": "权重",
    "graph.node_distribution": "节点分布",
    "graph.no_nodes": "暂无节点",
    "graph.graph_entries_title": "图谱条目",
    "graph.no_graph_entries": "暂无图谱条目",
    "graph.edge_tooltip": "{relation} · 记忆 #{memoryId}",
    "graph.graph_tooltip_meta": "记忆 {memoryCount} · 关系 {degree} · 条目 {entryCount}",

    // Recall Test
    "recall.clear": "清空结果",
    "recall.panel_title": "记忆召回功能测试",
    "recall.panel_subtitle": "输入查询语句，测试混合检索引擎的召回能力",
    "recall.query_label": "查询内容",
    "recall.query_placeholder": "输入你的查询语句，系统将使用混合检索（BM25+向量相似度）进行召回",
    "recall.k_label": "返回数量",
    "recall.session_label": "会话 ID (可选)",
    "recall.session_placeholder": "输入会话 ID 以过滤特定会话的记忆（支持多种格式）",
    "recall.search": "执行召回",
    "recall.searching": "执行中...",
    "recall.results_title": "召回结果",
    "recall.result_count": "召回数量",
    "recall.result_time": "查询耗时",
    "recall.empty": "暂无召回结果 · 请输入查询内容并执行召回",
    "recall.no_match": "未找到匹配的记忆",
    "recall.result_header": "结果 #{number}",
    "recall.result_memory_id": "记忆 ID",
    "recall.result_similarity": "相似度得分",
    "recall.result_session": "会话 UUID",
    "recall.result_importance": "重要性",
    "recall.result_type": "类型",
    "recall.result_status": "状态",

    // Nuke
    "nuke.message": "所有记忆将在 {seconds} 秒后被抹除。立即取消以中止核爆！",
    "nuke.message_zero": "正在抹除所有记忆... 请保持窗口打开。",
    "nuke.cancel": "取消核爆",
    "nuke.done": "核爆完成！所有记忆已从界面移除。",
    "nuke.table_empty": "核爆完成！所有记忆已被抹除。点击「刷新」重新加载。",

    // Toasts / Messages
    "toast.login_success": "登录成功，正在加载数据...",
    "toast.session_restored": "会话已恢复，正在验证...",
    "toast.verify_success": "验证成功，正在加载数据...",
    "toast.logout": "已退出登录",
    "toast.nuke_start": "核爆倒计时启动！",
    "toast.nuke_cancel": "核爆已取消！记忆保留",
    "toast.nuke_done": "核爆完成！所有记忆已从界面移除（仅视觉效果）",
    "toast.delete_success": "已成功删除 {count} 条记忆",
    "toast.delete_partial": "部分删除失败：成功 {success} 条，失败 {failed} 条",
    "toast.delete_failed": "删除失败",
    "toast.fetch_stats_failed": "获取统计信息失败",
    "toast.fetch_memories_failed": "获取记忆失败",
    "toast.record_not_found": "未找到对应的记录",
    "toast.memory_not_found": "未找到当前记忆信息",
    "toast.enter_new_value": "请输入新值",
    "toast.recall_failed": "召回失败",
    "toast.delete_confirm_title": "确认删除？",
    "toast.delete_confirm_body": "即将删除 {count} 条记忆。",
    "toast.delete_confirm_irreversible": "此操作无法撤销！",
    "toast.delete_confirm_action": '点击"确定"继续删除，点击"取消"保留。',
    "toast.delete_cancelled": "已取消删除操作",
    "toast.delete_failed_all": "删除失败：全部 {count} 条记忆无法删除",
    "toast.delete_none": "没有删除任何记忆",
    "toast.update_success": "更新成功",
    "toast.update_failed": "更新失败",
    "toast.recall_success": "成功召回 {count} 条记忆",
    "toast.search_results": "搜索结果：找到 {total} 条记忆，当前显示第 {shown} 条",
    "toast.no_results": "未找到相关记忆",
    "toast.error": "操作失败",
    "toast.not_logged_in": "尚未登录",
    "toast.session_expired": "会话已过期，请重新登录",
    "toast.server_format_error": "服务器返回格式错误",
    "toast.request_failed": "请求失败",
    "toast.theme_dark": "🌙 已切换到深色模式",
    "toast.theme_light": "☀️ 已切换到浅色模式",
  },

  en: {
    // Meta
    "meta.title": "LivingMemory Console",

    // Login
    "login.title": "LivingMemory",
    "login.subtitle": "Intelligent Memory Management System",
    "login.username_label": "Username",
    "login.password_label": "Access Password",
    "login.password_placeholder": "Enter access password",
    "login.submit": "Login",
    "login.error_empty": "Please enter password",
    "login.error_failed": "Login failed, please try again",

    // Header
    "header.title": "LivingMemory Admin Panel",
    "header.subtitle": "Long-term Memory & Session Management · Hybrid Retrieval Intelligent System",
    "header.logout": "Logout",
    "header.theme_tooltip": "Toggle Theme",
    "header.lang_tooltip": "Switch Language",

    // Tabs
    "tab.memories": "Memory Management",
    "tab.graph": "Knowledge Graph",
    "tab.recall": "Recall Test",

    // Toolbar - Memories
    "toolbar.refresh": "Refresh",
    "toolbar.nuke": "NUKE Clear",
    "toolbar.nuke_cancel": "Cancel NUKE",
    "toolbar.keyword_placeholder": "Keywords (memory_id / content search)",
    "toolbar.status_all": "All Statuses",
    "toolbar.status_active": "Active",
    "toolbar.status_archived": "Archived",
    "toolbar.status_deleted": "Deleted",
    "toolbar.filter": "Filter",
    "toolbar.select_all": "Select All",
    "toolbar.delete_selected": "Delete Selected",
    "toolbar.deleting": "Deleting...",

    // Stats
    "stat.total": "Total Memories",
    "stat.active": "Active",
    "stat.archived": "Archived",
    "stat.deleted": "Deleted",
    "stat.sessions": "Active Sessions",

    // Table
    "table.col_id": "Memory ID",
    "table.col_summary": "Summary",
    "table.col_type": "Type",
    "table.col_importance": "Importance",
    "table.col_status": "Status",
    "table.col_created": "Created",
    "table.col_accessed": "Last Access",
    "table.col_action": "Action",
    "table.empty": "No Data",
    "table.no_summary": "(No summary)",
    "table.no_content": "(No content)",
    "table.detail": "Details",

    // Status pills
    "status.active": "Active",
    "status.archived": "Archived",
    "status.deleted": "Deleted",

    // Pagination
    "page.prev": "Previous",
    "page.next": "Next",
    "page.per_page": "Per Page",
    "page.info": "Page {page} / {total} · {count} items",
    "page.all_loaded": "{count} records total (all loaded)",
    "page.filtering": "Filtering",
    "page.keyword": "Keyword",
    "page.status": "Status",

    // Detail Drawer
    "detail.title": "Memory Details",
    "detail.edit": "Edit Memory",
    "detail.source": "Source",
    "detail.source_storage": "Custom Storage",
    "detail.source_vector": "Vector Storage",
    "detail.status": "Status",
    "detail.importance": "Importance",
    "detail.type": "Type",
    "detail.created": "Created",
    "detail.accessed": "Last Access",

    // Edit Modal
    "modal.title": "Edit Memory",
    "modal.field": "Field",
    "modal.field_content": "Content",
    "modal.field_importance": "Importance",
    "modal.field_type": "Type",
    "modal.field_status": "Status",
    "modal.content_label": "New Content",
    "modal.content_placeholder": "Enter new memory content",
    "modal.importance_label": "New Importance (0-10)",
    "modal.importance_hint": "Higher importance = higher recall priority",
    "modal.type_label": "New Type",
    "modal.type_placeholder": "e.g. FACT, EVENT, PREFERENCE",
    "modal.type_hint": "Memory type used for categorization",
    "modal.status_active": "Active",
    "modal.status_archived": "Archived",
    "modal.status_deleted": "Deleted",
    "modal.status_hint": "Deleted memories will not be recalled",
    "modal.reason_label": "Update Reason (optional)",
    "modal.reason_placeholder": "Describe the reason for this update",
    "modal.cancel": "Cancel",
    "modal.save": "Save",

    // Graph
    "graph.kicker": "Graph Memory Explorer",
    "graph.title": "Knowledge Graph View",
    "graph.subtitle": "Observe people, topics, facts and memory connections from dual-route retrieval.",
    "graph.mode_recent": "Recent Overview",
    "graph.status_line": "Showing recently active graph memory subgraph",
    "graph.query_label": "Graph Query",
    "graph.query_placeholder": "Enter person, topic, fact or sentence to view graph subgraph",
    "graph.session_filter": "Session Filter",
    "graph.session_placeholder": "Optional: limit by session_id",
    "graph.persona_filter": "Persona Filter",
    "graph.persona_placeholder": "Optional: limit by persona_id",
    "graph.memory_id": "Memory ID",
    "graph.memory_id_placeholder": "Enter memory ID to locate local subgraph",
    "graph.search": "Search Graph",
    "graph.focus": "Focus Memory",
    "graph.overview": "Recent Overview",
    "graph.visible_nodes": "Visible Nodes",
    "graph.edges": "Edges",
    "graph.entries": "Entries",
    "graph.perspective": "Perspective",
    "graph.perspective_label": "Graph Browser",
    "graph.related_memories": "Related Memories",
    "graph.canvas_title": "Graph Canvas",
    "graph.canvas_subtitle": "Click node, memory card or recall result to switch focus.",
    "graph.focus_detail": "Focus Detail",
    "graph.core_nodes": "Core Nodes",
    "graph.recall_path": "Recall Path",
    "graph.node_topic": "Topic",
    "graph.node_person": "Person",
    "graph.node_fact": "Fact",
    "graph.node_summary": "Summary",
    "graph.mode_overview": "Recent Overview",
    "graph.mode_query": "Search View",
    "graph.mode_memory_focus": "Memory Focus",
    "graph.score_doc_kw": "Doc Keyword",
    "graph.score_doc_vec": "Doc Vector",
    "graph.score_graph_kw": "Graph Keyword",
    "graph.score_graph_vec": "Graph Vector",
    "graph.canvas_hint": "Click \"Recent Overview\" to load graph, or enter search term directly.",
    "graph.canvas_no_3d": "3D graph component not loaded. Please refresh and check static assets.",
    "graph.not_logged_in": "Not logged in. Please log in to WebUI first.",
    "graph.server_format_error": "Server response format error",
    "graph.request_failed": "Graph request failed",
    "graph.loading_overview": "Loading recent graph overview...",
    "graph.load_failed": "Unable to load graph overview",
    "graph.searching_query": "Searching graph for \"{query}\"...",
    "graph.search_failed": "Graph search failed",
    "graph.enter_memory_id": "Please enter a memory ID to locate.",
    "graph.memory_id_integer": "Memory ID must be an integer.",
    "graph.focusing_memory": "Focusing relationship graph for memory #{memoryId}...",
    "graph.focus_failed": "Focus memory failed",
    "graph.loading": "Loading graph...",
    "graph.disabled_title": "Graph Memory Disabled",
    "graph.disabled_message": "Graph memory is not enabled. Please enable graph memory and complete indexing.",
    "graph.disabled_route": "Disabled",
    "graph.no_data": "No graph data",
    "graph.no_memories": "No graph memories to display",
    "graph.select_node_hint": "Please select a node or memory to view details.",
    "graph.not_enabled": "Graph memory is not enabled for this instance.",
    "graph.load_failed_title": "Graph Load Failed",
    "graph.request_failed_chip": "Request Failed",
    "graph.no_data_panel": "No data",
    "graph.session_filter_label": "Session {sessionId}",
    "graph.persona_filter_label": "Persona {personaId}",
    "graph.core_connections": "Showing core connections in graph memory.",
    "graph.query_subgraph": "Showing subgraph for dual-route retrieval of \"{query}\".",
    "graph.memory_subgraph": "Showing relationship subgraph for memory #{memoryId}.",
    "graph.filter_conditions": "Filters: {conditions}",
    "graph.route_doc_graph": "Doc + Graph · Keyword + Vector",
    "graph.route_browse": "Graph Browser",
    "graph.no_connections": "No graph connections",
    "graph.no_visible_data": "No visible graph data in current range.",
    "graph.no_3d_reload": "3D graph component failed to load. Please refresh and try again.",
    "graph.no_core_nodes": "No core nodes",
    "graph.unnamed_node": "Unnamed Node",
    "graph.degree": "Degree {degree}",
    "graph.no_related_memories": "No related memories",
    "graph.no_summary": "No summary",
    "graph.node_count_label": "Nodes {count}",
    "graph.entry_count_label": "Entries {count}",
    "graph.edge_count_label": "Edges {count}",
    "graph.focus_memory_btn": "Focus Memory",
    "graph.retrieval_hint": "After executing search, document/graph × keyword/vector retrieval details will be shown here.",
    "graph.memory_header": "Memory #{memoryId}",
    "graph.select_node_inspector": "Click a node, memory card, or retrieval result to view details.",
    "graph.related_memories_title": "Related Memories",
    "graph.related_entries_title": "Related Entries",
    "graph.no_related_memories_panel": "No related memories",
    "graph.no_related_entries_panel": "No related entries",
    "graph.inspector_memory_label": "Memory #{memoryId}",
    "graph.inspector_related_memories": "Related Memories",
    "graph.inspector_memory_count": "Related Memories",
    "graph.inspector_degree": "Degree",
    "graph.inspector_entries": "Hit Entries",
    "graph.inspector_weight": "Weight",
    "graph.node_distribution": "Node Distribution",
    "graph.no_nodes": "No nodes",
    "graph.graph_entries_title": "Graph Entries",
    "graph.no_graph_entries": "No graph entries",
    "graph.edge_tooltip": "{relation} · Memory #{memoryId}",
    "graph.graph_tooltip_meta": "Memories {memoryCount} · Relations {degree} · Entries {entryCount}",

    // Recall Test
    "recall.clear": "Clear Results",
    "recall.panel_title": "Memory Recall Test",
    "recall.panel_subtitle": "Enter query to test hybrid retrieval recall capability",
    "recall.query_label": "Query Content",
    "recall.query_placeholder": "Enter your query, system will use hybrid retrieval (BM25 + vector similarity)",
    "recall.k_label": "Return Count",
    "recall.session_label": "Session ID (optional)",
    "recall.session_placeholder": "Enter session ID to filter specific session memories (supports multiple formats)",
    "recall.search": "Execute Recall",
    "recall.searching": "Executing...",
    "recall.results_title": "Recall Results",
    "recall.result_count": "Recall Count",
    "recall.result_time": "Query Time",
    "recall.empty": "No results · Enter query and execute",
    "recall.no_match": "No matching memories found",
    "recall.result_header": "Result #{number}",
    "recall.result_memory_id": "Memory ID",
    "recall.result_similarity": "Similarity Score",
    "recall.result_session": "Session UUID",
    "recall.result_importance": "Importance",
    "recall.result_type": "Type",
    "recall.result_status": "Status",

    // Nuke
    "nuke.message": "All memories will be erased in {seconds} seconds. Cancel now to abort NUKE!",
    "nuke.message_zero": "Erasing all memories... Please keep window open.",
    "nuke.cancel": "Cancel NUKE",
    "nuke.done": "NUKE complete! All memories removed from interface.",
    "nuke.table_empty": "NUKE complete! All memories erased. Click Refresh to reload.",

    // Toasts / Messages
    "toast.login_success": "Login successful, loading data...",
    "toast.session_restored": "Session restored, verifying...",
    "toast.verify_success": "Verification successful, loading data...",
    "toast.logout": "Logged out",
    "toast.nuke_start": "NUKE countdown started!",
    "toast.nuke_cancel": "NUKE cancelled! Memories preserved",
    "toast.nuke_done": "NUKE complete! All memories removed from UI (visual only)",
    "toast.delete_success": "Successfully deleted {count} memories",
    "toast.delete_partial": "Partial delete failed: {success} succeeded, {failed} failed",
    "toast.delete_failed": "Delete failed",
    "toast.fetch_stats_failed": "Failed to fetch statistics",
    "toast.fetch_memories_failed": "Failed to fetch memories",
    "toast.record_not_found": "Record not found",
    "toast.memory_not_found": "Current memory not found",
    "toast.enter_new_value": "Please enter a new value",
    "toast.recall_failed": "Recall failed",
    "toast.delete_confirm_title": "Confirm delete?",
    "toast.delete_confirm_body": "About to delete {count} memories.",
    "toast.delete_confirm_irreversible": "This action cannot be undone!",
    "toast.delete_confirm_action": 'Click "OK" to proceed, "Cancel" to keep.',
    "toast.delete_cancelled": "Delete cancelled",
    "toast.delete_failed_all": "Delete failed: all {count} memories could not be deleted",
    "toast.delete_none": "No memories deleted",
    "toast.update_success": "Update successful",
    "toast.update_failed": "Update failed",
    "toast.recall_success": "Successfully recalled {count} memories",
    "toast.search_results": "Search results: {total} memories found, showing {shown} items",
    "toast.no_results": "No related memories found",
    "toast.error": "Operation failed",
    "toast.not_logged_in": "Not logged in",
    "toast.session_expired": "Session expired, please log in again",
    "toast.server_format_error": "Server returned malformed data",
    "toast.request_failed": "Request failed",
    "toast.theme_dark": "🌙 Switched to Dark Mode",
    "toast.theme_light": "☀️ Switched to Light Mode",
  }
};

class I18nManager {
  constructor() {
    // Try localStorage first, then browser language, fallback to en
    const stored = localStorage.getItem("lmem_language");
    if (stored && TRANSLATIONS[stored]) {
      this.lang = stored;
    } else {
      const navLang = (navigator.language || navigator.userLanguage || "en").toLowerCase();
      const langCode = navLang.split("-")[0]; // "ru-RU" → "ru"
      this.lang = TRANSLATIONS[langCode] ? langCode : "en";
    }
    this._applyHtmlLang();
  }

  _applyHtmlLang() {
    document.documentElement.setAttribute("lang", this.lang);
  }

  t(key, params = {}) {
    let text = TRANSLATIONS[this.lang]?.[key];
    if (text === undefined) {
      text = TRANSLATIONS["zh"]?.[key];
    }
    if (text === undefined) {
      return `[i18n:${key}]`;
    }
    // Simple parameter substitution: {paramName}
    if (params && typeof params === "object") {
      Object.entries(params).forEach(([k, v]) => {
        text = text.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
      });
    }
    return text;
  }

  setLanguage(lang) {
    if (!TRANSLATIONS[lang]) return false;
    this.lang = lang;
    localStorage.setItem("lmem_language", lang);
    this._applyHtmlLang();
    return true;
  }

  getAvailableLanguages() {
    return [
      { code: "zh", label: "🇨🇳 中文", name: "中文" },
      { code: "en", label: "🇬🇧 English", name: "English" },
    ];
  }
}

// Global instance
const i18n = new I18nManager();

/**
 * Apply translations to all elements with data-i18n attribute
 */
function applyTranslations() {
  // Text content
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key) {
      if (el.tagName === "TITLE") {
        document.title = i18n.t(key);
      } else {
        el.textContent = i18n.t(key);
      }
    }
  });

  // Placeholders
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key) {
      el.setAttribute("placeholder", i18n.t(key));
    }
  });

  // Title/tooltip attributes
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    if (key) {
      el.setAttribute("title", i18n.t(key));
    }
  });

  // Re-render Lucide icons in case any were affected by textContent changes
  if (typeof lucide !== "undefined" && lucide.createIcons) {
    lucide.createIcons();
  }
}
