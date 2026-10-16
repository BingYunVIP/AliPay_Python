<script setup>
import { computed, ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, auth, errorText, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import HumanCheck from '../components/HumanCheck.vue'
const route = useRoute(), router = useRouter()
const register = computed(() => route.path === '/register')
const caps=ref({}),code=ref(''),sending=ref(false),retry=ref(0),sent=ref(false),token=ref(''),human=ref(null),invite=ref(String(route.query.invite||''))
const action=computed(()=>register.value?(caps.value.email_verification&&!sent.value?'email_code':'register'):'login')
let timer
onMounted(async()=>{try{caps.value=await api('/api/capabilities')}catch{};timer=setInterval(()=>retry.value=Math.max(0,retry.value-1),1000)})
onBeforeUnmount(()=>clearInterval(timer))
watch(()=>route.path,()=>{token.value='';sent.value=false})
async function sendCode(){if(action.value!=='email_code'){sent.value=false;token.value='';return}sending.value=true;error.value='';try{await api('/api/auth/email-code',{method:'POST',body:JSON.stringify({email:email.value,turnstile_token:token.value})});retry.value=60;sent.value=true;token.value=''}catch(e){error.value=errorText(e)}finally{sending.value=false;human.value?.reset()}}
const email = ref(''), password = ref(''), busy = ref(false), error = ref('')
async function submit() {
  busy.value = true; error.value = ''
  try {
    const data = await api(`/api/auth/${register.value ? 'register' : 'login'}`, { method: 'POST', body: JSON.stringify({ email: email.value, password: password.value, email_code:code.value,turnstile_token:token.value,invite_code:invite.value }) })
    auth.user = data.user; auth.csrf = data.csrf_token; auth.key = null
    password.value = ''
    const next = route.query.next
    await router.replace(typeof next === 'string' && /^\/dashboard(?:\?|$)/.test(next) ? next : '/dashboard')
  } catch (e) { error.value = errorText(e).replace(/[。！？.!?]+$/u, '') }
  finally { busy.value = false;human.value?.reset();token.value='' }
}
</script>
<template>
  <div class="auth-shell byh-wrap">
    <form class="auth-card panel" @submit.prevent="submit">
      <div class="auth-card-heading"><Brand class="auth-logo" name="bingyun" /><h2>{{ register ? t('创建冰云账户', 'Create your account') : t('欢迎回来', 'Welcome back') }}</h2></div>
      <label for="email">{{ register ? t('邮箱地址', 'Email address') : t('账号或邮箱', 'Account or email') }}</label><input id="email" v-model="email" :type="register ? 'email' : 'text'" autocomplete="username" required maxlength="254" :placeholder="register ? 'you@example.com' : 'admin'" />
      <label for="password">{{ t('密码', 'Password') }}</label><input id="password" v-model="password" type="password" :autocomplete="register ? 'new-password' : 'current-password'" required :minlength="register ? 12 : 1" maxlength="128" :placeholder="register ? t('至少 12 个字符', 'At least 12 characters') : t('输入密码', 'Your password')" />
      <template v-if="register&&caps.email_verification"><label for="email-code">邮箱验证码</label><div class="auth-code-row"><input id="email-code" v-model="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="one-time-code"/><button type="button" class="button button-secondary" :disabled="sending||retry>0||(!token&&caps.turnstile?.enabled&&action==='email_code')" @click="sendCode">{{ retry?`${retry}s`:sent?'重新验证并发送':'发送验证码' }}</button></div></template>
      <HumanCheck v-if="caps.turnstile?.enabled" ref="human" :site-key="caps.turnstile.site_key" :action="action" @token="token=$event"/>
      <p v-if="register&&caps.registration_review" class="field-hint">注册后需通过商户审核才能收款</p><p v-if="register&&caps.registration_paid" class="field-hint">注册服务费 ￥{{ caps.registration_fee }}，注册后在账户中心支付</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <button class="button full-width" :disabled="busy||(caps.turnstile?.enabled&&(!token||action==='email_code'))"><Icon v-if="busy" class="spin" name="loader" :size="17" />{{ register ? t('创建账户', 'Create account') : t('登录', 'Sign in') }}<Icon v-if="!busy" name="arrow" :size="17" /></button>
      <div class="auth-switch">{{ register ? t('已有账户', 'Already have an account') : t('还没有账户', 'New to Pay') }} <RouterLink :to="register ? '/login' : '/register'" @click="error = ''">{{ register ? t('立即登录', 'Sign in') : t('注册账户', 'Create an account') }}</RouterLink></div>
    </form>
  </div>
</template>
