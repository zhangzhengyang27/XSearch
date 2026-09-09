<template>
  <el-form @submit.prevent="submit">
    <el-form-item>
      <el-input v-model="username" placeholder="管理员账号" size="large"
                :prefix-icon="User" autocomplete="username" clearable />
    </el-form-item>
    <el-form-item>
      <el-input v-model="password" type="password" placeholder="密码" size="large"
                :prefix-icon="Lock" show-password autocomplete="current-password"
                @keyup.enter="submit" />
    </el-form-item>
    <el-button type="primary" size="large" class="submit"
               :loading="loading" @click="submit">登 录</el-button>
  </el-form>
</template>

<script setup lang="ts">
// 管理员登录表单：导航栏登录弹窗与 /login 登录页共用
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { api, errText } from '../api'
import { setAuth } from '../auth'

const emit = defineEmits<{ success: [] }>()
const username = ref('')
const password = ref('')
const loading = ref(false)

async function submit(): Promise<void> {
  if (!username.value.trim() || !password.value) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const r = await api.adminLogin(username.value.trim(), password.value)
    setAuth(r.token, r.username)
    ElMessage.success('登录成功')
    emit('success')
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.submit { width: 100%; }
</style>
