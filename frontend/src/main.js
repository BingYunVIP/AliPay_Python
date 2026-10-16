import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Home from './views/Home.vue'
import { auth, initializeAuth } from './state'
import './base.css'
import './home.css'
import './auth.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/login', component: () => import('./views/Auth.vue') },
    { path: '/register', component: () => import('./views/Auth.vue') },
    { path: '/dashboard', component: () => import('./views/Dashboard.vue'), meta: { auth: true } },
    { path: '/payment-return', component: () => import('./views/PaymentReturn.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
router.beforeEach(async (to) => {
  await initializeAuth()
  if (to.meta.auth && !auth.user) return { path: '/login', query: { next: to.fullPath } }
  if (['/login', '/register'].includes(to.path) && auth.user) return typeof to.query.next === 'string' && /^\/dashboard(?:\?|$)/.test(to.query.next) ? to.query.next : '/dashboard'
})
createApp(App).use(router).mount('#app')
