<script setup>
import { t } from '../state'
import Icon from '../components/Icon.vue'
</script>
<template><section class="return-shell byh-wrap"><div class="panel return-card"><div class="return-icon"><Icon name="shield" :size="32" /></div><span class="section-kicker">BINGYUN PAY</span><h1>{{ t('已返回冰云支付', 'Back to BingYun Pay') }}</h1><p>{{ t('支付结果以支付宝确认后的订单状态为准。请前往控制台查看，若状态尚未更新，可手动查询支付宝。', 'Your order status is confirmed by Alipay. Check the console, or query Alipay if the status has not updated yet.') }}</p><RouterLink class="button" to="/dashboard">{{ t('查看订单', 'View orders') }}<Icon name="arrow" :size="17" /></RouterLink></div></section></template>
