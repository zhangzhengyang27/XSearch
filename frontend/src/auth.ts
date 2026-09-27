// 管理员登录态：token 存 localStorage，路由守卫与采集管理接口鉴权均依赖它
import { ref, computed } from 'vue'

const TOKEN_KEY = 'xsearch_admin_token'
const NAME_KEY = 'xsearch_admin_name'

export const adminToken = ref(localStorage.getItem(TOKEN_KEY) || '')
export const adminName = ref(localStorage.getItem(NAME_KEY) || '')
export const isAdmin = computed(() => !!adminToken.value)

// 仅管理员可访问的路由：路由守卫与「401 是否跳登录页」共用这一份定义，
// 避免公开页（/ai /news /rankings）把匿名访客甩到他看不懂的管理登录页。
export const ADMIN_PATHS = ['/crawl', '/dbadmin']
export const isAdminPath = (path: string): boolean => ADMIN_PATHS.includes(path)

export function setAuth(token: string, name?: string): void {
  adminToken.value = token
  adminName.value = name || '管理员'
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(NAME_KEY, adminName.value)
}

export function clearAuth(): void {
  adminToken.value = ''
  adminName.value = ''
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(NAME_KEY)
}
