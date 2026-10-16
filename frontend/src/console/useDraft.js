import { computed, reactive, ref } from 'vue'

// A form's working copy. `dirty` is true only while it differs from the last loaded state,
// so typing a value and typing it back leaves nothing to save.
export function useDraft() {
  const draft = reactive({}), base = ref('')
  const dirty = computed(() => base.value !== '' && JSON.stringify(draft) !== base.value)
  function load(values) {
    for (const key of Object.keys(draft)) delete draft[key]
    Object.assign(draft, values)
    base.value = JSON.stringify(draft)
  }
  return { draft, dirty, load }
}

// Lists are edited as text: one entry per line (entries may contain spaces), or words split by blanks and commas
export const toLines = list => (Array.isArray(list) ? list : []).join('\n')
export const fromLines = text => String(text || '').split('\n').map(line => line.trim()).filter(Boolean)
export const fromWords = text => String(text || '').split(/[\s,，]+/u).filter(Boolean)
