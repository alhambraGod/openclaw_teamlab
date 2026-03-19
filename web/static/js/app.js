/**
 * OpenClaw TeamLab — Vue.js Application
 * 下一代 PI 团队管理 Dashboard
 */

const API_BASE = '/api';

// ── Logger ──
const logger = {
    debug: (...args) => console.debug('[teamlab]', ...args),
    info:  (...args) => console.info('[teamlab]', ...args),
    warn:  (...args) => console.warn('[teamlab]', ...args),
    error: (...args) => console.error('[teamlab]', ...args),
};

// ── API Helper ──
async function api(path, options = {}) {
    const url = API_BASE + path;
    const config = {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    };
    if (config.body && typeof config.body === 'object') {
        config.body = JSON.stringify(config.body);
    }
    try {
        const res = await fetch(url, config);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'API Error');
        }
        return await res.json();
    } catch (e) {
        console.error(`API ${path}:`, e);
        throw e;
    }
}

// ── ECharts Theme ──
const CHART_THEME = {
    backgroundColor: 'transparent',
    textStyle: { color: '#94a3b8', fontFamily: 'Inter, sans-serif' },
    title: { textStyle: { color: '#e2e8f0' } },
    legend: { textStyle: { color: '#94a3b8' } },
    categoryAxis: { axisLine: { lineStyle: { color: '#1e293b' } }, splitLine: { lineStyle: { color: '#1e293b' } } },
    valueAxis: { axisLine: { lineStyle: { color: '#1e293b' } }, splitLine: { lineStyle: { color: '#1e293b' } } },
};

// ── Chart Builders ──
const Charts = {
    // Radar chart for student capabilities
    radar(containerId, dimensions, scores, compareScores = null) {
        const dom = document.getElementById(containerId);
        if (!dom) return;
        const chart = echarts.init(dom);
        const indicator = dimensions.map(d => ({ name: d.label, max: 10 }));
        const series = [{
            name: '当前能力',
            type: 'radar',
            symbol: 'circle',
            symbolSize: 6,
            data: [{
                value: scores,
                name: '当前',
                areaStyle: { color: 'rgba(220,53,69,0.15)' },
                lineStyle: { color: '#DC3545', width: 2 },
                itemStyle: { color: '#DC3545' },
            }],
        }];
        if (compareScores) {
            series[0].data.push({
                value: compareScores,
                name: '上次评估',
                areaStyle: { color: 'rgba(14,165,233,0.08)' },
                lineStyle: { color: '#0ea5e9', width: 1, type: 'dashed' },
                itemStyle: { color: '#0ea5e9' },
            });
        }
        chart.setOption({
            ...CHART_THEME,
            tooltip: { trigger: 'item' },
            radar: {
                indicator,
                shape: 'polygon',
                splitNumber: 5,
                axisName: { color: '#94a3b8', fontSize: 11 },
                splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
                splitArea: { areaStyle: { color: ['rgba(255,255,255,0.01)', 'rgba(255,255,255,0.03)'] } },
                axisLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
            },
            series,
        });
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },

    // Mini radar for student cards
    miniRadar(containerId, scores) {
        const dom = document.getElementById(containerId);
        if (!dom) return;
        const chart = echarts.init(dom);
        const dims = ['文献', '实验', '数据', '编程', '写作', '汇报', '创新', '协作'];
        chart.setOption({
            ...CHART_THEME,
            radar: {
                indicator: dims.map(d => ({ name: d, max: 10 })),
                radius: '65%',
                axisName: { fontSize: 9, color: '#64748b' },
                splitLine: { lineStyle: { color: 'rgba(255,255,255,0.03)' } },
                splitArea: { show: false },
                axisLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
            },
            series: [{
                type: 'radar',
                symbol: 'none',
                data: [{
                    value: scores,
                    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(220,53,69,0.3)' },
                        { offset: 1, color: 'rgba(14,165,233,0.1)' },
                    ])},
                    lineStyle: { color: '#DC3545', width: 1.5 },
                }],
            }],
        });
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },

    // Force-directed collaboration network
    network(containerId, nodes, edges) {
        const dom = document.getElementById(containerId);
        if (!dom) return;
        const chart = echarts.init(dom);
        const categories = [
            { name: 'PhD', itemStyle: { color: '#DC3545' } },
            { name: 'Master', itemStyle: { color: '#0ea5e9' } },
            { name: 'PostDoc', itemStyle: { color: '#a855f7' } },
            { name: 'Other', itemStyle: { color: '#64748b' } },
        ];
        chart.setOption({
            ...CHART_THEME,
            tooltip: {
                trigger: 'item',
                formatter: (p) => {
                    if (p.dataType === 'edge') return `互补度: ${(p.value * 100).toFixed(0)}%<br/>${p.data.idea || ''}`;
                    return `${p.name}<br/>${p.data.area || ''}`;
                },
            },
            legend: [{ data: categories.map(c => c.name), textStyle: { color: '#94a3b8' }, top: 10 }],
            animationDuration: 1500,
            animationEasingUpdate: 'quinticInOut',
            series: [{
                type: 'graph',
                layout: 'force',
                data: nodes.map(n => ({
                    ...n,
                    symbolSize: Math.max(30, (n.score || 5) * 8),
                    label: { show: true, color: '#e2e8f0', fontSize: 11, position: 'bottom' },
                    itemStyle: {
                        shadowBlur: 10,
                        shadowColor: 'rgba(220,53,69,0.3)',
                    },
                })),
                links: edges.map(e => ({
                    ...e,
                    lineStyle: {
                        width: Math.max(1, (e.value || 0.5) * 5),
                        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                            { offset: 0, color: 'rgba(14,165,233,0.6)' },
                            { offset: 1, color: 'rgba(168,85,247,0.6)' },
                        ]),
                        curveness: 0.1,
                    },
                })),
                categories,
                roam: true,
                force: {
                    repulsion: 300,
                    gravity: 0.1,
                    edgeLength: [100, 250],
                    layoutAnimation: true,
                },
                emphasis: {
                    focus: 'adjacency',
                    lineStyle: { width: 5 },
                },
            }],
        });
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },

    // Research direction tree/mindmap
    directionTree(containerId, data) {
        const dom = document.getElementById(containerId);
        if (!dom) return;
        const chart = echarts.init(dom);
        chart.setOption({
            ...CHART_THEME,
            tooltip: { trigger: 'item', formatter: '{b}' },
            series: [{
                type: 'tree',
                data: [data],
                top: '5%',
                left: '15%',
                bottom: '5%',
                right: '25%',
                symbolSize: 12,
                orient: 'LR',
                label: {
                    position: 'left',
                    verticalAlign: 'middle',
                    align: 'right',
                    fontSize: 11,
                    color: '#e2e8f0',
                },
                leaves: {
                    label: { position: 'right', align: 'left' },
                },
                lineStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                        { offset: 0, color: '#DC3545' },
                        { offset: 1, color: '#0ea5e9' },
                    ]),
                    width: 1.5,
                },
                emphasis: { focus: 'descendant' },
                expandAndCollapse: true,
                animationDuration: 550,
                animationDurationUpdate: 750,
                itemStyle: {
                    color: '#DC3545',
                    borderColor: '#DC3545',
                },
            }],
        });
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },

    // Progress timeline
    timeline(containerId, events) {
        const dom = document.getElementById(containerId);
        if (!dom) return;
        const chart = echarts.init(dom);
        const typeColors = {
            paper_accepted: '#22c55e', paper_submitted: '#0ea5e9', paper_rejected: '#ef4444',
            experiment_completed: '#a855f7', milestone_reached: '#f59e0b', presentation: '#06b6d4',
            code_released: '#8b5cf6', award: '#eab308', custom: '#64748b',
        };
        const data = events.map(e => ({
            value: [e.event_date, e.event_type, e.title],
            itemStyle: { color: typeColors[e.event_type] || '#64748b' },
            symbolSize: e.event_type === 'paper_accepted' || e.event_type === 'award' ? 14 : 8,
        }));
        chart.setOption({
            ...CHART_THEME,
            tooltip: {
                trigger: 'item',
                formatter: (p) => `${p.value[2]}<br/><span style="color:#94a3b8">${p.value[0]}</span>`,
            },
            xAxis: {
                type: 'time',
                axisLine: { lineStyle: { color: '#1e293b' } },
                axisLabel: { color: '#64748b', fontSize: 10 },
            },
            yAxis: {
                type: 'category',
                data: [...new Set(events.map(e => e.event_type))],
                axisLine: { show: false },
                axisLabel: { color: '#64748b', fontSize: 10 },
            },
            series: [{
                type: 'scatter',
                data,
                encode: { x: 0, y: 1 },
            }],
            grid: { left: '15%', right: '5%', top: '8%', bottom: '15%' },
        });
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },

    // Benchmark comparison bar chart
    benchmark(containerId, teams) {
        const dom = document.getElementById(containerId);
        if (!dom) return;
        const chart = echarts.init(dom);
        const metrics = ['发文量', 'H-Index', '学生数', '研究广度', '合作度'];
        chart.setOption({
            ...CHART_THEME,
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { data: teams.map(t => t.name), textStyle: { color: '#94a3b8' }, top: 10 },
            grid: { left: '8%', right: '5%', bottom: '8%', top: '15%' },
            xAxis: { type: 'category', data: metrics, axisLabel: { color: '#94a3b8' } },
            yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e293b' } } },
            series: teams.map((t, i) => ({
                name: t.name,
                type: 'bar',
                barWidth: '15%',
                data: t.scores,
                itemStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: ['#DC3545', '#0ea5e9', '#a855f7', '#22c55e'][i % 4] },
                        { offset: 1, color: ['rgba(220,53,69,0.2)', 'rgba(14,165,233,0.2)', 'rgba(168,85,247,0.2)', 'rgba(34,197,94,0.2)'][i % 4] },
                    ]),
                    borderRadius: [4, 4, 0, 0],
                },
            })),
        });
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },
};

