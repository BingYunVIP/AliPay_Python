<script setup>
const props = defineProps({ name: String })
const cryptoNetwork = { BSC:'bsc', TRON:'tron', APTOS:'aptos', ETH:'ethereum', PLASMA:'plasma', SOL:'solana', ARBITRUM:'arbitrum', POLYGON:'polygon', AVAXC:'avalanche' }
const cryptoToken = name => String(name || '').replace(/^crypto-/, '').replace(/.*_/, '').toUpperCase()
const isCryptoToken = name => String(name || '').startsWith('crypto-') && ['USDT','USDC'].includes(cryptoToken(name))
const isCryptoNetwork = name => String(name || '').startsWith('network-') && cryptoNetwork[String(name).slice(8).toUpperCase()]
</script>
<template>
  <img v-if="name === 'crypto'" src="/brand/crypto/tokens/USDT.svg" class="brand-image" alt="" aria-hidden="true" />
  <img v-else-if="isCryptoToken(name)" :src="`/brand/crypto/tokens/${cryptoToken(name)}.svg`" class="brand-image" alt="" aria-hidden="true" />
  <img v-else-if="isCryptoNetwork(name)" :src="`/brand/crypto/networks/${cryptoNetwork[String(name).slice(8).toUpperCase()]}.svg`" class="brand-image" alt="" aria-hidden="true" />
  <img v-else-if="name === 'bingyun' || name === 'claude'" :src="`/brand/${name}.png`" class="brand-image" alt="" aria-hidden="true" />
  <svg v-else-if="name === 'openai'" class="brand-image openai-mark" aria-hidden="true" viewBox="0 0 24 24"><use :href="'/brand/openai.svg#mark'" /></svg>
  <svg v-else class="brand-image" viewBox="0 0 24 24" aria-hidden="true"><use :href="`/brand/payments.svg#${name}`" /></svg>
</template>
