<script setup>
import { inject, onMounted, ref, computed, watch } from 'vue'
import { api, copyText } from '../state'
import Icon from '../components/Icon.vue'
// Shown inside the merchant API dialog: another merchant's credentials for the administrator, your own otherwise
const props=defineProps({merchantId:[String,Number]})
const selectedVersion=ref('v1')
const c = inject('console'), profile = ref(null), publicKey = ref(''), privateKey = ref(''), reveal = ref(false), busy = ref(false), error = ref(''), notice = ref(''), action = ref(''), dialog = ref(null)
const endpoint = computed(() => '/api/merchant/config' + (props.merchantId ? '?merchant_id='+encodeURIComponent(props.merchantId) : ''))
const errors = {invalid_rsa_key:'请输入有效的 RSA 公钥',merchant_public_key_required:'请先配置商户 RSA 公钥'}
async function load() { try { profile.value = await api(endpoint.value); publicKey.value = profile.value.merchant_public_key } catch (e) { c.report(e) } }
onMounted(load)
watch(endpoint,load)
async function copy(value) { try { await copyText(String(value)); notice.value = '已复制' } catch { error.value = '请选中内容手动复制' } }
function toPem(buffer,label) { const encoded = btoa(String.fromCharCode(...new Uint8Array(buffer))); return `-----BEGIN ${label}-----\n${encoded.match(/.{1,64}/g).join('\n')}\n-----END ${label}-----\n` }
async function update(payload) {
  busy.value = true; error.value = ''; notice.value = ''
  try { profile.value = await api(endpoint.value,{method:'POST',body:JSON.stringify(payload)}); publicKey.value=profile.value.merchant_public_key; notice.value='配置已保存'; return true }
  catch (e) { error.value=errors[e.message] || '保存失败，请检查密钥内容后重试'; return false }
  finally { busy.value=false }
}
function confirm(kind) { action.value=kind;privateKey.value='';error.value='';dialog.value.showModal() }
async function generate() {
  if (action.value==='md5') { if (await update({rotate_md5:true})) dialog.value.close(); return }
  busy.value=true;error.value=''
  try {
    if (!window.crypto?.subtle) throw new Error('请使用 localhost 或 HTTPS 打开控制台生成密钥')
    const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify'])
    const pub=toPem(await crypto.subtle.exportKey('spki',pair.publicKey),'PUBLIC KEY')
    const secret=toPem(await crypto.subtle.exportKey('pkcs8',pair.privateKey),'PRIVATE KEY')
    if (await update({merchant_public_key:pub})) privateKey.value=secret
  } catch (e) { error.value=e.message }
  finally { busy.value=false }
}
function download() { const url=URL.createObjectURL(new Blob([privateKey.value],{type:'application/x-pem-file'}));const link=document.createElement('a');link.href=url;link.download=`merchant-${profile.value.merchant_id}-private.pem`;link.click();URL.revokeObjectURL(url) }
</script>
<template>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p><p v-if="notice" class="field-hint" role="status">{{ notice }}</p>
  <template v-if="profile">
    <div class="merchant-summary"><section class="hig-card merchant-identity"><label>接口地址<div class="credential-row"><input :value="profile.interface_url" readonly /><button class="icon-button" aria-label="复制接口地址" @click="copy(profile.interface_url)"><Icon name="copy" :size="17"/></button></div></label><label>商户 ID<div class="credential-row"><input :value="profile.merchant_id" readonly/><button class="icon-button" aria-label="复制商户 ID" @click="copy(profile.merchant_id)"><Icon name="copy" :size="17"/></button></div></label></section><section class="hig-card merchant-mode"><label>签名方式<select :value="profile.signature_mode" :disabled="busy" @change="update({signature_mode:$event.target.value})"><option value="both">V1 MD5 + V2 RSA</option><option value="rsa">仅 V2 RSA</option></select></label></section></div>
    <div class="merchant-version-tabs" role="tablist" aria-label="接口版本"><button type="button" role="tab" :aria-selected="selectedVersion==='v1'" @click="selectedVersion='v1'">V1 · MD5</button><button type="button" role="tab" :aria-selected="selectedVersion==='v2'" @click="selectedVersion='v2'">V2 · RSA</button></div><div class="merchant-keys-grid">
      <section v-if="selectedVersion==='v1'" class="hig-card merchant-key-card">
        <label>商户 MD5 密钥<div class="credential-row"><input :type="reveal?'text':'password'" :value="profile.md5_key" readonly autocomplete="off"/><button class="icon-button" :aria-label="reveal?'隐藏密钥':'显示密钥'" @click="reveal=!reveal"><Icon :name="reveal?'eye-off':'eye'" :size="17"/></button><button class="icon-button" aria-label="复制 MD5 密钥" @click="copy(profile.md5_key)"><Icon name="copy" :size="17"/></button></div></label>
        <button class="button button-secondary" :disabled="busy" @click="confirm('md5')">重置 MD5 密钥</button>
      </section>
      <section v-else class="hig-card merchant-key-card">
        <label>平台公钥<textarea :value="profile.platform_public_key" readonly rows="4" spellcheck="false"/></label><button class="button button-secondary" @click="copy(profile.platform_public_key)">复制平台公钥</button>
        <label>商户公钥<textarea v-model="publicKey" rows="4" spellcheck="false" placeholder="粘贴已有 RSA 公钥，或生成密钥对"/></label>
        <div class="merchant-key-actions"><button class="button button-secondary" :disabled="busy || !publicKey.trim()" @click="update({merchant_public_key:publicKey})">保存商户公钥</button><button class="button" :disabled="busy" @click="confirm('rsa')">{{ profile.merchant_public_key?'重新生成密钥对':'生成 RSA 密钥对' }}</button></div>
      </section>
    </div>
  </template>
  <dialog ref="dialog" class="console-dialog" aria-label="管理商户密钥" @close="privateKey=''" @cancel.prevent="!busy && dialog.close()"><div class="dialog-heading"><h2>{{ privateKey?'保存商户私钥':action==='md5'?'重置 MD5 密钥':'生成商户 RSA 密钥对' }}</h2><button class="icon-button" :disabled="busy" aria-label="关闭" @click="dialog.close()"><Icon name="x"/></button></div>
    <template v-if="privateKey"><p>私钥仅在当前窗口显示，请保存后填写到测试站的 V2 配置</p><textarea class="key-secret" :value="privateKey" readonly rows="9" aria-label="商户私钥"/><div class="dialog-actions"><button class="button button-secondary" @click="copy(privateKey)">复制私钥</button><button class="button" @click="download">下载私钥</button></div></template>
    <template v-else><p>生成后请同步更新使用该商户接口的应用配置</p><p v-if="error" class="error-box" role="alert">{{ error }}</p><div class="dialog-actions"><button class="button button-secondary" :disabled="busy" @click="dialog.close()">取消</button><button class="button" :disabled="busy" @click="generate"><Icon v-if="busy" name="loader" class="spin" :size="16"/>确认生成</button></div></template>
  </dialog>
</template>
<style scoped>
.merchant-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-bottom:20px;align-items:stretch}.merchant-identity,.merchant-key-card,.merchant-mode{padding:24px}.merchant-identity{display:grid;grid-template-columns:1fr;gap:18px;align-content:start}.merchant-keys-grid{display:grid;align-items:start;grid-template-columns:1fr;gap:20px}.merchant-key-card label,.merchant-mode label,.merchant-identity label{display:block;font-size:13px}.merchant-key-card label{margin-top:20px}.credential-row{display:flex;align-items:center;gap:8px;margin-top:9px}.credential-row input{min-width:0;flex:1;width:100%}.merchant-key-card textarea{width:100%;box-sizing:border-box;margin:10px 0;font:12px/1.5 monospace;resize:vertical}.merchant-key-card .button{margin-top:12px}.merchant-key-actions{display:flex;gap:10px;flex-wrap:wrap}.merchant-mode{display:flex;flex-direction:column;justify-content:flex-start}.merchant-mode select{display:block;width:100%;margin-top:12px}@media(max-width:850px){.merchant-summary,.merchant-keys-grid{grid-template-columns:1fr}}
</style>
