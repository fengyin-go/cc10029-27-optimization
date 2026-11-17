<template>
  <section class="page" data-module="alert">
    <header class="page-head">
      <div>
        <h2>告警中心管理</h2>
        <p class="page-desc">配置采集异常的告警触发条件、通知渠道和值班策略。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记告警规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出告警中心清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无告警中心数据，可先登记告警规则</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记告警规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条告警中心记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="notify-panel" data-testid="notify-panel">
      <header class="page-head">
        <div>
          <h3>告警通知清单</h3>
          <p class="page-desc">
            由监控完整度数据报告生成，仅针对被确认为缺失或异常的观测窗口。
            报告状态：{{ notifyMeta.报告状态 }}<template v-if="notifyMeta.数据版本"> · 数据版本 V{{ notifyMeta.数据版本 }}</template>
          </p>
        </div>
        <div class="page-actions">
          <button class="btn" data-testid="reload-notifications" type="button" @click="loadNotifications">刷新清单</button>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>通知编号</th><th>来源报告</th><th>窗口编号</th><th>观测时段</th>
            <th>告警类型</th><th>通知状态</th><th>原因说明</th><th>生成时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="n in notifications" :key="String(n.id)" :data-testid="`notify-${n.id}`" :data-status="String(n.通知状态)">
            <td>{{ n.通知编号 }}</td>
            <td>{{ n.来源报告 }} · V{{ n.数据版本 }}</td>
            <td>{{ n.窗口编号 }}</td>
            <td>{{ n.观测时段 }}</td>
            <td>{{ n.告警类型 }}</td>
            <td><span class="notify-badge" :class="`st-${n.通知状态}`">{{ n.通知状态 }}</span></td>
            <td>{{ n.原因说明 ?? '—' }}</td>
            <td>{{ n.生成时间 }}</td>
          </tr>
          <tr v-if="!notifications.length">
            <td colspan="8" class="empty-state">暂无告警通知：当前没有被确认为缺失或异常的观测窗口</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/alert'
const columns = ["规则编号", "监控对象", "告警条件", "通知渠道", "通知模板", "重复间隔", "生效时段", "值班人员"]
const actions = ["提交校验", "启用配置", "停用配置"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const metricNames = ["告警规则总数", "待校验告警规则", "已启用告警规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'alert.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('告警中心动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警中心操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('告警规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警中心列表读取失败'
  }
}

// 告警通知清单：读取监控完整度数据报告生成的通知，迟到数据到达后可在此核对告警变化
const notifications = ref<Row[]>([])
const notifyMeta = ref<Record<string, string | number | null>>({ 报告状态: '暂无数据', 数据版本: null })

async function loadNotifications() {
  try {
    const response = await request(`${ENDPOINT}/notifications`)
    if (!response.ok) {
      throw new Error('告警通知清单读取失败')
    }
    const payload = await response.json()
    notifications.value = payload.items ?? []
    notifyMeta.value = { 报告状态: payload.报告状态 ?? '暂无数据', 数据版本: payload.数据版本 ?? null }
  } catch (error) {
    notifications.value = []
    errorMessage.value = error instanceof Error ? error.message : '告警通知清单读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadNotifications()
})
</script>
<style scoped>
.notify-panel { margin-top: 20px; border-top: 2px solid var(--border); padding-top: 4px; }
.notify-badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #fff; white-space: nowrap; }
.notify-badge.st-待处理 { background: #ef4444; }
.notify-badge.st-已恢复 { background: #22c55e; }
.notify-badge.st-已取消 { background: #94a3b8; }
.notify-badge.st-已归档 { background: #64748b; }
</style>
