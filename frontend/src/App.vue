<template>
  <a-config-provider 
    :locale="zhCN"
    :theme="{
      algorithm: themeStore.mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
    }"
  >
    <router-view />
  </a-config-provider>
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import zhCN from 'ant-design-vue/es/locale/zh_CN';
import { theme } from 'ant-design-vue';
import { useThemeStore } from './stores/theme';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';

const themeStore = useThemeStore();

onMounted(() => {
  themeStore.initTheme();
});

dayjs.locale('zh-cn');
</script>

<style>
#app {
  height: 100vh;
}
</style>
