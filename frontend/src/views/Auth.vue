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
    const next = route.query.next
    await router.replace(typeof next === 'string' && /^\/dashboard(?:\?|$)/.test(next) ? next : '/dashboard')
  } catch (e) { error.value = errorText(e).replace(/[。！？.!?]+$/u, '') }
  finally { busy.value = false }
}
</script>
<template>
  <div class="auth-shell byh-wrap">
    <form class="auth-card panel" @submit.prevent="submit">
      <div class="auth-card-heading"><Brand class="auth-logo" name="bingyun" /><h2>{{ register ? t('创建冰云账户', 'Create your account') : t('欢迎回来', 'Welcome back') }}</h2></div>
      <label for="email">{{ register ? t('邮箱地址', 'Email address') : t('账号或邮箱', 'Account or email') }}</label><input id="email" v-model="email" :type="register ? 'email' : 'text'" autocomplete="username" required maxlength="254" :placeholder="register ? 'you@example.com' : 'admin'" />
      <label for="password">{{ t('密码', 'Password') }}</label><input id="password" v-model="password" type="password" :autocomplete="register ? 'new-password' : 'current-password'" required :minlength="register ? 12 : 1" maxlength="128" :placeholder="register ? t('至少 12 个字符', 'At least 12 characters') : t('输入密码', 'Your password')" />
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <button class="button full-width" :disabled="busy"><Icon v-if="busy" class="spin" name="loader" :size="17" />{{ register ? t('创建账户', 'Create account') : t('登录', 'Sign in') }}<Icon v-if="!busy" name="arrow" :size="17" /></button>
      <div class="auth-switch">{{ register ? t('已有账户', 'Already have an account') : t('还没有账户', 'New to Pay') }} <RouterLink :to="register ? '/login' : '/register'" @click="error = ''">{{ register ? t('立即登录', 'Sign in') : t('注册账户', 'Create an account') }}</RouterLink></div>
    </form>
  </div>
</template>
