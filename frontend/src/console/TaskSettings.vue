<script setup>
import { inject, onMounted, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import { ago, fullDate } from './format'
import Icon from '../components/Icon.vue'
import FormGroup from './FormGroup.vue'
const c = inject('console')
const state = ref(null), error = ref(''), busy = ref(false)
async function load() {
  try { state.value = await api('/api/admin/tasks'); error.value = '' }
  catch (e) { error.value = errorText(e) }
}
async function run() {
  busy.value = true
  try {
    const result = await api('/api/admin/tasks/run', { method: 'POST' })
    c.toast(result.claimed ? t('平台任务已执行', 'Platform tasks ran') : t('距离上次执行不足设定间隔，暂不重复执行', 'Too soon after the last run — nothing was started'))
    await load()
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
// The worker writes its heartbeat every few seconds, so a minute of silence means it is not running
const alive = beat => Date.now() / 1000 - beat.last_seen < 60
const heartbeatName = name => ({ callbacks: t('后台工作进程', 'Background worker') })[name] || name
const taskName = name => ({ platform: t('平台任务', 'Platform tasks') })[name] || name
const taskStatus = status => ({ success: t('成功', 'Succeeded'), failed: t('失败', 'Failed'), running: t('执行中', 'Running'), idle: t('等待执行', 'Idle') })[status] || status
const taskClass = status => ({ success: 'ok', failed: 'bad', running: 'wait' })[status] || ''
watch(c.revision, load)
onMounted(load)
</script>
<template>
  <div class="settings-stack">
    <p v-if="error" class="error-box" role="alert">{{ error }}</p>
    <div v-if="state" class="stat-grid">
      <div class="hig-card stat-tile"><span>{{ t('账本借贷平衡检查', 'Ledger balance check') }}<Icon name="shield" :size="17" /></span><strong class="small" :class="state.ledger_balanced ? 'text-green' : 'text-red'">{{ state.ledger_balanced ? t('通过', 'Balanced') : t('不平衡', 'Unbalanced') }}</strong><small v-if="!state.ledger_balanced">{{ t('发现不平衡，请停止结算并排查', 'Out of balance — stop settlements and investigate') }}</small></div>
      <div v-for="beat in state.heartbeats" :key="beat.name" class="hig-card stat-tile"><span>{{ heartbeatName(beat.name) }}<Icon name="network" :size="17" /></span><strong class="small" :class="alive(beat) ? 'text-green' : 'text-red'">{{ alive(beat) ? t('运行中', 'Running') : t('无响应', 'Not responding') }}</strong><small>{{ t('最后心跳', 'Last heartbeat') }} {{ ago(beat.last_seen) }}</small></div>
      <div class="hig-card stat-tile"><span>{{ t('当前识别的客户端 IP', 'Detected client IP') }}<Icon name="globe" :size="17" /></span><strong class="small"><code translate="no">{{ state.client_ip }}</code></strong></div>
    </div>
    <FormGroup :title="t('任务执行记录', 'Task runs')" :note="t('由 bypay 工作进程持续执行，支付核验、回调和退款任务保持运行，邮件及平台任务开关在商户与资金规则中设置', 'Run continuously by the bypay worker: payment checks, callbacks and refunds. Email and platform task switches are under Merchants & funds')">
      <template #action><button type="button" class="button button-secondary" :disabled="busy" @click="run"><Icon :name="busy ? 'loader' : 'play'" :class="{ spin: busy }" :size="15" />{{ t('执行一次平台任务', 'Run platform tasks once') }}</button></template>
      <div v-if="state?.tasks.length" class="table-scroll">
        <table class="console-table admin-table">
          <thead><tr><th>{{ t('任务', 'Task') }}</th><th>{{ t('状态', 'Status') }}</th><th>{{ t('最近执行', 'Last run') }}</th><th>{{ t('说明', 'Detail') }}</th></tr></thead>
          <tbody><tr v-for="task in state.tasks" :key="task.name"><td>{{ taskName(task.name) }}</td><td><span class="state-badge" :class="taskClass(task.status)"><i></i>{{ taskStatus(task.status) }}</span></td><td class="date-cell">{{ task.finished_at ? fullDate(task.finished_at) : t('尚未执行', 'Never run') }}</td><td>{{ task.error || '—' }}</td></tr></tbody>
        </table>
      </div>
      <p v-else class="status-row"><span class="status-text"><small>{{ state ? t('尚未执行过任务', 'No task has run yet') : t('正在读取…', 'Loading…') }}</small></span></p>
    </FormGroup>
  </div>
</template>
