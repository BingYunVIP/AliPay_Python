import { onBeforeUnmount, onMounted, ref } from 'vue'

// Adapted from ByHome_UI/useCustomHomeMotion: one flight, two sequential legs.
// Background effects are deliberately absent. At most one pulse is visible.
export function usePaymentFlow(host) {
  const paused = ref(false)
  let frame = 0, flight = null, nextFlight = 0, lastFrame = 0, media, observer
  let lastBenefit = -1
  const lastBranch = { in: -1, out: -1 }
  const random = (min, max) => min + Math.random() * (max - min)
  const choose = (last, length) => length === 1 ? 0 : (last + 1 + Math.floor(random(0, length - 1))) % length
  function clear() {
    const root = host.value
    root?.querySelectorAll('.byh-pulse').forEach(el => { el.style.opacity = '0' })
    root?.querySelectorAll('.byh-flow-active, .byh-benefit-breath').forEach(el => {
      el.classList.remove('byh-flow-active', 'byh-benefit-breath')
      delete el.dataset.homeFlowState
    })
    flight = null
  }
  function route(side) {
    const root = host.value
    const svg = [...root.querySelectorAll(`.byh-wire-${side} > svg`)].find(el => el.getBoundingClientRect().width > 0 && getComputedStyle(el).display !== 'none')
    if (!svg) return null
    const branches = [...svg.querySelectorAll('.byh-branch')]
    const trunk = svg.querySelector('.byh-trunk'), pulse = svg.querySelector('.byh-pulse')
    if (!branches.length || !trunk || !pulse) return null
    const index = choose(lastBranch[side], branches.length)
    lastBranch[side] = index
    const node = root.querySelectorAll(`.byh-col-${side} .byh-node`)[index]
    const paths = side === 'in' ? [branches[index], trunk] : [trunk, branches[index]]
    return { node, pulse, paths, lengths: paths.map(path => path.getTotalLength()) }
  }
  function activate(node, state) {
    node.classList.add('byh-flow-active')
    node.dataset.homeFlowState = state
  }
  function place(route, progress, size) {
    let distance = route.lengths.reduce((a, b) => a + b, 0) * progress, index = 0
    while (index < route.paths.length - 1 && distance > route.lengths[index]) distance -= route.lengths[index++]
    const point = route.paths[index].getPointAtLength(distance)
    route.pulse.setAttribute('transform', `translate(${point.x} ${point.y})`)
    route.pulse.style.opacity = String(Math.min(1, progress * 15, (1 - progress) * 15))
    route.pulse.style.strokeWidth = `${size * (1 + .6 * Math.sin(progress * Math.PI))}px`
  }
  function tick(now) {
    const root = host.value
    if (!root) { frame = 0; return }
    if (paused.value || document.hidden || media.matches) {
      clear(); frame = 0
      root.dataset.homeMotionState = media.matches ? 'static' : 'paused'
      return
    }
    if (now - lastFrame >= 1000 / 30) {
      lastFrame = now
      if (!flight && now >= nextFlight) {
        clear()
        const input = route('in'), output = route('out')
        if (input && output) {
          flight = { input, output, start: now, leg: random(1600, 1900), hold: random(1800, 2300), size: random(7, 9), arrived: false }
          const duration = 1100 + flight.leg * 2 + 350 + flight.hold + 500
          nextFlight = now + duration
          root.style.setProperty('--home-flow-cycle-duration', `${duration}ms`)
          const benefits = [...root.querySelectorAll('.byh-eyebrow li')]
          if (benefits.length) {
            lastBenefit = choose(lastBenefit, benefits.length)
            benefits[lastBenefit].classList.add('byh-benefit-breath')
          }
          activate(input.node, 'source')
          root.dataset.homeFlowRoute = `${lastBranch.in}:${lastBranch.out}`
        } else nextFlight = now + 1500
      }
      if (flight) {
        const elapsed = now - flight.start
        const outputStart = 1100 + flight.leg + 350, arrival = outputStart + flight.leg
        flight.input.pulse.style.opacity = '0'
        flight.output.pulse.style.opacity = '0'
        if (elapsed >= 1100 && elapsed < 1100 + flight.leg) place(flight.input, (elapsed - 1100) / flight.leg, flight.size)
        else if (elapsed >= outputStart && elapsed < arrival) place(flight.output, (elapsed - outputStart) / flight.leg, flight.size)
        else if (elapsed >= arrival && !flight.arrived) { activate(flight.output.node, 'destination'); flight.arrived = true }
        if (elapsed >= arrival + flight.hold) {
          root.querySelectorAll('.byh-pulse').forEach(el => { el.style.opacity = '0' })
          flight = null
        }
      }
    }
    frame = requestAnimationFrame(tick)
  }
  function sync() {
    cancelAnimationFrame(frame)
    clear()
    const root = host.value
    if (!root) return
    if (paused.value || document.hidden || media.matches) {
      root.dataset.homeMotionState = media.matches ? 'static' : 'paused'
      return
    }
    root.dataset.homeMotionState = 'running'
    nextFlight = performance.now() + 350
    frame = requestAnimationFrame(tick)
  }
  function toggle() { paused.value = !paused.value; sync() }
  onMounted(() => {
    media = window.matchMedia('(prefers-reduced-motion: reduce)')
    observer = new ResizeObserver(sync)
    observer.observe(host.value)
    media.addEventListener('change', sync)
    document.addEventListener('visibilitychange', sync)
    sync()
  })
  onBeforeUnmount(() => {
    cancelAnimationFrame(frame); clear(); observer?.disconnect()
    media?.removeEventListener('change', sync)
    document.removeEventListener('visibilitychange', sync)
  })
  return { paused, toggle }
}
