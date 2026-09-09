<template>
  <div class="login-wrap">
    <el-card shadow="never" class="login-card">
      <div class="brand"><span class="logo">X</span>Search</div>
      <div class="sub">采集管理 · 管理员登录</div>
      <LoginForm @success="onSuccess" />
    </el-card>
  </div>
</template>

<script setup>
// 直达地址 /login（或 /crawl 未登录被守卫重定向）时的独立登录页
import { useRoute, useRouter } from 'vue-router'
import LoginForm from '../components/LoginForm.vue'

const route = useRoute()
const router = useRouter()

function onSuccess() {
  router.push(typeof route.query.next === 'string' ? route.query.next : '/crawl')
}
</script>

<style scoped>
.login-wrap {
  min-height: 60vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-card {
  width: 360px;
  padding: 8px 12px 4px;
}
.brand {
  font-size: 26px;
  font-weight: 700;
  text-align: center;
  color: var(--el-text-color-primary);
}
.logo { color: var(--el-color-primary); }
.sub {
  text-align: center;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 6px 0 22px;
}
</style>