// ── Vue Application ──
const { createApp, ref, reactive, computed, onMounted, watch, nextTick } = Vue;

const app = createApp({
    setup() {
        // ── Navigation ──
        const tabs = [
            { id: 'projects',   label: '项目总览', icon: '🏠' },
            { id: 'directions', label: '研究方向', icon: '🧭' },
            { id: 'network',    label: '合作网络', icon: '🔗' },
            { id: 'members',    label: '团队成员', icon: '👥' },
            { id: 'meetings',   label: '会议',     icon: '📋' },
            { id: 'benchmark',  label: '团队对标', icon: '📈' },
            { id: 'data',       label: '数据中台', icon: '🔄' },
            { id: 'scheduler',  label: '调度管理', icon: '⏰' },
            { id: 'knowledge',  label: '知识图谱', icon: '🧠' },
        ];
        const currentTab = ref('projects');

        // ── System State ──
        const systemOk = ref(true);
        const workerInfo = ref('Workers: 0/0');

        // ── Dashboard ──
        const dashboardStats = ref([
            { icon: '👥', label: '活跃成员',   value: 0, trend: 0 },
            { icon: '📄', label: '在投论文',   value: 0, trend: 0 },
            { icon: '🧭', label: '研究方向',   value: 0, trend: 0 },
            { icon: '🤖', label: '本周任务',   value: 0, trend: 0 },
        ]);
        const recentEvents = ref([]);
        const aiInsights = ref([]);
        const eventIcons = {
            paper_submitted: '📝', paper_accepted: '🎉', paper_rejected: '😔',
            experiment_completed: '🔬', milestone_reached: '🏆', presentation: '🎤',
            code_released: '💻', award: '🏅', custom: '📌',
        };
        const eventColors = {
            paper_accepted: 'bg-emerald-500/20', paper_submitted: 'bg-blue-500/20', paper_rejected: 'bg-red-500/20',
            experiment_completed: 'bg-purple-500/20', milestone_reached: 'bg-amber-500/20', presentation: 'bg-cyan-500/20',
        };

        // ── Students ──
        const students = ref([]);
        const selectedStudent = ref(null);
        const showAddStudent = ref(false);
        const newStudent = reactive({
            name: '', email: '', degree_type: 'phd', research_area: '',
            enrollment_date: '', feishu_open_id: '', bio: '',
        });
        const studentSubmitting = ref(false);

        // ── Collaboration ──
        const collaborations = ref([]);

        // ── Directions ──
        const directions = ref([]);
        const showAddDirection = ref(false);
        const newDirection = reactive({
            title: '', description: '', source: 'pi_defined', status: 'exploring', evidence: '',
        });
        const directionSubmitting = ref(false);

        // ── Meetings ──
        const meetings = ref([]);
        const showAddMeeting = ref(false);
        const selectedMeeting = ref(null);
        const newMeeting = reactive({
            title: '', meeting_type: 'group', meeting_date: '', attendees_text: '', raw_notes: '',
        });
        const meetingSubmitting = ref(false);

        // ── Chat ──
        const chatMessages = ref([]);
        const chatInput = ref('');
        const chatLoading = ref(false);
        const chatContainer = ref(null);
        const chatHints = [
            '查看所有学生进展',
            '推荐张三和李四的合作方向',
            '为王五制定指导计划',
            '发现新的研究方向',
            '总结上周的组会',
            '对比全球类似课题组',
        ];

        // ── Feishu Logs ──
        const feishuLogs = ref([]);
        const feishuFilter = ref('all'); // all / feishu / web
        const feishuAutoRefresh = ref(true);
        let feishuRefreshTimer = null;

        // ── CoEvo Data Integration ──
        const coevoOverview = ref(null);
        const coevoProjects = ref([]);
        const coevoStudents = ref([]);
        const coevoMeetings = ref([]);
        const coevoBlockers = ref([]);
        const coevoEngagement = ref([]);
        const coevoMomentum = ref([]);
        const coevoSyncLogs = ref([]);
        const coevoSelectedProject = ref(null);
        const coevoSelectedStudent = ref(null);
        const coevoSelectedMeeting = ref(null);
        const coevoActiveView = ref('overview'); // overview / projects / students / meetings / analytics
        const coevoLoading = ref(false);
        const coevoSyncing = ref(false);

        // ── Projects Overview ──
        const projectsOverview = ref([]);
        const directionClusters = ref([]);
        const directionIdeas = ref([]);
        const pendingIdeasCount = ref(0);
        const directionsSubView = ref('graph'); // graph | international | ideas
        const selectedCluster = ref(null);
        const directionAnalyzing = ref(false);

        // ── Network (multi-layer) ──
        const networkData = ref({ nodes: [], edges: [], stats: {} });
        const selectedNetworkNode = ref(null);
        let networkChartInstance = null;

        // ── Innovation Index (Benchmark) ──
        const innovationIndex = ref([]);

        // ── Data sub-tab (was coevo + feishu) ──
        const dataSubTab = ref('overview'); // overview | projects | students | meetings | analytics | feishu

        // ── Toast ──
        const toastMessage = ref('');
        const toastVisible = ref(false);
        let toastTimer = null;

        function showToast(msg, duration = 3000) {
            toastMessage.value = msg;
            toastVisible.value = true;
            if (toastTimer) clearTimeout(toastTimer);
            toastTimer = setTimeout(() => { toastVisible.value = false; }, duration);
        }

        async function loadProjectsOverview() {
            try {
                const data = await api('/dashboard/projects-overview');
                projectsOverview.value = data;
            } catch(e) { console.warn('Projects overview:', e); }
        }

        async function loadDirectionClusters() {
            try {
                const [clusters, ideas] = await Promise.allSettled([
                    api('/directions/clusters'),
                    api('/directions/ideas?status=pending'),
                ]);
                directionClusters.value = clusters.status === 'fulfilled' ? clusters.value : [];
                const ideasList = ideas.status === 'fulfilled' ? ideas.value : [];
                directionIdeas.value = ideasList;
                pendingIdeasCount.value = ideasList.filter(i => i.status === 'pending').length;
            } catch(e) { console.warn('Direction clusters:', e); }
        }

        async function triggerDirectionAnalyze() {
            directionAnalyzing.value = true;
            try {
                await api('/directions/analyze', { method: 'POST' });
                showToast('研究方向分析任务已提交，约需1-2分钟完成');
                setTimeout(loadDirectionClusters, 90000);
            } catch(e) { showToast('分析任务提交失败: ' + e.message); }
            directionAnalyzing.value = false;
        }

        async function activateIdea(id) {
            try {
                await api(`/directions/ideas/${id}/activate`, { method: 'POST' });
                directionIdeas.value = directionIdeas.value.filter(i => i.id !== id);
                pendingIdeasCount.value = Math.max(0, pendingIdeasCount.value - 1);
                showToast('已激活研究方向 Idea');
            } catch(e) { showToast('激活失败: ' + e.message); }
        }

        async function dismissIdea(id) {
            try {
                await api(`/directions/ideas/${id}/dismiss`, { method: 'POST' });
                directionIdeas.value = directionIdeas.value.filter(i => i.id !== id);
                pendingIdeasCount.value = Math.max(0, pendingIdeasCount.value - 1);
                showToast('已忽略');
            } catch(e) { showToast('操作失败: ' + e.message); }
        }

        async function loadNetworkData() {
            try {
                const data = await api('/collaborations/network');
                networkData.value = data;
                await nextTick();
                initNetworkChart(data);
            } catch(e) { console.warn('Network data:', e); }
        }

        function initNetworkChart(data) {
            const dom = document.getElementById('multilayer-network-chart');
            if (!dom) return;
            if (networkChartInstance) { networkChartInstance.dispose(); }
            networkChartInstance = echarts.init(dom);

            const nodeColors = { project: '#38bdf8', student: '#22c55e', direction: '#a855f7' };
            const nodeSymbols = { project: 'roundRect', student: 'circle', direction: 'diamond' };
            const edgeColors = { collab: '#38bdf8', direction_link: '#a855f7', cross_project: '#f97316' };

            const nodes = (data.nodes || []).map(n => ({
                id: n.id,
                name: n.name,
                symbolSize: n.type === 'project' ? 50 : n.type === 'direction' ? 30 : Math.max(20, Math.min(45, (n.capability_avg || 60) / 3)),
                symbol: nodeSymbols[n.type] || 'circle',
                itemStyle: { color: nodeColors[n.type] || '#64748b', shadowBlur: n.type === 'project' ? 15 : 5, shadowColor: nodeColors[n.type] || '#64748b' },
                label: { show: true, color: '#e2e8f0', fontSize: n.type === 'project' ? 13 : 10, position: 'bottom' },
                tooltip: { formatter: () => `${n.name}<br/>${n.type === 'student' ? ('能力均分: ' + (n.capability_avg || 0).toFixed(1)) : ''}` },
                _raw: n,
            }));

            const edges = (data.edges || []).map(e => ({
                source: e.source,
                target: e.target,
                lineStyle: {
                    color: edgeColors[e.type] || '#64748b',
                    width: e.type === 'collab' ? Math.max(1, (e.weight || 0.5) * 4) : 1,
                    type: e.type === 'direction_link' ? 'dashed' : 'solid',
                    opacity: 0.6,
                    curveness: 0.1,
                },
            }));

            networkChartInstance.setOption({
                ...CHART_THEME,
                tooltip: { trigger: 'item' },
                animationDuration: 1500,
                series: [{
                    type: 'graph',
                    layout: 'force',
                    roam: true,
                    draggable: true,
                    data: nodes,
                    links: edges,
                    force: { repulsion: 250, gravity: 0.08, edgeLength: [80, 200], layoutAnimation: true },
                    emphasis: { focus: 'adjacency', lineStyle: { width: 4 } },
                }],
            });

            networkChartInstance.on('click', (params) => {
                if (params.dataType === 'node') {
                    selectedNetworkNode.value = params.data._raw;
                }
            });

            window.addEventListener('resize', () => networkChartInstance && networkChartInstance.resize());
        }

        function initDirectionGraph() {
            const dom = document.getElementById('direction-force-chart');
            if (!dom || directionClusters.value.length === 0) return;
            const chart = echarts.init(dom);

            const groupColors = {};
            const groups = [...new Set(directionClusters.value.map(c => c.similarity_group))];
            const palette = ['#DC3545', '#0ea5e9', '#a855f7', '#22c55e', '#f59e0b', '#06b6d4', '#ef4444', '#8b5cf6'];
            groups.forEach((g, i) => { groupColors[g] = palette[i % palette.length]; });

            const nodes = directionClusters.value.map(c => ({
                id: String(c.id),
                name: c.topic,
                symbolSize: Math.max(25, Math.min(60, ((c.related_students || []).length + 1) * 10)),
                itemStyle: { color: groupColors[c.similarity_group] || '#64748b' },
                label: { show: true, color: '#e2e8f0', fontSize: 11 },
                _cluster: c,
            }));

            chart.setOption({
                ...CHART_THEME,
                tooltip: { trigger: 'item', formatter: (p) => p.data.name + '<br/>' + (p.data._cluster?.similarity_group || '') },
                series: [{
                    type: 'graph',
                    layout: 'force',
                    roam: true,
                    draggable: true,
                    data: nodes,
                    links: [],
                    force: { repulsion: 150, gravity: 0.1, edgeLength: 120 },
                    label: { show: true },
                }],
            });

            chart.on('click', (params) => {
                if (params.dataType === 'node') {
                    selectedCluster.value = params.data._cluster;
                }
            });
            window.addEventListener('resize', () => chart.resize());
        }

        async function loadInnovationIndex() {
            try {
                innovationIndex.value = await api('/coevo/innovation-index');
                await nextTick();
                renderInnovationCharts();
            } catch(e) { console.warn('Innovation index:', e); }
        }

        function renderInnovationCharts() {
            innovationIndex.value.forEach((proj, idx) => {
                const dom = document.getElementById(`innovation-gauge-${proj.project_id}`);
                if (!dom) return;
                const chart = echarts.init(dom);
                const score = proj.innovation_index;
                const color = score >= 70 ? '#22c55e' : score >= 40 ? '#f59e0b' : '#ef4444';
                chart.setOption({
                    ...CHART_THEME,
                    series: [{
                        type: 'gauge',
                        startAngle: 200, endAngle: -20,
                        min: 0, max: 100,
                        itemStyle: { color },
                        progress: { show: true, width: 12, roundCap: true },
                        pointer: { show: false },
                        axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(255,255,255,0.05)']] } },
                        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
                        detail: {
                            valueAnimation: true, offsetCenter: [0, '0%'],
                            fontSize: 28, fontWeight: 'bold', color,
                            formatter: '{value}',
                        },
                        title: { show: false },
                        data: [{ value: Math.round(score) }],
                    }],
                });
                window.addEventListener('resize', () => chart.resize());
            });
        }

        async function loadProjectsTab() {
            await Promise.allSettled([
                loadProjectsOverview(),
                loadDirectionClusters(),
                loadDashboard(),
            ]);
            await nextTick();
            // Render direction graph on projects tab (mini version)
            if (directionClusters.value.length > 0) {
                initDirectionGraph();
            }
        }

        async function loadDirectionsTab() {
            await loadDirectionClusters();
            await nextTick();
            if (directionsSubView.value === 'graph') {
                initDirectionGraph();
            }
        }

        // ── Command Center ──
        const teamHealth = ref(null);
        const riskDashboard = ref(null);
        const actionItems = ref(null);
        const studentNarrative = ref(null);
        const riskComputing = ref(false);

        async function loadTeamHealth() {
            try {
                teamHealth.value = await api('/coevo/team-health');
            } catch(e) { console.warn('Team health:', e); }
        }

        async function loadRiskDashboard() {
            try {
                riskDashboard.value = await api('/coevo/risk/dashboard');
            } catch(e) { console.warn('Risk dashboard:', e); }
        }

        async function loadActionItems() {
            try {
                actionItems.value = await api('/coevo/actions?limit=30');
            } catch(e) { console.warn('Action items:', e); }
        }

        async function triggerRiskCompute() {
            riskComputing.value = true;
            try {
                await api('/coevo/risk/compute', { method: 'POST' });
                // Poll after a delay
                setTimeout(async () => {
                    await loadRiskDashboard();
                    await loadTeamHealth();
                    riskComputing.value = false;
                }, 5000);
            } catch(e) {
                console.warn('Risk compute:', e);
                riskComputing.value = false;
            }
        }

        async function loadStudentNarrative(coevoUserId) {
            try {
                studentNarrative.value = await api(`/coevo/students/${coevoUserId}/narrative?months=3`);
            } catch(e) { console.warn('Narrative:', e); }
        }

        async function loadCommandCenter() {
            await Promise.all([
                loadTeamHealth(),
                loadRiskDashboard(),
                loadActionItems(),
            ]);
            // Render team health gauge after data loads
            await nextTick();
            if (teamHealth.value) {
                renderHealthGauge();
            }
        }

        function renderHealthGauge() {
            const dom = document.getElementById('health-gauge');
            if (!dom || !teamHealth.value) return;
            const chart = echarts.init(dom);
            const score = teamHealth.value.overall_health;
            const color = score >= 70 ? '#22c55e' : score >= 40 ? '#f59e0b' : '#ef4444';
            chart.setOption({
                ...CHART_THEME,
                series: [{
                    type: 'gauge',
                    startAngle: 200,
                    endAngle: -20,
                    min: 0,
                    max: 100,
                    splitNumber: 10,
                    itemStyle: { color },
                    progress: { show: true, width: 20, roundCap: true },
                    pointer: { show: false },
                    axisLine: { lineStyle: { width: 20, color: [[1, 'rgba(255,255,255,0.05)']] } },
                    axisTick: { show: false },
                    splitLine: { show: false },
                    axisLabel: { show: false },
                    title: { show: true, offsetCenter: [0, '35%'], fontSize: 14, color: '#94a3b8' },
                    detail: {
                        valueAnimation: true, offsetCenter: [0, '-5%'],
                        fontSize: 40, fontWeight: 'bold', color,
                        formatter: '{value}',
                    },
                    data: [{ value: Math.round(score), name: '团队健康指数' }],
                }],
            });
            window.addEventListener('resize', () => chart.resize());
        }

        async function loadCoevoOverview() {
            try {
                coevoOverview.value = await api('/coevo/overview');
            } catch(e) { console.warn('CoEvo overview:', e); }
        }

        async function loadCoevoProjects() {
            try {
                coevoProjects.value = await api('/coevo/projects');
            } catch(e) { console.warn('CoEvo projects:', e); }
        }

        async function loadCoevoStudents() {
            try {
                coevoStudents.value = await api('/coevo/students');
            } catch(e) { console.warn('CoEvo students:', e); }
        }

        async function loadCoevoMeetings(projectId = null) {
            try {
                const qs = projectId ? `?project_id=${projectId}&limit=30` : '?limit=30';
                coevoMeetings.value = await api('/coevo/meetings' + qs);
            } catch(e) { console.warn('CoEvo meetings:', e); }
        }

        async function loadCoevoBlockers(projectId = null) {
            try {
                const qs = projectId ? `?project_id=${projectId}&limit=20` : '?limit=20';
                coevoBlockers.value = await api('/coevo/analytics/blockers' + qs);
            } catch(e) { console.warn('CoEvo blockers:', e); }
        }

        async function loadCoevoEngagement(projectId = null) {
            try {
                const qs = projectId ? `?project_id=${projectId}` : '';
                coevoEngagement.value = await api('/coevo/analytics/meeting-engagement' + qs);
            } catch(e) { console.warn('CoEvo engagement:', e); }
        }

        async function loadCoevoMomentum() {
            try {
                coevoMomentum.value = await api('/coevo/analytics/research-momentum');
            } catch(e) { console.warn('CoEvo momentum:', e); }
        }

        async function loadCoevoSyncLogs() {
            try {
                coevoSyncLogs.value = await api('/coevo/sync/logs?limit=10');
            } catch(e) { console.warn('CoEvo sync logs:', e); }
        }

        async function selectCoevoProject(project) {
            coevoSelectedProject.value = project;
            coevoLoading.value = true;
            try {
                const detail = await api(`/coevo/projects/${project.id}`);
                coevoSelectedProject.value = detail;
            } catch(e) { console.warn('CoEvo project detail:', e); }
            coevoLoading.value = false;
        }

        async function selectCoevoStudent(student) {
            coevoSelectedStudent.value = student;
            coevoLoading.value = true;
            try {
                const detail = await api(`/coevo/students/${student.id}`);
                coevoSelectedStudent.value = detail;
            } catch(e) { console.warn('CoEvo student detail:', e); }
            coevoLoading.value = false;
        }

        async function selectCoevoMeeting(meeting) {
            coevoSelectedMeeting.value = meeting;
            coevoLoading.value = true;
            try {
                const detail = await api(`/coevo/meetings/${meeting.id}`);
                coevoSelectedMeeting.value = detail;
            } catch(e) { console.warn('CoEvo meeting detail:', e); }
            coevoLoading.value = false;
        }

        async function triggerCoevoSync() {
            coevoSyncing.value = true;
            try {
                const result = await api('/coevo/sync/students/blocking', { method: 'POST' });
                alert(`同步完成！\n读取: ${result.coevo_records_read} 条\n新建: ${result.openclaw_records_created} 条\n更新: ${result.openclaw_records_updated} 条`);
                await loadCoevoOverview();
                await loadCoevoStudents();
                await loadCoevoSyncLogs();
            } catch(e) {
                alert('同步失败: ' + e.message);
            }
            coevoSyncing.value = false;
        }

        async function loadCoevoTab() {
            coevoLoading.value = true;
            await Promise.allSettled([
                loadCoevoOverview(),
                loadCoevoProjects(),
                loadCoevoStudents(),
                loadCoevoMeetings(),
                loadCoevoSyncLogs(),
            ]);
            coevoLoading.value = false;
        }

        // Render engagement bar chart
        function renderEngagementChart(data) {
            const dom = document.getElementById('engagement-chart');
            if (!dom || !data.length) return;
            const chart = echarts.init(dom);
            chart.setOption({
                ...CHART_THEME,
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                grid: { left: '20%', right: '5%', bottom: '5%', top: '5%', containLabel: true },
                xAxis: { type: 'value', max: 1, axisLabel: { formatter: v => (v * 100).toFixed(0) + '%', color: '#94a3b8' } },
                yAxis: {
                    type: 'category',
                    data: data.map(d => d.student_name || d.user_id),
                    axisLabel: { color: '#94a3b8', fontSize: 11 }
                },
                series: [{
                    type: 'bar',
                    data: data.map(d => ({
                        value: d.engagement_rate,
                        itemStyle: {
                            color: d.engagement_rate >= 0.8 ? '#22c55e' :
                                   d.engagement_rate >= 0.5 ? '#f59e0b' : '#ef4444',
                            borderRadius: [0, 4, 4, 0],
                        }
                    })),
                    label: { show: true, position: 'right', formatter: p => (p.value * 100).toFixed(0) + '%', color: '#94a3b8' }
                }],
            });
            window.addEventListener('resize', () => chart.resize());
        }

        const STATUS_COLORS = {
            queued: 'bg-blue-500/20 text-blue-400',
            processing: 'bg-amber-500/20 text-amber-400',
            completed: 'bg-emerald-500/20 text-emerald-400',
            failed: 'bg-red-500/20 text-red-400',
        };
        const SOURCE_ICONS = { feishu: '💬', web: '🌐', api: '⚙️', system: '🔧' };

        async function loadFeishuLogs() {
            try {
                const source = feishuFilter.value === 'all' ? '' : feishuFilter.value;
                const qs = source ? `?source=${source}` : '';
                feishuLogs.value = await api('/chat/logs' + qs);
            } catch { /* keep existing */ }
        }

        function startFeishuAutoRefresh() {
            stopFeishuAutoRefresh();
            if (feishuAutoRefresh.value) {
                feishuRefreshTimer = setInterval(loadFeishuLogs, 5000);
            }
        }

        function stopFeishuAutoRefresh() {
            if (feishuRefreshTimer) { clearInterval(feishuRefreshTimer); feishuRefreshTimer = null; }
        }

        watch(feishuAutoRefresh, (v) => { if (v) startFeishuAutoRefresh(); else stopFeishuAutoRefresh(); });
        watch(feishuFilter, loadFeishuLogs);

        // ── Data Fetching ──
        async function loadDashboard() {
            try {
                const data = await api('/dashboard/overview');
                dashboardStats.value = [
                    { icon: '👥', label: '团队成员', value: data.active_students || 0, sub: `${data.total_projects || 0}个项目` },
                    { icon: '🗓', label: '已完成会议', value: data.completed_meetings || 0, sub: `共${data.total_meetings || 0}次` },
                    { icon: '🧭', label: '研究方向', value: data.active_directions || 0, sub: '规划中' },
                    { icon: '⚙️', label: '队列任务', value: data.weekly_tasks || 0, sub: 'AI处理' },
                ];
                recentEvents.value = data.recent_events || [];
                aiInsights.value = data.ai_insights || [];
                systemOk.value = data.system_ok !== false;
                workerInfo.value = `Workers: ${data.busy_workers || 0}/${data.total_workers || 0}`;
            } catch (e) {
                loadDemoData();
            }
            loadCommandCenter();
        }

        const DEGREE_LABELS = { phd: '博士', master: '硕士', bachelor: '本科', postdoc: '博士后' };
        const STATUS_LABELS = { active: '在读/在站', graduated: '已毕业', suspended: '暂停', visiting: '访问' };
        const ROLE_DEGREE = { teacher: '导师', researcher: '博士', pm: '项目经理', student: '硕士' };

        function mapStudent(s) {
            const scores = s.latest_scores?.length
                ? s.latest_scores.map(sc => sc.score)
                : (s.scores || null);
            return {
                ...s,
                name: s.name || s.display_name || s.username || '',
                degree_label: DEGREE_LABELS[s.degree_type] || ROLE_DEGREE[s.project_role] || s.degree_type || '',
                status_label: STATUS_LABELS[s.status] || (s.is_active !== false ? '活跃' : '非活跃'),
                tags: s.tags || (s.research_area ? s.research_area.split(/[,，\s]+/).slice(0, 4) : []),
                scores,
            };
        }

        // Map CoEvo member to student shape
        function mapCoevoMember(m) {
            return {
                ...m,
                name: m.display_name || m.username || '',
                degree_label: ROLE_DEGREE[m.project_role] || '成员',
                status: m.is_active !== false ? 'active' : 'inactive',
                status_label: m.is_active !== false ? '活跃' : '非活跃',
                research_area: m.bio || m.research_area || '',
                tags: m.bio ? m.bio.split(/[,，、\s]+/).slice(0, 4).filter(Boolean) : [],
                scores: null,
                // extra CoEvo info shown in detail panel
                quarterly_goal: m.quarterly_goal,
                short_term_goal: m.short_term_goal,
                project_name: m.project_name,
                project_role: m.project_role,
            };
        }

        async function loadStudents() {
            try {
                // Try CoEvo members first (real prod data)
                const coevoData = await api('/coevo/members');
                if (coevoData && coevoData.length > 0) {
                    students.value = coevoData.map(mapCoevoMember);
                } else {
                    // Fall back to openclaw students
                    const data = await api('/students');
                    students.value = data.map(mapStudent);
                }
            } catch {
                try {
                    const data = await api('/students');
                    students.value = data.map(mapStudent);
                } catch {
                    loadDemoStudents();
                }
            }
            await nextTick();
            students.value.forEach(s => {
                if (s.scores) Charts.miniRadar('mini-radar-' + s.id, s.scores);
            });
        }

        async function loadCollaborations() {
            try {
                // Try CoEvo collab recommendations first
                const coevoCollabs = await api('/coevo/collaborations');
                if (coevoCollabs && coevoCollabs.length > 0) {
                    collaborations.value = coevoCollabs.map(c => ({
                        ...c,
                        student_a: c.student_a || c.requester_name || '',
                        student_b: c.student_b || (c.target_names || [])[0] || '',
                        complementarity_score: c.complementarity_score || 0.8,
                        research_idea: c.collaboration_direction || c.research_idea || '',
                        rationale: c.collaboration_suggestion || c.rationale || '',
                        status: c.status === 'completed' ? 'accepted' : c.status,
                        status_label: { completed: '已完成', pending: '处理中', generating: '生成中', failed: '失败' }[c.status] || c.status,
                    }));
                    // Build network from CoEvo collabs
                    await nextTick();
                    const nodeMap = {};
                    const edges = [];
                    collaborations.value.forEach(c => {
                        if (c.student_a) nodeMap[c.student_a] = { name: c.student_a, category: 0, score: 7, area: '' };
                        if (c.student_b) nodeMap[c.student_b] = { name: c.student_b, category: 0, score: 7, area: '' };
                        if (c.student_a && c.student_b) {
                            edges.push({ source: c.student_a, target: c.student_b, value: c.complementarity_score, idea: c.research_idea?.slice(0, 50) || '' });
                        }
                    });
                    Charts.network('collab-network', Object.values(nodeMap), edges);
                    return;
                }
                // Fall back to openclaw collaborations
                const [netData, listData] = await Promise.all([
                    api('/collaborations/network'),
                    api('/collaborations'),
                ]);
                collaborations.value = listData.map(c => ({
                    ...c,
                    student_a: c.student_a?.name || c.student_a_id,
                    student_b: c.student_b?.name || c.student_b_id,
                    status_label: { suggested: '待确认', accepted: '已接受', in_progress: '进行中', completed: '已完成', dismissed: '已忽略' }[c.status] || c.status,
                }));
                await nextTick();
                Charts.network('collab-network', netData.nodes || [], netData.edges || []);
            } catch {
                loadDemoCollaborations();
            }
        }

        async function loadDirections() {
            try {
                // Try CoEvo research plans as primary source
                const [coevoPlans, ocDirs] = await Promise.allSettled([
                    api('/coevo/research-plans'),
                    api('/directions'),
                ]);
                const planItems = (coevoPlans.status === 'fulfilled' ? coevoPlans.value : []) || [];
                const dirItems = (ocDirs.status === 'fulfilled' ? ocDirs.value : []) || [];
                // Merge: openclaw directions first, then CoEvo plans (tagged differently)
                const merged = [
                    ...dirItems,
                    ...planItems.map(p => ({ ...p, _source: 'coevo' })),
                ];
                directions.value = merged.length > 0 ? merged : [];
                if (merged.length === 0) { loadDemoDirections(); return; }
                await nextTick();
                const tree = buildDirectionTree(merged);
                Charts.directionTree('direction-mindmap', tree);
            } catch {
                loadDemoDirections();
            }
        }

        async function loadMeetings() {
            try {
                // Try CoEvo meetings first (real prod data)
                const coevoMtgs = await api('/coevo/meetings?limit=30');
                if (coevoMtgs && coevoMtgs.length > 0) {
                    meetings.value = coevoMtgs.map(m => ({
                        ...m,
                        title: m.meeting_name || '会议',
                        meeting_type: 'group',
                        type_label: '组会',
                        date_display: (m.meeting_time || '').slice(0, 16),
                        summary: m.overall_summary || '',
                        action_items: [],
                        status: m.status,
                        openclaw_insight_count: 0,
                        _source: 'coevo',
                    }));
                    return;
                }
                // Fall back to openclaw meetings
                const data = await api('/meetings');
                meetings.value = data;
            } catch {
                try {
                    const data = await api('/meetings');
                    meetings.value = data;
                } catch {
                    loadDemoMeetings();
                }
            }
        }

        function buildDirectionTree(dirs) {
            const root = {
                name: '研究方向',
                children: dirs.filter(d => !d.parent_id).map(d => ({
                    name: d.title,
                    value: d.priority,
                    itemStyle: {
                        color: d.source === 'ai_suggested' ? '#a855f7' :
                               d.source === 'pi_defined' ? '#DC3545' : '#0ea5e9',
                    },
                    children: dirs.filter(c => c.parent_id === d.id).map(c => ({
                        name: c.title,
                        value: c.priority,
                    })),
                })),
            };
            return root;
        }

        // ── Actions ──
        function selectStudent(student) {
            selectedStudent.value = student;
            nextTick(() => {
                if (student.scores) {
                    const dims = [
                        { label: '文献功底' }, { label: '实验设计' }, { label: '数据分析' },
                        { label: '编程能力' }, { label: '学术写作' }, { label: '汇报表达' },
                        { label: '创新思维' }, { label: '协作能力' },
                    ];
                    Charts.radar('detail-radar', dims, student.scores, student.prev_scores);
                }
                if (student.events) {
                    Charts.timeline('detail-timeline', student.events);
                }
            });
        }

        async function refreshGuidance(student) {
            const msg = `请为学生${student.name}制定个性化指导建议`;
            try {
                const res = await api('/chat', { method: 'POST', body: { message: msg, user_id: 'web:pi' } });
                pollResult(res.task_id, (result) => {
                    student.guidance = result;
                });
            } catch { /* ignore */ }
        }

        async function generateCollaborations() {
            await sendChat('分析所有学生的互补能力，推荐合作关系');
            setTimeout(loadCollaborations, 3000);
        }

        async function discoverDirections() {
            await sendChat('分析最近的会议议题，结合全球研究趋势，发现新的研究方向');
            setTimeout(loadDirections, 3000);
        }

        async function runBenchmark() {
            await sendChat('调研全球类似研究团队，进行对比分析');
        }

        function selectMeeting(meeting) {
            selectedMeeting.value = meeting;
        }

        async function createStudent() {
            if (!newStudent.name.trim()) return;
            studentSubmitting.value = true;
            try {
                const payload = {
                    name: newStudent.name.trim(),
                    email: newStudent.email || undefined,
                    degree_type: newStudent.degree_type,
                    research_area: newStudent.research_area || undefined,
                    enrollment_date: newStudent.enrollment_date || undefined,
                    feishu_open_id: newStudent.feishu_open_id || undefined,
                    bio: newStudent.bio || undefined,
                };
                await api('/students', { method: 'POST', body: payload });
                showAddStudent.value = false;
                Object.assign(newStudent, { name: '', email: '', degree_type: 'phd', research_area: '', enrollment_date: '', feishu_open_id: '', bio: '' });
                await loadStudents();
            } catch (e) {
                alert('添加失败: ' + e.message);
            } finally {
                studentSubmitting.value = false;
            }
        }

        async function createMeeting() {
            if (!newMeeting.meeting_type || !newMeeting.meeting_date) return;
            meetingSubmitting.value = true;
            try {
                const attendees = newMeeting.attendees_text
                    ? newMeeting.attendees_text.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
                    : [];
                const payload = {
                    title: newMeeting.title || undefined,
                    meeting_type: newMeeting.meeting_type,
                    meeting_date: newMeeting.meeting_date,
                    attendees: attendees.length ? attendees : undefined,
                    raw_notes: newMeeting.raw_notes || undefined,
                };
                await api('/meetings', { method: 'POST', body: payload });
                showAddMeeting.value = false;
                Object.assign(newMeeting, { title: '', meeting_type: 'group', meeting_date: '', attendees_text: '', raw_notes: '' });
                await loadMeetings();
            } catch (e) {
                alert('添加失败: ' + e.message);
            } finally {
                meetingSubmitting.value = false;
            }
        }

        async function createDirection() {
            if (!newDirection.title.trim()) return;
            directionSubmitting.value = true;
            try {
                const payload = {
                    title: newDirection.title.trim(),
                    description: newDirection.description || undefined,
                    source: newDirection.source,
                    status: newDirection.status,
                    evidence: newDirection.evidence || undefined,
                };
                await api('/directions', { method: 'POST', body: payload });
                showAddDirection.value = false;
                Object.assign(newDirection, { title: '', description: '', source: 'pi_defined', status: 'exploring', evidence: '' });
                await loadDirections();
            } catch (e) {
                alert('添加失败: ' + e.message);
            } finally {
                directionSubmitting.value = false;
            }
        }

        function handleInsight(insight) {
            currentTab.value = 'chat';
            nextTick(() => sendChat(insight.action || insight.text));
        }

        // ── Chat ──
        // ── 任务状态追踪（task_id → pending assistant message） ──
        const _pendingTasks = new Map(); // task_id → { msgIdx, timerId, eta, etaInterval }

        async function sendChat(text) {
            if (!text?.trim()) return;
            const msg = text.trim();
            chatInput.value = '';
            chatMessages.value.push({
                role: 'user',
                content: msg,
                time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
            });
            chatLoading.value = true;
            scrollChat();

            // 先推入一个占位 assistant 消息
            const placeholderIdx = chatMessages.value.length;
            chatMessages.value.push({
                role: 'assistant',
                content: null,
                task_id: null,
                status: 'submitting',
                queue_position: null,
                eta: null,
                eta_countdown: null,
                progress: null,
                skill: null,
                time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
            });
            scrollChat();

            try {
                const res = await api('/chat', {
                    method: 'POST',
                    body: { message: msg, user_id: 'web:pi', source: 'web' },
                });

                // 更新占位消息
                const placeholder = chatMessages.value[placeholderIdx];
                placeholder.task_id = res.task_id;
                placeholder.status = 'queued';
                placeholder.queue_position = res.queue_position;
                placeholder.eta = res.estimated_wait_seconds;
                placeholder.eta_countdown = res.estimated_wait_seconds;

                // 启动 ETA 倒计时
                const etaInterval = setInterval(() => {
                    if (placeholder.eta_countdown > 0) placeholder.eta_countdown--;
                }, 1000);

                // 注册 pending task（WS 推送时使用）
                _pendingTasks.set(res.task_id, { msgIdx: placeholderIdx, etaInterval });

                // 同时保持轮询兜底（WS 可能丢失）
                _pollResultFallback(res.task_id, placeholderIdx, etaInterval);

            } catch (e) {
                const placeholder = chatMessages.value[placeholderIdx];
                placeholder.status = 'failed';
                placeholder.content = '抱歉，提交请求时出现错误，请稍后重试。';
                chatLoading.value = false;
                scrollChat();
            }
        }

        function _resolveTask(taskId, result) {
            const entry = _pendingTasks.get(taskId);
            if (!entry) return;
            _pendingTasks.delete(taskId);
            clearInterval(entry.etaInterval);

            const msg = chatMessages.value[entry.msgIdx];
            if (!msg) return;
            if (result.status === 'failed' || result.error_message) {
                msg.status = 'failed';
                msg.content = result.error_message || '任务处理失败，请重试。';
            } else {
                msg.status = 'completed';
                msg.content = result.result_summary || result.summary || '（无返回内容）';
                msg.skill = result.skill_used;
                msg.duration_ms = result.duration_ms;
            }
            msg.progress = null;
            msg.eta_countdown = null;
            if (_pendingTasks.size === 0) chatLoading.value = false;
            scrollChat();
        }

        function _pollResultFallback(taskId, msgIdx, etaInterval, retries = 60) {
            let count = 0;
            const timer = setInterval(async () => {
                // WS already resolved
                if (!_pendingTasks.has(taskId)) { clearInterval(timer); return; }
                count++;
                if (count > retries) {
                    clearInterval(timer);
                    _resolveTask(taskId, { status: 'failed', error_message: '等待超时，请在"任务日志"查看结果。' });
                    return;
                }
                try {
                    const res = await api(`/chat/result/${taskId}`);
                    if (res.status === 'completed' || res.status === 'failed') {
                        clearInterval(timer);
                        _resolveTask(taskId, res);
                    }
                } catch { /* ignore */ }
            }, 2000);
        }

        // Legacy pollResult kept for non-chat uses
        function pollResult(taskId, callback, retries = 30) {
            let count = 0;
            const interval = setInterval(async () => {
                count++;
                if (count > retries) {
                    clearInterval(interval);
                    callback({ summary: '任务超时，请稍后重试。' });
                    return;
                }
                try {
                    const res = await api(`/chat/result/${taskId}`);
                    if (res.status === 'completed' || res.status === 'failed') {
                        clearInterval(interval);
                        callback(res.status === 'completed' ? res : { summary: res.error_message || '任务失败' });
                    }
                } catch { /* continue polling */ }
            }, 1000);
        }

        function scrollChat() {
            nextTick(() => {
                const el = chatContainer.value;
                if (el) el.scrollTop = el.scrollHeight;
            });
        }

        // ── System Status Polling ──
        async function pollSystemStatus() {
            try {
                const data = await api('/system/status');
                systemOk.value = data.ok !== false;
                workerInfo.value = `Workers: ${data.busy || 0}/${data.total || 0}`;
            } catch {
                systemOk.value = false;
            }
        }

        // ── Demo Data (used when API not yet available) ──
        function loadDemoData() {
            dashboardStats.value = [
                { icon: '👥', label: '活跃成员',   value: 12, trend: 8 },
                { icon: '📄', label: '在投论文',   value: 5,  trend: 25 },
                { icon: '🧭', label: '研究方向',   value: 7,  trend: 14 },
                { icon: '🤖', label: '本周任务',   value: 34, trend: 12 },
            ];
            recentEvents.value = [
                { type: 'paper_accepted', title: 'NeurIPS 2026 论文录用', student: '张三', date: '2天前', type_label: '论文录用' },
                { type: 'experiment_completed', title: '大规模对比实验完成', student: '李四', date: '3天前', type_label: '实验完成' },
                { type: 'milestone_reached', title: '开题报告通过', student: '王五', date: '5天前', type_label: '里程碑' },
                { type: 'presentation', title: 'ICML Workshop 口头报告', student: '赵六', date: '1周前', type_label: '学术报告' },
                { type: 'code_released', title: 'GitHub 开源项目发布', student: '张三', date: '1周前', type_label: '代码发布' },
            ];
            aiInsights.value = [
                { category: 'collab', category_label: '合作建议', text: '张三和李四在图神经网络和生物信息学领域有很强的互补性，建议合作探索GNN在蛋白质结构预测上的应用', date: '今天', action: '推荐张三和李四的合作方向' },
                { category: 'trend', category_label: '前沿趋势', text: 'AI for Science 领域近期出现多篇高影响力论文，与课题组方向高度相关', date: '今天', action: '分析AI for Science最新趋势' },
                { category: 'guidance', category_label: '指导建议', text: '王五的学术写作能力近期提升明显，建议鼓励独立投稿一篇Workshop论文', date: '昨天', action: '为王五制定指导计划' },
            ];
            workerInfo.value = 'Workers: 2/3';

            nextTick(() => {
                Charts.directionTree('direction-tree', {
                    name: '研究方向',
                    children: [
                        { name: '大语言模型', itemStyle: { color: '#DC3545' }, children: [
                            { name: 'LLM推理优化' }, { name: '多模态理解' }, { name: '长文本建模' },
                        ]},
                        { name: 'AI for Science', itemStyle: { color: '#0ea5e9' }, children: [
                            { name: '蛋白质结构预测' }, { name: '分子生成' },
                        ]},
                        { name: '图神经网络', itemStyle: { color: '#a855f7' }, children: [
                            { name: '动态图学习' }, { name: '知识图谱推理' },
                        ]},
                    ],
                });
            });
        }

        function loadDemoStudents() {
            students.value = [
                { id: 1, name: '张三', degree_label: '博士三年级', research_area: 'LLM推理优化', status: 'active', status_label: '在读', tags: ['NeurIPS', 'LLM', 'Reasoning'], scores: [8, 7, 9, 9, 7, 6, 8, 7], prev_scores: [6, 6, 7, 8, 5, 5, 7, 6], events: [
                    { event_date: '2026-01-15', event_type: 'paper_accepted', title: 'NeurIPS 2026 录用' },
                    { event_date: '2025-11-20', event_type: 'experiment_completed', title: '推理加速实验完成' },
                    { event_date: '2025-09-01', event_type: 'paper_submitted', title: 'ACL 2026 投稿' },
                    { event_date: '2025-06-15', event_type: 'presentation', title: '组会汇报: Chain-of-Thought' },
                ]},
                { id: 2, name: '李四', degree_label: '博士二年级', research_area: '生物信息学 + GNN', status: 'active', status_label: '在读', tags: ['BioInfo', 'GNN', 'Protein'], scores: [7, 8, 8, 6, 6, 7, 7, 8], events: [] },
                { id: 3, name: '王五', degree_label: '硕士二年级', research_area: '多模态理解', status: 'active', status_label: '在读', tags: ['Multimodal', 'Vision', 'Language'], scores: [6, 5, 7, 7, 8, 8, 6, 7], events: [] },
                { id: 4, name: '赵六', degree_label: '博士后', research_area: '知识图谱推理', status: 'active', status_label: '在站', tags: ['KG', 'Reasoning', 'NLP'], scores: [9, 7, 8, 7, 9, 9, 8, 6], events: [] },
                { id: 5, name: '钱七', degree_label: '博士一年级', research_area: '分子生成', status: 'active', status_label: '在读', tags: ['MolGen', 'Drug', 'AI4Science'], scores: [5, 6, 6, 5, 4, 5, 7, 8], events: [] },
                { id: 6, name: '孙八', degree_label: '硕士一年级', research_area: '动态图学习', status: 'active', status_label: '在读', tags: ['DynGraph', 'Temporal', 'GNN'], scores: [4, 4, 5, 6, 3, 4, 6, 7], events: [] },
            ];
            nextTick(() => {
                students.value.forEach(s => {
                    if (s.scores) Charts.miniRadar('mini-radar-' + s.id, s.scores);
                });
            });
        }

        function loadDemoCollaborations() {
            const nodes = [
                { name: '张三', category: 0, score: 8, area: 'LLM推理优化' },
                { name: '李四', category: 0, score: 7, area: '生物信息学+GNN' },
                { name: '王五', category: 1, score: 6.5, area: '多模态理解' },
                { name: '赵六', category: 2, score: 8.5, area: '知识图谱推理' },
                { name: '钱七', category: 0, score: 5.5, area: '分子生成' },
                { name: '孙八', category: 1, score: 5, area: '动态图学习' },
            ];
            const edges = [
                { source: '张三', target: '李四', value: 0.85, idea: 'GNN+LLM融合的蛋白质功能预测' },
                { source: '张三', target: '赵六', value: 0.72, idea: '知识增强的推理链优化' },
                { source: '李四', target: '钱七', value: 0.9, idea: 'AI驱动的药物分子设计' },
                { source: '王五', target: '赵六', value: 0.65, idea: '多模态知识图谱构建' },
                { source: '王五', target: '张三', value: 0.6, idea: '多模态推理能力评估' },
                { source: '孙八', target: '李四', value: 0.78, idea: '时序蛋白质交互网络建模' },
            ];
            collaborations.value = edges.map((e, i) => ({
                id: i + 1,
                student_a: e.source,
                student_b: e.target,
                complementarity_score: e.value,
                research_idea: e.idea,
                status: ['suggested', 'accepted', 'in_progress'][i % 3],
                status_label: ['待确认', '已接受', '进行中'][i % 3],
            }));
            nextTick(() => Charts.network('collab-network', nodes, edges));
        }

        function loadDemoDirections() {
            directions.value = [
                { id: 1, title: '大语言模型推理优化', description: '研究如何提升LLM的推理效率和准确性', source: 'pi_defined', source_label: 'PI定义', status: 'active', status_label: '进行中', priority: 1, parent_id: null, related_student_names: ['张三', '赵六'] },
                { id: 2, title: 'AI for Science', description: '将AI技术应用于科学发现', source: 'pi_defined', source_label: 'PI定义', status: 'active', status_label: '进行中', priority: 2, parent_id: null, related_student_names: ['李四', '钱七'] },
                { id: 3, title: '多模态知识图谱', description: '融合视觉和语言信息构建知识表示', source: 'ai_suggested', source_label: 'AI建议', status: 'exploring', status_label: '探索中', priority: 3, parent_id: null, evidence: '基于王五和赵六的研究交叉点，结合最新ICLR论文趋势发现的新方向', related_student_names: ['王五', '赵六'] },
                { id: 4, title: 'GNN+LLM融合架构', description: '将图神经网络与大语言模型深度融合', source: 'meeting_derived', source_label: '会议衍生', status: 'exploring', status_label: '探索中', priority: 4, parent_id: null, evidence: '来自第42次组会的讨论议题排列组合分析', related_student_names: ['张三', '李四', '孙八'] },
            ];
            nextTick(() => {
                Charts.directionTree('direction-mindmap', buildDirectionTree(directions.value));
            });
        }

        function loadDemoMeetings() {
            meetings.value = [
                { id: 1, title: '第45次组会：LLM推理新进展', meeting_type: 'group', type_label: '组会', date_display: '2026-03-10 14:00', summary: '讨论了Chain-of-Thought推理的最新进展，张三汇报了NeurIPS投稿结果...', action_items: [{task: '整理实验数据', assignee: '张三'}, {task: '补充对比实验', assignee: '李四'}] },
                { id: 2, title: '张三一对一指导', meeting_type: 'individual', type_label: '一对一', date_display: '2026-03-08 10:00', summary: '讨论了博士论文框架和下一步实验计划...', action_items: [{task: '完成论文第三章', assignee: '张三'}] },
                { id: 3, title: '跨领域研讨会：AI+Bio', meeting_type: 'seminar', type_label: '研讨会', date_display: '2026-03-05 15:00', summary: '邀请了生物系的教授讨论AI在蛋白质研究中的应用前景...', action_items: [] },
            ];
        }

        // ── WebSocket for real-time updates ──
        let ws = null;
        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

            ws.onmessage = (event) => {
                let data;
                try { data = JSON.parse(event.data); } catch { return; }

                if (data.type === 'task_update') {
                    // Task completed/failed — resolve the pending message
                    if (data.task_id) _resolveTask(data.task_id, data);
                    loadDashboard();

                } else if (data.type === 'task_progress') {
                    // Step-by-step progress from worker
                    const entry = _pendingTasks.get(data.task_id);
                    if (entry) {
                        const msg = chatMessages.value[entry.msgIdx];
                        if (msg) {
                            msg.status = 'processing';
                            msg.progress = {
                                step: data.step,
                                detail: data.detail,
                                percent: data.percent,
                            };
                        }
                    }

                } else if (data.type === 'task_queued') {
                    // Queue position update (re-queue or initial queue from gateway)
                    const entry = _pendingTasks.get(data.task_id);
                    if (entry) {
                        const msg = chatMessages.value[entry.msgIdx];
                        if (msg) {
                            msg.queue_position = data.queue_position;
                            if (data.estimated_wait_seconds) {
                                msg.eta = data.estimated_wait_seconds;
                                msg.eta_countdown = data.estimated_wait_seconds;
                            }
                        }
                    }

                } else if (data.type === 'worker_status') {
                    workerInfo.value = `Workers: ${data.busy}/${data.total}`;
                }
            };

            ws.onopen = () => logger.debug?.('WS connected');
            ws.onclose = () => setTimeout(connectWebSocket, 5000);
        }

        // ── Tab watchers ──
        watch(currentTab, async (tab) => {
            switch (tab) {
                case 'projects':  loadProjectsTab(); break;
                case 'members':   loadStudents(); break;
                case 'network':   loadNetworkData(); break;
                case 'directions': loadDirectionsTab(); break;
                case 'meetings':  loadMeetings(); break;
                case 'benchmark':
                    await Promise.allSettled([loadInnovationIndex(), loadTeamHealth()]);
                    await nextTick();
                    if (teamHealth.value) renderHealthGauge();
                    break;
                case 'data':
                    stopFeishuAutoRefresh();
                    loadCoevoTab();
                    break;
                case 'scheduler':
                    stopFeishuAutoRefresh();
                    loadSchedulerJobs();
                    break;
                case 'chat':
                    stopFeishuAutoRefresh();
                    break;
                default:
                    stopFeishuAutoRefresh();
                    break;
            }
        });

        watch(directionsSubView, async (v) => {
            await nextTick();
            if (v === 'graph') initDirectionGraph();
            if (v === 'international') loadCoevoMomentum();
        });

        // ── Knowledge Graph Management ──
        const knowledgeTab = ref('search');      // search | entity | stats
        const knowledgeSearchQ = ref('');
        const knowledgeResults = ref([]);
        const knowledgeLoading = ref(false);
        const knowledgeStats = ref(null);
        const knowledgeEntityId = ref('');
        const knowledgeProfile = ref('');
        const knowledgeSubGraph = ref({ nodes: [], edges: [] });
        const newKnowledgeNode = ref({ entity_type: 'person', entity_id: '', title: '', content: '', importance: 50 });
        const knowledgeNodeSaving = ref(false);

        async function searchKnowledge() {
            if (!knowledgeSearchQ.value.trim()) return;
            knowledgeLoading.value = true;
            try {
                const r = await api(`/api/knowledge/search?q=${encodeURIComponent(knowledgeSearchQ.value)}&k=12`);
                knowledgeResults.value = r.results || [];
            } catch (e) {
                showToast('知识检索失败: ' + (e.message || e));
            } finally {
                knowledgeLoading.value = false;
            }
        }

        async function loadKnowledgeStats() {
            try {
                knowledgeStats.value = await api('/api/knowledge/stats');
            } catch (e) {
                console.error('loadKnowledgeStats failed', e);
            }
        }

        async function loadEntityProfile() {
            if (!knowledgeEntityId.value.trim()) return;
            knowledgeLoading.value = true;
            try {
                const r = await api(`/api/knowledge/entity/${encodeURIComponent(knowledgeEntityId.value)}`);
                knowledgeProfile.value = r.profile_markdown || '（无数据）';
                knowledgeResults.value = r.nodes || [];
            } catch (e) {
                showToast('加载画像失败: ' + (e.message || e));
            } finally {
                knowledgeLoading.value = false;
            }
        }

        async function loadEntityGraph() {
            if (!knowledgeEntityId.value.trim()) return;
            try {
                knowledgeSubGraph.value = await api(`/api/knowledge/graph/${encodeURIComponent(knowledgeEntityId.value)}`);
            } catch (e) {
                showToast('加载子图失败: ' + (e.message || e));
            }
        }

        async function addKnowledgeNode() {
            const n = newKnowledgeNode.value;
            if (!n.entity_id || !n.title || !n.content) {
                showToast('请填写实体ID、标题和内容');
                return;
            }
            knowledgeNodeSaving.value = true;
            try {
                await api('/api/knowledge/nodes', { method: 'POST', body: n });
                showToast('知识节点已保存');
                newKnowledgeNode.value = { entity_type: 'person', entity_id: '', title: '', content: '', importance: 50 };
                await loadKnowledgeStats();
            } catch (e) {
                showToast('保存失败: ' + (e.message || e));
            } finally {
                knowledgeNodeSaving.value = false;
            }
        }

        watch(knowledgeTab, async (v) => {
            if (v === 'stats') await loadKnowledgeStats();
        });

        // ── Scheduler Management ──
        const schedulerJobs = ref([]);
        const schedulerLoading = ref(false);
        const schedulerEditJob = ref(null);       // job being edited
        const schedulerEditCron = ref('');
        const schedulerEditDesc = ref('');
        const schedulerSaving = ref(false);

        const CRON_PRESETS = [
            { label: '每天 06:00',       value: '0 6 * * *' },
            { label: '每天 02:00',       value: '0 2 * * *' },
            { label: '每天 03:00',       value: '0 3 * * *' },
            { label: '每天 08:00',       value: '0 8 * * *' },
            { label: '每周一 03:00',     value: '0 3 * * 1' },
            { label: '每周一 06:00',     value: '0 6 * * 1' },
            { label: '每周日 03:00',     value: '0 3 * * 0' },
            { label: '每小时',           value: '0 * * * *' },
            { label: '每 6 小时',        value: '0 */6 * * *' },
            { label: '每 12 小时',       value: '0 */12 * * *' },
        ];

        async function loadSchedulerJobs() {
            schedulerLoading.value = true;
            try {
                schedulerJobs.value = await api('/system/scheduler/jobs');
            } catch (e) {
                showToast('获取调度任务失败: ' + (e.message || e));
            } finally {
                schedulerLoading.value = false;
            }
        }

        function openSchedulerEdit(job) {
            schedulerEditJob.value = job;
            schedulerEditCron.value = job.cron;
            schedulerEditDesc.value = job.description || '';
        }

        function closeSchedulerEdit() {
            schedulerEditJob.value = null;
        }

        async function saveSchedulerJob() {
            if (!schedulerEditJob.value) return;
            schedulerSaving.value = true;
            try {
                await api(`/system/scheduler/jobs/${schedulerEditJob.value.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cron: schedulerEditCron.value,
                        description: schedulerEditDesc.value || null,
                    }),
                });
                showToast(`✅ 任务 ${schedulerEditJob.value.id} 已更新`);
                closeSchedulerEdit();
                await loadSchedulerJobs();
            } catch (e) {
                showToast('保存失败: ' + (e.message || e));
            } finally {
                schedulerSaving.value = false;
            }
        }

        async function triggerSchedulerJob(jobId) {
            try {
                await api(`/system/scheduler/jobs/${jobId}/trigger`, { method: 'POST' });
                showToast(`▶️ 任务 ${jobId} 已立即触发`);
            } catch (e) {
                showToast('触发失败: ' + (e.message || e));
            }
        }

        async function pauseSchedulerJob(jobId) {
            try {
                await api(`/system/scheduler/jobs/${jobId}/pause`, { method: 'POST' });
                showToast(`⏸ 任务 ${jobId} 已暂停`);
                await loadSchedulerJobs();
            } catch (e) {
                showToast('暂停失败: ' + (e.message || e));
            }
        }

        async function resumeSchedulerJob(jobId) {
            try {
                await api(`/system/scheduler/jobs/${jobId}/resume`, { method: 'POST' });
                showToast(`▶️ 任务 ${jobId} 已恢复`);
                await loadSchedulerJobs();
            } catch (e) {
                showToast('恢复失败: ' + (e.message || e));
            }
        }

        // ── Init ──
        onMounted(() => {
            loadProjectsTab();
            connectWebSocket();
            setInterval(pollSystemStatus, 15000);
        });

        return {
            tabs, currentTab,
            systemOk, workerInfo,
            dashboardStats, recentEvents, aiInsights, eventIcons, eventColors,
            students, selectedStudent, showAddStudent, newStudent, studentSubmitting, selectStudent, refreshGuidance, createStudent,
            collaborations, generateCollaborations,
            directions, showAddDirection, newDirection, directionSubmitting, discoverDirections, createDirection,
            meetings, showAddMeeting, newMeeting, meetingSubmitting, selectMeeting, selectedMeeting, createMeeting,
            feishuLogs, feishuFilter, feishuAutoRefresh, loadFeishuLogs, STATUS_COLORS, SOURCE_ICONS,
            chatMessages, chatInput, chatLoading, chatContainer, chatHints, sendChat,
            handleInsight, runBenchmark,
            // New: Projects / Directions / Network / Benchmark
            projectsOverview, directionClusters, directionIdeas, pendingIdeasCount,
            directionsSubView, selectedCluster, directionAnalyzing,
            networkData, selectedNetworkNode,
            innovationIndex,
            dataSubTab,
            toastMessage, toastVisible,
            showToast, triggerDirectionAnalyze, activateIdea, dismissIdea,
            // CoEvo integration
            coevoOverview, coevoProjects, coevoStudents, coevoMeetings,
            coevoBlockers, coevoEngagement, coevoMomentum, coevoSyncLogs,
            coevoSelectedProject, coevoSelectedStudent, coevoSelectedMeeting,
            coevoActiveView, coevoLoading, coevoSyncing,
            selectCoevoProject, selectCoevoStudent, selectCoevoMeeting,
            triggerCoevoSync, loadCoevoBlockers, loadCoevoEngagement,
            loadCoevoMomentum, renderEngagementChart,
            // Command Center
            teamHealth, riskDashboard, actionItems, studentNarrative, riskComputing,
            loadTeamHealth, loadRiskDashboard, loadActionItems,
            triggerRiskCompute, loadStudentNarrative, loadCommandCenter,
            // Scheduler
            schedulerJobs, schedulerLoading, schedulerEditJob, schedulerEditCron, schedulerEditDesc,
            schedulerSaving, CRON_PRESETS,
            loadSchedulerJobs, openSchedulerEdit, closeSchedulerEdit, saveSchedulerJob,
            triggerSchedulerJob, pauseSchedulerJob, resumeSchedulerJob,
            // Knowledge Graph
            knowledgeSearchQ, knowledgeResults, knowledgeLoading, knowledgeStats,
            knowledgeEntityId, knowledgeProfile, knowledgeSubGraph,
            knowledgeTab, searchKnowledge, loadKnowledgeStats,
            loadEntityProfile, loadEntityGraph, addKnowledgeNode,
            newKnowledgeNode, knowledgeNodeSaving,
        };
    },
});

app.mount('#app');
