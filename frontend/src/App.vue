<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider 
      v-model:collapsed="collapsed" 
      collapsible
      style="overflow: auto; height: 100vh; position: fixed; left: 0; top: 0; bottom: 0"
    >
      <div class="logo">
        <img src="@/assets/logo.png" alt="logo" style="height: 24px; margin-right: 8px" />
        <span v-if="!collapsed">115网盘转存分享</span>
      </div>
      <a-menu v-model:selectedKeys="selectedKeys" theme="dark" mode="inline">
        <a-menu-item key="dashboard" @click="currentView = 'dashboard'">
          <template #icon><DashboardOutlined /></template>
          <span>控制台</span>
        </a-menu-item>
        <a-menu-item key="logs" @click="currentView = 'logs'">
          <template #icon><ContainerOutlined /></template>
          <span>实时日志</span>
        </a-menu-item>
        <a-menu-item key="settings" @click="currentView = 'settings'">
          <template #icon><SettingOutlined /></template>
          <span>系统配置</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>
    <a-layout :style="{ marginLeft: collapsed ? '80px' : '200px', transition: 'all 0.2s' }">
      <a-layout-header style="background: #fff; padding: 0 24px; display: flex; align-items: center">
        <img src="@/assets/logo.png" alt="logo" style="height: 32px; margin-right: 12px" />
        <h2 style="margin: 0">115网盘转存分享-管理面板</h2>
      </a-layout-header>
      <a-layout-content style="margin: 16px">
        <div :style="{ padding: '24px', background: '#fff', minHeight: '360px' }">
          <Dashboard v-if="currentView === 'dashboard'" />
          <LogViewer v-if="currentView === 'logs'" />
          <Settings v-if="currentView === 'settings'" />
        </div>
      </a-layout-content>
      <a-layout-footer style="text-align: center">
        P115-Share ©2026 Created by Listening
      </a-layout-footer>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { 
  DashboardOutlined, 
  SettingOutlined, 
  ContainerOutlined 
} from '@ant-design/icons-vue';
import Dashboard from './views/Dashboard.vue';
import LogViewer from './views/LogViewer.vue';
import Settings from './views/Settings.vue';

const collapsed = ref<boolean>(false);
const selectedKeys = ref<string[]>(['dashboard']);
const currentView = ref<string>('dashboard');
</script>

<style scoped>
.logo {
  height: 32px;
  margin: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  line-height: 32px;
  font-weight: bold;
}
</style>
