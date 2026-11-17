<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div><h2>运行监控</h2><p class="page-desc">展示各采集任务的实时运行指标、成功率和资源占用。</p></div>
    </header>
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>
    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
        <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
        <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>

    <section class="comp-panel" data-testid="completeness-panel">
      <header class="page-head">
        <div>
          <h3>监控完整度数据报告</h3>
          <p class="page-desc">
            按期望观测窗口计算完整度，缺失观测、零流量、正常停机分别可识别，缺数不记零值。
            <span v-if="!session.canOperate" class="error-text">当前账户权限不足，仅可查看。</span>
          </p>
        </div>
        <div v-if="session.canOperate" class="page-actions">
          <button class="btn" type="button" data-testid="version-rollback" :disabled="!report" @click="switchVersion('回退')">回退版本</button>
          <button class="btn" type="button" data-testid="version-switch" :disabled="!report" @click="switchVersion('切换')">切换新版本</button>
          <button class="btn" type="button" data-testid="verify-completeness" :disabled="!report" @click="verifyReport">核对完整度</button>
        </div>
      </header>

      <form v-if="session.canOperate" class="analysis-form" data-testid="completeness-form" @submit.prevent="runCompleteness">
        <label><span>请求标识（同一请求只记一次）</span>
          <input v-model="runForm.请求标识" data-testid="request-id" placeholder="请输入请求标识" />
        </label>
        <label><span>故障注入</span>
          <select v-model="runForm.故障注入" data-testid="fault-inject">
            <option>无</option>
            <option>采集中断</option>
          </select>
        </label>
        <button class="btn primary" type="submit" data-testid="run-completeness">生成完整度报告</button>
        <button class="btn ghost" type="button" data-testid="mint-request-id" @click="mintRequestId">重新生成标识</button>
        <button v-if="completeness.可重试" class="btn" type="button" data-testid="retry-completeness" @click="retryCompleteness">重试</button>
      </form>

      <p v-if="completeness.失败原因" class="error-text" data-testid="failure-reason">
        最近生成失败：{{ completeness.失败原因 }}<template v-if="completeness.可重试">，可点击重试。</template>
      </p>
      <p v-if="completeness.恢复说明" class="comp-note" data-testid="recovery-note">恢复说明：{{ completeness.恢复说明 }}</p>

      <div v-if="completeness.空态" class="comp-empty" data-testid="completeness-empty">
        <p v-if="completeness.状态 === '失败'">报告生成失败：{{ completeness.失败原因 }}（支持重试，恢复后保留原因说明）</p>
        <p v-else>暂无监控完整度数据，请生成完整度报告。</p>
      </div>

      <template v-else-if="report">
        <div class="stat-row">
          <article class="stat-card">
            <span class="stat-label">报告编号 / 数据版本</span>
            <strong class="stat-value" data-testid="report-version">{{ report.报告编号 }} · V{{ report.数据版本 }}</strong>
          </article>
          <article v-for="s in summaryCards" :key="s.label" class="stat-card">
            <span class="stat-label">{{ s.label }}</span>
            <strong class="stat-value">{{ s.value }}</strong>
          </article>
        </div>
        <p class="comp-note">
          观测时段范围：{{ report.观测时段范围 }} · 生成时间：{{ report.生成时间 }} · 生成人：{{ report.生成人 }}
          <template v-if="report.核对人"> · 核对人：{{ report.核对人 }}（{{ report.核对时间 }}）</template>
          <template v-else> · 待值班管理员核对</template>
        </p>

        <table class="data-table" data-testid="window-table">
          <thead>
            <tr>
              <th>窗口编号</th><th>期望观测窗口</th><th>实际收到时间</th><th>窗口状态</th>
              <th>观测速率</th><th>完整度</th><th>原因说明</th>
              <th v-if="session.canOperate">窗口事件</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="w in report.windows" :key="w.窗口编号" :data-testid="`window-${w.窗口编号}`" :data-state="w.窗口状态">
              <td>{{ w.窗口编号 }}</td>
              <td>{{ w.期望观测窗口 }}</td>
              <td>{{ w.实际收到时间 ?? '—' }}</td>
              <td><span class="comp-badge" :class="`state-${w.窗口状态}`">{{ w.窗口状态 }}</span></td>
              <td>{{ w.观测速率 === null || w.观测速率 === undefined ? '—' : w.观测速率 }}</td>
              <td>{{ Math.round(w.完整度 * 100) }}%</td>
              <td>{{ w.原因说明 }}</td>
              <td v-if="session.canOperate" class="row-actions">
                <button class="link" type="button" :data-testid="`late-${w.窗口编号}`" @click="sendEvent(w, '迟到观测')">迟到观测</button>
                <button class="link" type="button" :data-testid="`shutdown-${w.窗口编号}`" @click="sendEvent(w, '任务停机')">任务停机</button>
                <button class="link" type="button" :data-testid="`conflict-${w.窗口编号}`" @click="sendEvent(w, '冲突批次')">冲突批次</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="timeline-wrap" style="margin-top:12px" data-testid="timeline">
          <div v-for="(t, i) in report.时间线" :key="i" class="timeline-row">
            <span class="timeline-label">{{ t.时间 }}</span>
            <span>{{ t.事件 }}（V{{ t.数据版本 }}）— 最终原因：{{ t.最终原因 }}</span>
          </div>
        </div>
      </template>

      <footer class="page-foot">
        <span>版本列表：{{ (completeness.版本列表 ?? []).join('、') || '—' }} · 当前版本 V{{ completeness.当前版本 ?? '—' }}</span>
        <span v-if="compMessage" :class="compOk ? '' : 'error-text'" data-testid="comp-message">{{ compMessage }}</span>
      </footer>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'
