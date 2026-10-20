<script setup>
import {computed,ref} from 'vue'
import {useRouter} from 'vue-router'
import {api,auth} from '../state'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'
const router=useRouter(),email=ref(''),current=ref(''),password=ref(''),confirm=ref(''),visible=ref(false),busy=ref(false),error=ref('')
const characterTypes=computed(()=>[
  {text:'大写字母',ok:/[A-Z]/.test(password.value)},
  {text:'小写字母',ok:/[a-z]/.test(password.value)},
  {text:'数字',ok:/[0-9]/.test(password.value)},
  {text:'特殊符号',ok:Array.from(password.value).some(char=>"!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~".includes(char))},
])
const categoryCount=computed(()=>characterTypes.value.filter(item=>item.ok).length)
const rules=computed(()=>[
  {text:'至少 12 个字符',ok:Array.from(password.value).length>=12&&Array.from(password.value).length<=128},
  {text:`字符类型至少满足 3 类（当前 ${categoryCount.value}/4）`,ok:categoryCount.value>=3},
  {text:'不含空白字符',ok:password.value.length>0&&!/\s/.test(password.value)},
])
const valid=computed(()=>rules.value.every(rule=>rule.ok)&&password.value===confirm.value&&Boolean(email.value.trim())&&email.value.trim().toLowerCase()!=='admin@pay.com')
async function submit(){if(!valid.value)return;busy.value=true;error.value='';try{await api('/api/auth/initial-credentials',{method:'POST',body:JSON.stringify({email:email.value,password:password.value,password_confirmation:confirm.value,current_password:current.value})});auth.user=null;auth.csrf='';auth.key=null;await router.replace('/login?updated=1')}catch(e){error.value=({'password_confirmation_mismatch':'两次输入的密码不一致','invalid_admin_password':'密码须至少 12 位，大小写字母、数字、符号中满足至少三类，且不含空白','invalid_new_email':'请填写不同于初始账号的有效邮箱','invalid_credentials':'当前密码不正确','email_unavailable':'此邮箱已被使用'})[e.message]||'修改失败，请检查输入后重试'}finally{busy.value=false}}
</script>
<template><div class="auth-shell"><form class="auth-card panel security-setup" @submit.prevent="submit"><div class="auth-card-heading"><Brand name="bingyun"/><h1>设置管理员凭据</h1></div><p class="field-hint">首次登录必须修改邮箱和密码后才能使用后台</p><label for="setup-email">新管理员邮箱</label><input id="setup-email" v-model="email" type="email" required autocomplete="username" maxlength="254"/><label for="setup-current">当前密码</label><input id="setup-current" v-model="current" type="password" required autocomplete="current-password"/><label for="setup-password">新密码</label><div class="setup-password-row"><input id="setup-password" v-model="password" :type="visible?'text':'password'" minlength="12" maxlength="128" required autocomplete="new-password"/><button type="button" class="icon-button" :aria-label="visible?'隐藏密码':'显示密码'" @click="visible=!visible"><Icon :name="visible?'eye-off':'eye'"/></button></div><ul class="password-rules"><li v-for="rule in rules" :key="rule.text" :class="{met:rule.ok}"><Icon :name="rule.ok?'check':'circle'" :size="15"/>{{rule.text}}</li></ul><div class="password-categories" aria-label="密码字符类型"><span v-for="item in characterTypes" :key="item.text" :class="{met:item.ok}"><Icon :name="item.ok?'check':'circle'" :size="13"/>{{item.text}}</span></div><label for="setup-confirm">确认新密码</label><input id="setup-confirm" v-model="confirm" :type="visible?'text':'password'" minlength="12" maxlength="128" required autocomplete="new-password"/><p v-if="confirm&&password!==confirm" class="error-box">两次输入的密码不一致</p><p v-if="error" class="error-box" role="alert">{{error}}</p><button class="button full-width" :disabled="busy||!valid">{{busy?'正在保存':'保存并重新登录'}}</button><button type="button" class="text-link" @click="api('/api/auth/logout',{method:'POST'}).then(()=>{auth.user=null;router.replace('/login')})">退出登录</button></form></div></template>
<style scoped>.security-setup{max-width:460px}.security-setup h1{font-size:22px}.setup-password-row{display:flex;align-items:center;gap:8px}.setup-password-row input{min-width:0;flex:1}.password-rules{padding:0;list-style:none;display:grid;gap:7px;font-size:12px;color:var(--muted)}.password-rules li{display:flex;align-items:center;gap:7px}.password-rules .met{color:#168052}.password-categories{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:0 0 8px}.password-categories span{display:inline-flex;align-items:center;gap:4px;padding:4px 7px;border:1px solid var(--line);border-radius:7px;font-size:11px;color:var(--muted)}.password-categories .met{color:var(--green);border-color:var(--green);background:var(--accent-wash)}</style>
