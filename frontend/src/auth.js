// 管理员登录态：token 存 localStorage，路由守卫与采集管理接口鉴权均依赖它
import { ref, computed } from 'vue'

const TOKEN_KEY = 'xsearch_admin_token'
const NAME_KEY = 'xsearch_admin_name'

export const adminToken = ref(localStorage.getItem(TOKEN_KEY) || '')
export const adminName = ref(localStorage.getItem(NAME_KEY) || '')
export const isAdmin = computed(() => !!adminToken.value)

export function setAuth(token, name) {
  adminToken.value = token
  adminName.value = name || '管理员'
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(NAME_KEY, adminName.value)
}

export function clearAuth() {
  adminToken.value = ''
  adminName.value = ''
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(NAME_KEY)
}