const ENDPOINT = '/api/monitor'
const session = useSessionStore()
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["大盘编号", "关联任务", "采集速率", "成功率", "平均延迟", "内存占用", "错误次数", "观测时段"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["大盘编号", "关联任务", "采集速率", "成功率", "平均延迟", "内存占用", "错误次数", "观测时段"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}

// 监控完整度数据报告
const completeness = ref<any>({ 空态: true, 状态: '暂无数据', 版本列表: [] })
const report = computed(() => completeness.value.报告)
const compMessage = ref('')
const compOk = ref(true)
const runForm = ref({ 请求标识: '', 故障注入: '无' })
const summaryCards = computed(() => {
  const s = report.value?.汇总
  if (!s) return []
  return [
    { label: '期望窗口数', value: s.期望窗口数 },
    { label: '正常', value: s.正常 },
    { label: '零流量', value: s.零流量 },
    { label: '缺失观测', value: s.缺失观测 },
    { label: '正常停机', value: s.正常停机 },
    { label: '整体完整度', value: `${(s.整体完整度 * 100).toFixed(1)}%` },
  ]
})
function mintRequestId() { runForm.value.请求标识 = `REQ-${Date.now()}` }
async function fetchCompleteness() {
  try { const res = await request(`${ENDPOINT}/completeness`); completeness.value = await res.json() } catch(e) {}
}
async function postCompleteness(path: string, body: Record<string, unknown>) {
  compMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/completeness/${path}`, {
      method: 'POST',
      body: JSON.stringify({ ...body, operator: session.operator, role: session.role }),
    })
    const payload = await res.json()
    compOk.value = !!payload.ok
    compMessage.value = payload.message || ''
    await fetchCompleteness()
  } catch (e) {
    compOk.value = false
    compMessage.value = e instanceof Error ? e.message : '操作失败'
  }
}
const runCompleteness = () => postCompleteness('run', { values: { ...runForm.value } })
const retryCompleteness = () => postCompleteness('retry', {})
const switchVersion = (action: string) => postCompleteness('version', { action })
const verifyReport = () => postCompleteness('verify', {})
function sendEvent(w: any, kind: string) {
  const events = kind === '冲突批次'
    ? [{ 事件类型: '迟到观测' }, { 事件类型: '任务停机', 原因: '计划内停机维护' }]
    : kind === '任务停机'
      ? [{ 事件类型: '任务停机', 原因: '计划内停机维护（值班确认）' }]
      : [{ 事件类型: '迟到观测' }]
  void postCompleteness('events', { window: w.窗口编号, events })
}
onMounted(() => { fetchSummary(); fetchCompleteness(); mintRequestId() })
</script>
<style scoped>
.comp-panel { margin-top: 20px; border-top: 2px solid var(--border); padding-top: 4px; }
.comp-empty { background: #fff; border: 1px dashed var(--border); border-radius: 8px; padding: 24px; text-align: center; color: var(--muted); }
.comp-note { font-size: 12px; color: var(--muted); }
.comp-badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #fff; white-space: nowrap; }
.comp-badge.state-正常 { background: #22c55e; }
.comp-badge.state-零流量 { background: #3b82f6; }
.comp-badge.state-缺失观测 { background: #ef4444; }
.comp-badge.state-正常停机 { background: #94a3b8; }
</style>
