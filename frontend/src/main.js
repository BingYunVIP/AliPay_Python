import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Home from './views/Home.vue'
import { auth, initializeAuth } from './state'
import './base.css'
import './home.css'
import './auth.css'
const common=['overview','testpay','orders','webhook','logs','account']
const admin=[...common,'channels','merchants','groups','rates','settings','settlements']
const routes=[
 {path:'/',component:Home},
 {path:'/login',component:()=>import('./views/Auth.vue')},
 {path:'/register',component:()=>import('./views/Auth.vue')},
 {path:'/admin/security/setup',component:()=>import('./views/SecuritySetup.vue'),meta:{auth:true,role:'admin',setup:true}},
 ...['admin','user'].flatMap(role=>[
  {path:`/${role}`,redirect:`/${role}/overview`},
  ...(role==='admin'?admin:common).map(page=>({path:`/${role}/${page}`+(page==='settings'?'/section/:section?':page==='rates'?'/section/:section?':''),component:()=>import('./views/Dashboard.vue'),meta:{auth:true,role,page}}))
 ]),
 {path:'/cashier/:token',component:()=>import('./views/Cashier.vue')},
 {path:'/payment-return',component:()=>import('./views/PaymentReturn.vue')},
 {path:'/:pathMatch(.*)*',component:()=>import('./views/NotFound.vue')}
]
// Optional sections are real paths, never query-based page switching.
for(const route of routes){if(route.path.includes('/section/'))route.path=route.path.replace('/section/','/')}
const router=createRouter({history:createWebHistory(),routes,scrollBehavior:()=>({top:0})})
router.beforeEach(async to=>{
 await initializeAuth()
 if(to.meta.auth&&!auth.user)return {path:'/login',query:{next:to.fullPath}}
 if(auth.user?.must_change_credentials&&to.meta.auth&&!to.meta.setup)return '/admin/security/setup'
 if(to.meta.auth&&auth.user&&to.meta.role!==auth.user.role)return `/${auth.user.role}/overview`
 if(to.meta.setup&&!auth.user?.must_change_credentials)return '/admin/overview'
 if(['/login','/register'].includes(to.path)&&auth.user)return auth.user.must_change_credentials?'/admin/security/setup':`/${auth.user.role}/overview`
})
createApp(App).use(router).mount('#app')
