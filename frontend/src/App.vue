<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">智能爬虫采集平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向网页爬取、API接入、增量抓取、反爬对抗与采集质量校验的数据采集平台。</span>
        <span class="head-user">
          当前值班：{{ store.operator }} · {{ store.shiftLabel }}
          <select
            class="role-switch"
            data-testid="role-switch"
            :value="store.role"
            @change="store.setRole(($event.target as HTMLSelectElement).value)"
          >
            <option value="值班管理员">值班管理员</option>
            <option value="观察员">观察员（仅查看）</option>
          </select>
        </span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const navItems = [{ label: "运营概览", path: "/" }, { label: "采集目标", path: "/targetsite" }, { label: "抽取规则", path: "/selector" }, { label: "爬虫引擎", path: "/crawler" }, { label: "代理池", path: "/proxy" }, { label: "反爬对抗", path: "/antiblock" }, { label: "定时调度", path: "/schedule" }, { label: "数据提取", path: "/extractor" }, { label: "采集校验", path: "/validate" }, { label: "失败重试", path: "/retry" }, { label: "存储落地", path: "/storage" }, { label: "重复去重", path: "/dedup" }, { label: "质量报告", path: "/quality" }, { label: "运行监控", path: "/monitor" }, { label: "告警中心", path: "/alert" }, { label: "采集API", path: "/apigate" }, { label: "历史归档", path: "/archive" }, { label: "模板市场", path: "/template" }, { label: "采集流程", path: "/flowline" }]
</script>
<style scoped>
.role-switch { margin-left: 8px; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; }
</style>
