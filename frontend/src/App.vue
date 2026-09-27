<template>
  <div class="app">
    <header class="header">
      <div class="brand" @click="$router.push('/')">
        <span class="logo">X</span><span class="word">Search</span>
      </div>
      <el-menu mode="horizontal" :default-active="$route.path" router :ellipsis="false" class="nav">
        <el-menu-item index="/">首页</el-menu-item>
        <el-menu-item index="/search">搜索</el-menu-item>
        <el-menu-item index="/news">新闻</el-menu-item>
        <el-menu-item index="/ai">AI 讯息</el-menu-item>
        <el-menu-item index="/rankings">抖音榜单</el-menu-item>
        <el-menu-item index="/stats">数据概览</el-menu-item>
        <el-menu-item v-if="isAdmin" index="/crawl">采集管理</el-menu-item>
        <el-menu-item v-if="isAdmin" index="/dbadmin">数据管理</el-menu-item>
      </el-menu>
      <el-button v-if="!isAdmin" class="login-btn" size="small" round
                 @click="loginVisible = true">登录</el-button>
      <el-button v-else class="login-btn" size="small" round plain type="danger"
                 @click="logout">退出登录</el-button>
    </header>

    <main class="main">
      <router-view />
    </main>

    <footer class="footer">
      Scrapy 2.13 + Playwright + Elasticsearch · 数据仅供学习交流
    </footer>

    <!-- 导航栏登录弹窗（管理员） -->
    <el-dialog v-model="loginVisible" title="管理员登录" width="360px"
               append-to-body :close-on-click-modal="false">
      <LoginForm @success="onLoginSuccess" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
// 登录入口在导航栏：未登录显示「登录」弹窗，登录后显示「退出登录」；
// 登录后「采集管理」才出现在菜单中，/login 为直达用的独立登录页
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { isAdmin, clearAuth } from './auth'
import { api } from './api'
import LoginForm from './components/LoginForm.vue'

const router = useRouter()
const loginVisible = ref(false)

function onLoginSuccess(): void {
  loginVisible.value = false
  router.push('/crawl')
}

// 退出登录：吊销后端 token、清除本地登录态，回到搜索页
async function logout(): Promise<void> {
  try { await api.adminLogout() } catch { /* token 已失效也照常退出 */ }
  clearAuth()
  ElMessage.success('已退出登录')
  router.push('/search')
}
</script>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 0 32px;
  border-bottom: 1px solid var(--el-border-color-light);
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand {
  font-size: 22px;
  font-weight: 700;
  cursor: pointer;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.logo { color: var(--el-color-primary); }
/* min-width:0 是必需的：flex 子项默认 min-width:auto，导航撑不住就会把整页
   顶出视口（实测 375px 下 documentElement 宽 636px，登录按钮被推到看不见的地方） */
.nav { flex: 1; min-width: 0; overflow-x: auto; border-bottom: none !important; }
.nav::-webkit-scrollbar { height: 0; }
.login-btn { flex-shrink: 0; }
.main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
.footer {
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 20px 0 28px;
}

/* 移动端：页头收紧到一屏内，导航改为在自身容器里横向滑动 */
@media (max-width: 768px) {
  .header { gap: 10px; padding: 0 12px; }
  .brand { font-size: 18px; }
  .nav :deep(.el-menu-item) { padding: 0 12px; font-size: 14px; }
  .main { padding: 16px 12px 40px; }
}

/* 窄到 480px 以下时"Search"这个词先让位，保证导航和登录按钮都在视口内 */
@media (max-width: 480px) {
  .brand .word { display: none; }
}
</style>
