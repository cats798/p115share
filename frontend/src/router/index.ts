import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { public: true }
  },
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/Home.vue'),
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

router.beforeEach(async (to, _, next) => {
  const auth = useAuthStore()
  
  if (!auth.isAuthenticated && !to.meta.public) {
    next('/login')
  } else if (auth.isAuthenticated && to.path === '/login') {
    next('/')
  } else {
    next()
  }
})

export default router
