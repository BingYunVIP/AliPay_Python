<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, auth, errorText, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
const route = useRoute(), router = useRouter()
const register = computed(() => route.path === '/register')
const email = ref(''), password = ref(''), busy = ref(false), error = ref('')
async function submit() {
  busy.value = true; error.value = ''
  try {
    const data = await api(`/api/auth/${register.value ? 'register' : 'login'}`, { method: 'POST', body: JSON.stringify({ email: email.value, password: password.value }) })
    auth.user = data.user; auth.csrf = data.csrf_token; auth.key = null
    password.value = ''
    await router.push('/dashboard')
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="auth-shell byh-wrap"><div class="auth-intro"><span class="section-kicker">BINGYUN PAY</span><h1>{{ t('让每一笔收款，', 'Make every payment') }}<br /><span class="accent">{{ t('都有清晰的答案。', 'a little simpler.') }}</span></h1><p>{{ t('一个账户，管理订单、查看回调、连接你的业务。', 'One account to manage orders, follow notifications, and connect your business.') }}</p><div class="auth-proof"><Icon name="shield" />{{ t('独立密钥', 'Individual keys') }}<span>·</span><Icon name="network" />{{ t('统一接口', 'Unified API') }}</div><RouterLink class="text-link" to="/">{{ t('返回首页', 'Back to home') }}<Icon name="arrow" :size="16" /></RouterLink></div>
    <form class="auth-card panel" @submit.prevent="submit"><Brand class="auth-logo" name="bingyun" /><h2>{{ register ? t('创建冰云账户', 'Create your account') : t('欢迎回来', 'Welcome back') }}</h2><p>{{ register ? t('开始接入你的第一笔支付。', 'Connect your first payment.') : t('登录冰云支付控制台。', 'Sign in to your Pay console.') }}</p>
      <label for="email">{{ t('邮箱地址', 'Email address') }}</label><input id="email" v-model="email" type="email" autocomplete="username" required maxlength="254" placeholder="you@example.com" />
      <label for="password">{{ t('密码', 'Password') }}</label><input id="password" v-model="password" type="password" :autocomplete="register ? 'new-password' : 'current-password'" required :minlength="register ? 12 : 1" maxlength="128" :aria-describedby="register ? 'password-hint' : undefined" :placeholder="register ? t('至少 12 个字符', 'At least 12 characters') : t('输入密码', 'Your password')" />
      <small v-if="register" id="password-hint">{{ t('建议使用密码管理器生成并保存密码。', 'A password manager can generate and remember a strong password.') }}</small>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <button class="button full-width" :disabled="busy"><Icon v-if="busy" class="spin" name="loader" :size="17" />{{ register ? t('创建账户', 'Create account') : t('登录', 'Sign in') }}<Icon v-if="!busy" name="arrow" :size="17" /></button>
      <div class="auth-switch">{{ register ? t('已有账户？', 'Already have an account?') : t('还没有账户？', 'New to Pay?') }} <RouterLink :to="register ? '/login' : '/register'" @click="error = ''">{{ register ? t('立即登录', 'Sign in') : t('注册账户', 'Create an account') }}</RouterLink></div>
    </form>
  </div>
</template>
