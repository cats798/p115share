<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-header">
        <img src="/logo.png" alt="Logo" class="login-logo" />
        <h1>P115-Share</h1>
        <p>欢迎使用 115 分享管理系统</p>
      </div>
      
      <a-form :model="form" @finish="onLogin" layout="vertical">
        <a-form-item label="用户名" name="username">
          <a-input v-model:value="form.username" placeholder="admin">
            <template #prefix><UserOutlined /></template>
          </a-input>
        </a-form-item>
        
        <a-form-item label="密码" name="password">
          <a-input-password v-model:value="form.password" placeholder="admin">
            <template #prefix><LockOutlined /></template>
          </a-input-password>
        </a-form-item>
        
        <a-form-item>
          <a-button type="primary" html-type="submit" :loading="loading" block class="submit-btn">
            登录
          </a-button>
        </a-form-item>
      </a-form>
      
      <div class="login-footer">
        默认账号密码均为 <span>admin</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { UserOutlined, LockOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '../stores/auth'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)

const form = reactive({
  username: '',
  password: ''
})

const onLogin = async () => {
  if (!form.username || !form.password) {
    return message.warning('请输入用户名和密码')
  }
  
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    message.success('登录成功')
    router.push('/')
  } catch (error) {
    message.error('登录失败：用户名或密码错误')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: linear-gradient(135deg, #1a1a1a 0%, #0d0d0d 100%);
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 40px;
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(10px);
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-logo {
  width: 64px;
  height: 64px;
  margin-bottom: 16px;
}

h1 {
  color: #fff;
  font-size: 24px;
  margin-bottom: 8px;
  font-weight: 600;
}

p {
  color: rgba(255, 255, 255, 0.5);
  font-size: 14px;
}

.submit-btn {
  height: 48px;
  font-size: 16px;
  border-radius: 8px;
  margin-top: 8px;
}

.login-footer {
  text-align: center;
  margin-top: 24px;
  color: rgba(255, 255, 255, 0.3);
  font-size: 12px;
}

.login-footer span {
  color: #177ddc;
}

:deep(.ant-input-affix-wrapper) {
  background: rgba(255, 255, 255, 0.05);
  border-color: rgba(255, 255, 255, 0.1);
  color: #fff;
}

:deep(.ant-input) {
  background: transparent;
  color: #fff;
}

:deep(.ant-input-password-icon) {
  color: rgba(255, 255, 255, 0.3);
}

:deep(.ant-form-item-label > label) {
  color: rgba(255, 255, 255, 0.8);
}
</style>
